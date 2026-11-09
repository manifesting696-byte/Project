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
        if kind == "task":
            origin = self.stores.get("requests", item["request_id"])["session_id"]
        else:
            origin = item["session_id"]
        if origin != session_id:
            raise PermissionError("引用不属于当前会话")
        output = ref.get("output")
        if kind == "request" and output:
            if output not in {value["name"] for value in item["spec"]["outputs"]}:
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
                    raise ValueError(
                        "新请求应使用本轮局部 ID，已有工作请使用 update_task"
                    )
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
                        previous = self.outputs(
                            self.stores.get("requests", ref.id)
                        ).get(ref.output)
                        if not previous:
                            raise ValueError("目标需求尚无可复用的该项结果")
                        ref.kind, ref.id = previous["kind"], previous["id"]
                        ref.output = previous.get("output")
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
    def order_operations(operations, specs=None):
        by_id = {item.request_id: item for item in operations}
        ordered, visiting, done = [], set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("本轮 Request 依赖存在循环")
            if key in done:
                return
            visiting.add(key)
            refs = (
                [binding.source for binding in specs[key].inputs]
                if specs is not None and key in specs
                else by_id[key].refs
            )
            for ref in refs:
                if ref.kind == "request" and ref.id in by_id and ref.id != key:
                    visit(ref.id)
            visiting.remove(key)
            done.add(key)
            ordered.append(by_id[key])

        for operation in operations:
            visit(operation.request_id)
        return ordered

    def validate_specs(self, session_id, specs, targets=None):
        graph = {
            item["request_id"]: item["spec"]
            for item in self.stores.list("requests", "session_id", session_id)
        }
        graph.update(
            {key: spec.model_dump(exclude_none=True) for key, spec in specs.items()}
        )
        for key in targets if targets is not None else specs:
            spec = specs[key]
            for binding in spec.inputs:
                source = binding.source
                if source.kind not in ("request", "artifact"):
                    raise ValueError("需求输入使用 Request 命名交付或具体 Artifact")
                if source.kind == "request":
                    if source.id == key:
                        raise ValueError(
                            "历史输入应使用具体 Artifact，需求不能依赖自身交付"
                        )
                    if source.id not in graph:
                        raise ValueError("输入引用的 Request 不存在于当前会话")
                    names = [item["name"] for item in graph[source.id]["outputs"]]
                    if source.output is None and len(names) == 1:
                        source.output = names[0]
                    if source.output not in names:
                        raise ValueError("输入引用的上游交付名称未声明")
                else:
                    self.validate_ref(session_id, source.model_dump(exclude_none=True))
            graph[key] = spec.model_dump(exclude_none=True)
        visiting, done = set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("Request 输入依赖存在循环")
            if key in done:
                return
            visiting.add(key)
            for binding in graph[key]["inputs"]:
                source = binding["source"]
                if source["kind"] == "request" and source["id"] in graph:
                    visit(source["id"])
            visiting.remove(key)
            done.add(key)

        for key in targets if targets is not None else specs:
            visit(key)

    def plan_bindings(self, request):
        events = self.stores.list("session_events", "session_id", request["session_id"])
        revision = self.stores.get("request_revisions", request["current_revision_id"])
        names = {item["name"] for item in request["spec"]["outputs"]}
        while names:
            for event in reversed(events):
                if (
                    event["event_type"] == "plan_updated"
                    and event.get("request_id") == request["request_id"]
                    and event["payload"].get("revision_id") == revision["revision_id"]
                ):
                    return {
                        name: ref
                        for name, ref in event["payload"]["plan"][
                            "output_bindings"
                        ].items()
                        if name in names
                    }
            if not revision.get("parent_revision_id"):
                break
            previous = self.stores.get(
                "request_revisions", revision["parent_revision_id"]
            )
            old = {item["name"]: item for item in previous["spec"]["outputs"]}
            current = {item["name"]: item for item in revision["spec"]["outputs"]}
            changed = {name for name, item in old.items() if current.get(name) != item}
            if (
                previous["spec"]["description"] != revision["spec"]["description"]
                or previous["spec"]["parameters"] != revision["spec"]["parameters"]
                or (
                    previous["spec"]["inputs"] != revision["spec"]["inputs"]
                    and not self.presentation_only_inputs(
                        request, previous["spec"], revision["spec"], changed
                    )
                )
            ):
                break
            names &= {name for name, item in current.items() if old.get(name) == item}
            revision = previous
        return {}

    def resolve(self, session_id, ref, visiting=None):
        self.validate_ref(session_id, ref)
        identity = (ref["kind"], ref["id"], ref.get("output"), ref.get("block_id"))
        visiting = set(visiting or ())
        if identity in visiting:
            raise ValueError("交付引用存在循环")
        visiting.add(identity)
        kind, key = ref["kind"], ref["id"]
        if kind == "artifact":
            return {"artifact_id": key}
        if kind == "event":
            event = deepcopy(self.stores.get("session_events", key))
            if ref.get("block_id"):
                event["payload"]["blocks"] = [
                    block
                    for block in event["payload"]["blocks"]
                    if block["block_id"] == ref["block_id"]
                ]
            return {"event": event}
        if kind == "request":
            request = self.stores.get("requests", key)
            declared = [item["name"] for item in request["spec"]["outputs"]]
            name = ref.get("output")
            if name is None:
                if len(declared) != 1:
                    raise ValueError("多个交付需要明确 output 名称")
                name = declared[0]
            bindings = self.plan_bindings(request)
            if name not in bindings:
                raise ValueError("上游 Request 的交付尚未完成")
            return self.resolve_delivery(request, bindings[name], visiting)
        task = self.stores.get("tasks", key)
        if task["status"] != "completed":
            raise ValueError("上游执行 Task 尚未完成")
        if task.get("semantic_binding") and ref.get("output") in (
            None,
            "semantic_binding",
        ):
            return {"semantic_binding": task["semantic_binding"]}
        outputs = task["produced_outputs"]
        name = ref.get("output")
        if name is None:
            if len(outputs) != 1:
                raise ValueError("多个上游输出必须指定 output")
            name = next(iter(outputs))
        if name not in outputs:
            raise ValueError("上游未生成声明的输出")
        return self.resolve(session_id, outputs[name], visiting)

    def resolve_delivery(self, request, ref, visiting=None):
        value = self.resolve(request["session_id"], ref, visiting)
        if (
            value.get("event")
            and value["event"].get("request_id") == request["request_id"]
            and request["status"] != "completed"
        ):
            raise ValueError("该回答交付需要依据当前输入重新生成")
        if value.get("artifact_id"):
            artifact = self.stores.get("artifacts", value["artifact_id"])
            provenance = artifact["payload"].get("provenance", {})
            if (
                not artifact.get("task_id")
                and artifact.get("request_id") == request["request_id"]
                and "input_artifact_ids" in provenance
            ):
                current_inputs = []
                for binding in request["spec"]["inputs"]:
                    source = self.resolve(
                        request["session_id"], binding["source"], visiting
                    )
                    if source.get("artifact_id"):
                        current_inputs.append(source["artifact_id"])
                if sorted(set(current_inputs)) != provenance["input_artifact_ids"]:
                    raise ValueError("该文字交付需要依据当前输入重新生成")
            if self.stores.exists("tasks", artifact.get("task_id")):
                producer = self.stores.get("tasks", artifact.get("task_id"))
                if producer["status"] != "completed":
                    explicit = self.artifact_scope(
                        [
                            binding["source"]
                            for binding in request["spec"]["inputs"]
                            if binding["source"]["kind"] in ("artifact", "event")
                        ],
                        follow_requests=False,
                    )
                    if artifact["artifact_id"] not in explicit:
                        raise ValueError("该交付的执行结果已经失效")
        return value

    def outputs(self, request):
        resolved = {}
        specs = {item["name"]: item for item in request["spec"]["outputs"]}
        for name, ref in self.plan_bindings(request).items():
            try:
                item = self.resolve_delivery(request, ref)
            except ValueError:
                continue
            key = item.get("artifact_id")
            if (
                key
                and self.stores.get("artifacts", key)["content_type"]
                == specs[name]["content_type"]
            ):
                resolved[name] = {"kind": "artifact", "id": key}
        return resolved

    def artifact_scope(self, refs, follow_requests=True):
        allowed, pending, visited = set(), list(refs), set()
        while pending:
            ref = pending.pop()
            identity = (ref["kind"], ref["id"], ref.get("output"), ref.get("block_id"))
            if identity in visited:
                continue
            visited.add(identity)
            if ref["kind"] == "request" and follow_requests:
                request = self.stores.get("requests", ref["id"])
                values = self.outputs(request)
                if ref.get("output"):
                    pending.extend(
                        [values[ref["output"]]] if ref["output"] in values else []
                    )
                else:
                    pending.extend(values.values())
            elif ref["kind"] == "task":
                task = self.stores.get("tasks", ref["id"])
                pending.extend(task["produced_outputs"].values())
            elif ref["kind"] == "event":
                event = self.stores.get("session_events", ref["id"])
                for block in event["payload"].get("blocks", []):
                    if not ref.get("block_id") or block["block_id"] == ref["block_id"]:
                        pending.extend(
                            [{"kind": "artifact", "id": block["artifact_id"]}]
                        )
            elif ref["kind"] == "artifact" and ref["id"] not in allowed:
                allowed.add(ref["id"])
                pending.extend(
                    {"kind": "artifact", "id": key}
                    for key in self.stores.get("artifacts", ref["id"])[
                        "source_artifact_ids"
                    ]
                )
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
            spec=spec,
            created_at=old["created_at"] if old else now(),
            updated_at=now(),
        ).model_dump(exclude_none=True)
        changes = []
        if old:
            changes = self.invalidate(old, previous["spec"], spec_data)
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
        owners = {
            item["request_id"]: item
            for item in self.stores.list(
                "requests", "session_id", request["session_id"]
            )
        }
        old_outputs = {item["name"]: item for item in previous["outputs"]}
        new_outputs = {item["name"]: item for item in current["outputs"]}
        changed_outputs = {
            name for name, item in old_outputs.items() if new_outputs.get(name) != item
        }
        changed_data = (
            previous["description"] != current["description"]
            or previous["parameters"] != current["parameters"]
            or (
                previous["inputs"] != current["inputs"]
                and not self.presentation_only_inputs(
                    request, previous, current, changed_outputs
                )
            )
        )
        bindings = self.plan_bindings(request)
        sources = {
            bindings[name]["id"]
            for name in changed_outputs
            if name in bindings and bindings[name]["kind"] == "task"
        }
        affected = {
            node["task_id"]
            for node in nodes
            if node["request_id"] == request["request_id"]
            and node["status"] not in ("superseded", "cancelled")
            and (
                changed_data
                or node["task_id"] in sources
                or (
                    changed_outputs
                    and not sources
                    and node["assigned_agent"] == "PresentationAgent"
                )
            )
        }
        changed_deliveries = {
            request["request_id"]: set(old_outputs) if changed_data else changed_outputs
        }
        fixed_inputs = {
            key: self.artifact_scope(
                [
                    binding["source"]
                    for binding in owner["spec"]["inputs"]
                    if binding["source"]["kind"] in ("artifact", "event")
                ],
                follow_requests=False,
            )
            for key, owner in owners.items()
        }
        while True:
            previous_affected = set(affected)
            previous_owners = set(changed_deliveries)
            for key, owner in owners.items():
                if key in changed_deliveries:
                    continue
                if any(
                    binding["source"]["kind"] == "request"
                    and changed_deliveries.get(binding["source"]["id"])
                    and (
                        binding["source"].get("output") is None
                        or binding["source"]["output"]
                        in changed_deliveries[binding["source"]["id"]]
                    )
                    for binding in owner["spec"]["inputs"]
                ):
                    changed_deliveries[key] = {
                        item["name"] for item in owner["spec"]["outputs"]
                    }
                    affected.update(
                        node["task_id"]
                        for node in nodes
                        if node["request_id"] == key
                        and node["status"] not in ("superseded", "cancelled")
                    )
            artifacts = {
                ref["id"]
                for node in nodes
                if node["task_id"] in affected
                for ref in node["produced_outputs"].values()
                if ref["kind"] == "artifact"
            }
            affected.update(
                node["task_id"]
                for node in nodes
                if node["status"] not in ("superseded", "cancelled")
                and (
                    set(node["depends_on"]) & affected
                    or any(
                        binding["source"]["kind"] == "artifact"
                        and binding["source"]["id"] in artifacts
                        and binding["source"]["id"]
                        not in fixed_inputs[node["request_id"]]
                        for binding in node["input_bindings"]
                    )
                )
            )
            for node in nodes:
                key = node["request_id"]
                if node["task_id"] in affected and key not in changed_deliveries:
                    changed_deliveries[key] = {
                        item["name"] for item in owners[key]["spec"]["outputs"]
                    }
            if (
                affected == previous_affected
                and set(changed_deliveries) == previous_owners
            ):
                break
        changes = []
        for node in nodes:
            if node["task_id"] in affected:
                updated = deepcopy(node)
                updated["status"] = "superseded"
                changes.append(("tasks", updated))
        for key in changed_deliveries.keys() - {request["request_id"]}:
            dependent = owners[key]
            dependent["status"], dependent["updated_at"] = "pending", now()
            changes.append(("requests", dependent))
        return changes

    def presentation_only_inputs(self, request, previous, current, changed_outputs):
        old = {item["slot"]: item for item in previous["inputs"]}
        new = {item["slot"]: item for item in current["inputs"]}
        outputs = {item["name"]: item for item in current["outputs"]}
        if not changed_outputs or any(
            outputs.get(name, {}).get("content_type") not in ("chart", "report")
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
                "content_type"
            ] not in ("chart", "report"):
                return False
        return True
