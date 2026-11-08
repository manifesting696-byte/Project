"""Persistent request identity, immutable specifications and reference validation."""

from copy import deepcopy

from ..core.models import Request, RequestRevision
from ..persistence.stores import now


class Requests:
    def __init__(self, stores):
        self.stores = stores

    def validate_ref(self, session_id, ref, pending=None):
        kind, key = ref["kind"], ref["id"]
        if kind == "request" and key in (pending or set()):
            return
        table = {
            "request": "requests",
            "task": "tasks",
            "artifact": "artifacts",
            "event": "session_events",
        }[kind]
        item = self.stores.get(table, key)
        if kind in ("task", "artifact"):
            origin = self.stores.get("requests", item["request_id"])["session_id"]
        else:
            origin = item["session_id"]
        if origin != session_id:
            raise PermissionError("引用不属于当前会话")
        output = ref.get("output")
        if kind == "request" and output:
            revision = self.stores.get("request_revisions", item["current_revision_id"])
            if output not in {value["name"] for value in revision["spec"]["outputs"]}:
                raise ValueError("Request 未声明该交付名称")
        if kind == "task" and output:
            semantic = (
                item["assigned_agent"] == "SemanticAgent"
                and output == "semantic_binding"
            )
            if output not in item["expected_outputs"] and not semantic:
                raise ValueError("执行 Task 未声明该输出")
        if kind == "event" and ref.get("block_id"):
            if not any(
                block["block_id"] == ref["block_id"]
                for block in item["payload"].get("blocks", [])
            ):
                raise ValueError("历史回答块不存在")

    def normalize(self, session_id, intent):
        aliases = {}
        for operation in intent.operations:
            if operation.action == "update_task":
                request = self.stores.get("requests", operation.request_id)
                if (
                    request["session_id"] != session_id
                    or request["status"] == "archived"
                ):
                    raise PermissionError("更新目标不属于当前可用需求")
            else:
                if self.stores.exists("requests", operation.request_id):
                    raise ValueError("新请求应使用本轮局部 ID，已有工作请使用 update_task")
                aliases[operation.request_id] = self.stores.new_id("req")
        for operation in intent.operations:
            operation.request_id = aliases.get(
                operation.request_id, operation.request_id
            )
            for ref in operation.refs:
                if ref.kind == "request":
                    ref.id = aliases.get(ref.id, ref.id)
                    # Freeze a reference to the target's old output before replacing its revision.
                    if (
                        ref.id == operation.request_id
                        and operation.action == "update_task"
                        and ref.output
                    ):
                        previous = self.stores.get("requests", ref.id)[
                            "output_refs"
                        ].get(ref.output)
                        if not previous:
                            raise ValueError("目标需求尚无可复用的该项结果")
                        ref.kind, ref.id = previous["kind"], previous["id"]
                        ref.output, ref.block_id = (
                            previous.get("output"),
                            previous.get("block_id"),
                        )
        for item in intent.clarifications:
            item.request_id = aliases.get(item.request_id, item.request_id)
            for ref in item.refs:
                if ref.kind == "request":
                    ref.id = aliases.get(ref.id, ref.id)
        pending = {
            operation.request_id
            for operation in intent.operations
            if operation.action != "update_task"
        }
        for operation in intent.operations:
            for ref in operation.refs:
                self.validate_ref(
                    session_id, ref.model_dump(exclude_none=True), pending
                )
        for item in intent.clarifications:
            if item.request_id:
                self.validate_ref(
                    session_id, {"kind": "request", "id": item.request_id}, pending
                )
            for ref in item.refs:
                self.validate_ref(
                    session_id, ref.model_dump(exclude_none=True), pending
                )
        return self.order_operations(intent.operations)

    @staticmethod
    def order_operations(operations):
        by_id = {item.request_id: item for item in operations}
        ordered, visiting, done = [], set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("本轮 Request 依赖存在循环")
            if key in done:
                return
            visiting.add(key)
            for ref in by_id[key].refs:
                if ref.kind == "request" and ref.id in by_id and ref.id != key:
                    visit(ref.id)
            visiting.remove(key)
            done.add(key)
            ordered.append(by_id[key])

        for operation in operations:
            visit(operation.request_id)
        return ordered

    def plan_bindings(self, request):
        events = self.stores.list("session_events", "session_id", request["session_id"])
        for event in reversed(events):
            if (
                event["event_type"] == "plan_updated"
                and event.get("request_id") == request["request_id"]
                and event["payload"].get("revision_id")
                == request["current_revision_id"]
            ):
                return event["payload"]["plan"]["output_bindings"]
        return dict(request["output_refs"])

    def artifact_scope(self, refs):
        allowed, pending, visited = set(), list(refs), set()
        while pending:
            ref = pending.pop()
            identity = (ref["kind"], ref["id"], ref.get("output"), ref.get("block_id"))
            if identity in visited:
                continue
            visited.add(identity)
            if ref["kind"] == "request":
                request = self.stores.get("requests", ref["id"])
                values = request["output_refs"]
                pending.extend(
                    [values[ref["output"]]]
                    if ref.get("output") and ref["output"] in values
                    else values.values()
                )
            elif ref["kind"] == "task":
                task = self.stores.get("tasks", ref["id"])
                pending.extend(task["produced_outputs"].values())
            elif ref["kind"] == "event":
                event = self.stores.get("session_events", ref["id"])
                for block in event["payload"].get("blocks", []):
                    if not ref.get("block_id") or block["block_id"] == ref["block_id"]:
                        pending.extend(block.get("refs", []))
            elif ref["kind"] == "artifact" and ref["id"] not in allowed:
                allowed.add(ref["id"])
                pending.extend(self.stores.get("artifacts", ref["id"])["source_refs"])
        return allowed

    def revise(self, session_id, turn_id, operation, spec, event_id):
        request_id = operation.request_id
        old = (
            self.stores.get("requests", request_id)
            if operation.action == "update_task"
            else None
        )
        previous = (
            self.stores.get("request_revisions", old["current_revision_id"])
            if old
            else None
        )
        spec_data = spec.model_dump(exclude_none=True)
        for binding in spec_data["inputs"]:
            self.validate_ref(session_id, binding["source"])
            if (
                binding["source"]["kind"] == "request"
                and binding["source"]["id"] == request_id
            ):
                raise ValueError("复用目标需求的旧结果时，请绑定具体历史 Artifact")
        if previous and previous["spec"] == spec_data:
            return old, previous
        revision = RequestRevision(
            revision_id=self.stores.new_id("rev"),
            request_id=request_id,
            parent_revision_id=previous["revision_id"] if previous else None,
            source_intent_event_id=event_id,
            spec=spec,
            created_at=now(),
        ).model_dump(exclude_none=True)
        request = Request(
            request_id=request_id,
            session_id=session_id,
            title=spec.description[:80],
            created_by_turn_id=old["created_by_turn_id"] if old else turn_id,
            current_revision_id=revision["revision_id"],
            plan_version=old["plan_version"] if old else 0,
            status="pending",
            created_at=old["created_at"] if old else now(),
            updated_at=now(),
        ).model_dump(exclude_none=True)
        changes = []
        if old:
            changes, retained = self.invalidate(old, previous["spec"], spec_data)
            request["output_refs"] = retained
        expected = {request_id: previous["revision_id"] if previous else None}
        expected.update(
            {
                item["request_id"]: item["current_revision_id"]
                for table, item in changes
                if table == "requests"
            }
        )
        self.stores.save_many(
            [*changes, ("request_revisions", revision), ("requests", request)],
            expected_revisions=expected,
            event={
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": "request_revised",
                "actor": "Supervisor",
                "request_id": request_id,
                "payload": {"revision_id": revision["revision_id"]},
            },
        )
        return request, revision

    def invalidate(self, request, previous, current):
        nodes = self.stores.session_tasks(request["session_id"])
        old_outputs = {item["name"]: item for item in previous["outputs"]}
        new_outputs = {item["name"]: item for item in current["outputs"]}
        changed_outputs = {
            name for name, item in old_outputs.items() if new_outputs.get(name) != item
        }
        presentation_inputs = self.presentation_only_inputs(
            request, previous, current, changed_outputs
        )
        changed_data = (
            previous["description"] != current["description"]
            or previous["parameters"] != current["parameters"]
            or (previous["inputs"] != current["inputs"] and not presentation_inputs)
        )
        bindings = self.plan_bindings(request)
        affected = set()
        for node in nodes:
            if node["request_id"] != request["request_id"] or node["status"] in (
                "superseded",
                "cancelled",
            ):
                continue
            if changed_data:
                affected.add(node["task_id"])
            elif changed_outputs:
                sources = {
                    bindings[name]["id"]
                    for name in changed_outputs
                    if name in bindings and bindings[name]["kind"] == "task"
                }
                if node["task_id"] in sources or (
                    not sources and node["assigned_agent"] == "PresentationAgent"
                ):
                    affected.add(node["task_id"])
        while True:
            artifacts = {
                ref["id"]
                for node in nodes
                if node["task_id"] in affected
                for ref in node["produced_outputs"].values()
                if ref["kind"] == "artifact"
            }
            expanded = affected | {
                node["task_id"]
                for node in nodes
                if node["status"] not in ("superseded", "cancelled")
                and (
                    set(node["depends_on"]) & affected
                    or any(
                        binding["source"]["kind"] == "artifact"
                        and binding["source"]["id"] in artifacts
                        for binding in node["input_bindings"]
                    )
                )
            }
            if expanded == affected:
                break
            affected = expanded
        changes = []
        affected_artifacts = {
            ref["id"]
            for node in nodes
            if node["task_id"] in affected
            for ref in node["produced_outputs"].values()
            if ref["kind"] == "artifact"
        }
        for node in nodes:
            if node["task_id"] in affected:
                updated = deepcopy(node)
                updated["status"] = "superseded"
                changes.append(("tasks", updated))
        for owner in {
            node["request_id"] for node in nodes if node["task_id"] in affected
        } - {request["request_id"]}:
            dependent = self.stores.get("requests", owner)
            dependent["status"], dependent["updated_at"] = "pending", now()
            dependent["output_refs"] = {
                name: ref
                for name, ref in dependent["output_refs"].items()
                if ref["id"] not in affected_artifacts and ref["id"] not in affected
            }
            changes.append(("requests", dependent))
        retained = (
            {}
            if changed_data
            else {
                name: ref
                for name, ref in request["output_refs"].items()
                if name in new_outputs
                and name not in changed_outputs
                and ref["id"] not in affected_artifacts
                and ref["id"] not in affected
            }
        )
        return changes, retained

    def presentation_only_inputs(self, request, previous, current, changed_outputs):
        old = {item["slot"]: item for item in previous["inputs"]}
        new = {item["slot"]: item for item in current["inputs"]}
        outputs = {item["name"]: item for item in current["outputs"]}
        if not changed_outputs or any(
            outputs.get(name, {}).get("kind") not in ("chart", "report")
            for name in changed_outputs
        ):
            return False
        if any(new.get(slot) != value for slot, value in old.items()):
            return False
        for slot in new.keys() - old.keys():
            ref = new[slot]["source"]
            if ref["kind"] != "artifact":
                return False
            artifact = self.stores.get("artifacts", ref["id"])
            if artifact["request_id"] != request["request_id"] or artifact[
                "type"
            ] not in ("chart", "report"):
                return False
        return True
