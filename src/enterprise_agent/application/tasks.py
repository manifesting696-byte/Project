"""Validate execution DAGs, bind logical outputs and resolve exact inputs."""

from copy import deepcopy

from ..core.config import MAX_TASKS
from ..core.models import Plan, PlannedTask
from ..persistence.stores import now


class Tasks:
    def __init__(self, stores, requests):
        self.stores, self.requests = stores, requests

    def resolve(self, session_id, ref):
        self.requests.validate_ref(session_id, ref)
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
            outputs = request["output_refs"]
            name = ref.get("output")
            if name is None:
                if len(outputs) != 1:
                    raise ValueError("Request 有多个或尚无结果，请指定实际交付名称")
                name = next(iter(outputs))
            if name not in outputs:
                raise ValueError("上游 Request 的交付尚未完成")
            return self.resolve(session_id, outputs[name])
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
        return self.resolve(session_id, outputs[name])

    def allowed_artifacts(self, request):
        revision = self.stores.get("request_revisions", request["current_revision_id"])
        return self.requests.artifact_scope(
            [binding["source"] for binding in revision["spec"]["inputs"]]
        )

    def validate_artifact_input(self, request, artifact_id, explicit):
        artifact = self.stores.get("artifacts", artifact_id)
        self.requests.validate_ref(
            request["session_id"], {"kind": "artifact", "id": artifact_id}
        )
        if self.stores.exists("tasks", artifact["task_id"]):
            producer = self.stores.get("tasks", artifact["task_id"])
            if producer["status"] == "completed":
                return
        if artifact_id not in explicit:
            raise ValueError("失效执行的产物需要在当前需求输入中明确指定后才能复用")

    def apply_plan(self, request, turn_id, plan: Plan):
        nodes = self.stores.session_tasks(request["session_id"])
        existing = {node["task_id"]: node for node in nodes}
        revision = self.stores.get("request_revisions", request["current_revision_id"])
        output_names = {item["name"] for item in revision["spec"]["outputs"]}
        cancelled = set(plan.cancel_task_ids)
        for key in cancelled:
            if (
                key not in existing
                or existing[key]["request_id"] != request["request_id"]
                or existing[key]["status"] != "pending"
            ):
                raise ValueError("仅能撤销当前 Request 尚未执行的节点")
        local_ids = [item.task_id for item in plan.tasks]
        if len(local_ids) != len(set(local_ids)) or set(local_ids) & existing.keys():
            raise ValueError("新增执行 Task 的局部 ID 必须唯一")
        active = [
            node
            for node in nodes
            if node["request_id"] == request["request_id"]
            and node["status"] not in ("cancelled", "superseded")
        ]
        if len(active) + len(local_ids) - len(cancelled) > MAX_TASKS:
            raise ValueError("执行节点超过本次预算")
        aliases = {key: self.stores.new_id("task") for key in local_ids}
        additions = []
        for proposed in plan.tasks:
            node = proposed.model_dump(exclude_none=True)
            if len(node["expected_outputs"]) != len(set(node["expected_outputs"])):
                raise ValueError("执行输出名称重复")
            if node["assigned_agent"] == "SemanticAgent" and node["expected_outputs"]:
                raise ValueError("语义绑定通过 semantic_binding 传递，不声明 Artifact")
            node["task_id"] = aliases[proposed.task_id]
            node["depends_on"] = [aliases.get(key, key) for key in node["depends_on"]]
            for binding in node["input_bindings"]:
                ref = binding["source"]
                if ref["kind"] == "task":
                    ref["id"] = aliases.get(ref["id"], ref["id"])
                elif ref["kind"] == "request":
                    if ref["id"] == request["request_id"]:
                        raise ValueError(
                            "执行节点已获得当前 spec，不能把本 Request 尚未生成的交付作为输入。"
                            "请用 kind=task、上游 task_id 和 output 绑定 DAG 内的结果，旧数据用真实 Artifact 引用"
                        )
                    source = self.stores.get("requests", ref["id"])
                    self.requests.validate_ref(request["session_id"], ref)
                    name = ref.get("output")
                    if name is None and len(source["output_refs"]) == 1:
                        name = next(iter(source["output_refs"]))
                    if name not in source["output_refs"]:
                        raise ValueError("上游 Request 的交付尚未完成")
                    binding["source"] = deepcopy(source["output_refs"][name])
                    if (
                        binding["source"]["kind"] == "task"
                        and binding["source"]["id"] not in node["depends_on"]
                    ):
                        node["depends_on"].append(binding["source"]["id"])
            node.update(
                request_id=request["request_id"],
                revision_id=request["current_revision_id"],
                plan_version=request["plan_version"] + 1,
                created_by_turn_id=turn_id,
                status="pending",
                produced_outputs={},
                created_at=now(),
            )
            additions.append(node)
        combined = {**existing, **{node["task_id"]: node for node in additions}}
        explicit = self.allowed_artifacts(request)
        pending = [
            node
            for node in nodes
            if node["request_id"] == request["request_id"]
            and node["status"] == "pending"
            and node["task_id"] not in cancelled
        ]
        for node in additions + pending:
            for dep in node["depends_on"]:
                if (
                    dep not in combined
                    or dep in cancelled
                    or combined[dep]["status"] in ("superseded", "cancelled")
                ):
                    raise ValueError("执行依赖不存在或已经失效")
            for binding in node["input_bindings"]:
                ref = binding["source"]
                if ref["kind"] == "task":
                    source = combined.get(ref["id"])
                    if not source or ref["id"] not in node["depends_on"]:
                        raise ValueError("上游 Task 输出必须具有显式 depends_on")
                    semantic = (
                        source["assigned_agent"] == "SemanticAgent"
                        and ref.get("output") == "semantic_binding"
                    )
                    if (
                        ref.get("output")
                        and ref["output"] not in source["expected_outputs"]
                        and not semantic
                    ):
                        raise ValueError("上游未声明该命名输出")
                else:
                    self.requests.validate_ref(request["session_id"], ref)
                    if ref["kind"] == "artifact":
                        self.validate_artifact_input(request, ref["id"], explicit)
                    elif ref["kind"] == "request":
                        # Request dependencies are materialized before a consumer plan executes.
                        value = self.resolve(request["session_id"], ref)
                        if value.get("artifact_id"):
                            self.validate_artifact_input(
                                request, value["artifact_id"], explicit
                            )
        visiting, done = set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("执行 Task DAG 存在循环")
            if key in done:
                return
            visiting.add(key)
            for dep in combined[key]["depends_on"]:
                visit(dep)
            visiting.remove(key)
            done.add(key)

        for node in additions:
            visit(node["task_id"])
        bindings = {
            name: ref
            for name, ref in self.requests.plan_bindings(request).items()
            if name in output_names and ref["id"] not in cancelled
        }
        for name, value in plan.output_bindings.items():
            if name not in output_names:
                raise ValueError("交付绑定名称不属于当前 Request 的 outputs")
            ref = value.model_dump(exclude_none=True)
            if ref["kind"] == "task":
                ref["id"] = aliases.get(ref["id"], ref["id"])
                source = combined.get(ref["id"])
                if not source or source["status"] in ("cancelled", "superseded"):
                    raise ValueError("交付绑定指向失效执行")
                semantic = (
                    source["assigned_agent"] == "SemanticAgent"
                    and ref.get("output") == "semantic_binding"
                )
                if ref.get("output") not in source["expected_outputs"] and not semantic:
                    raise ValueError("交付绑定必须明确实际输出名称")
            else:
                self.requests.validate_ref(request["session_id"], ref)
                if ref["kind"] == "artifact":
                    self.validate_artifact_input(request, ref["id"], explicit)
                if ref["kind"] == "request" and ref["id"] == request["request_id"]:
                    raise ValueError("交付绑定不能循环引用自身")
            bindings[name] = ref
        records = [("tasks", node) for node in additions]
        for key in cancelled:
            updated = deepcopy(existing[key])
            updated["status"] = "cancelled"
            records.append(("tasks", updated))
        updated_request = deepcopy(request)
        updated_request.update(
            plan_version=request["plan_version"] + 1, updated_at=now()
        )
        records.append(("requests", updated_request))
        serialized = {
            "tasks": [
                {key: node[key] for key in PlannedTask.model_fields if key in node}
                for node in additions
            ],
            "cancel_task_ids": list(plan.cancel_task_ids),
            "output_bindings": bindings,
            "reason": plan.reason,
        }
        self.stores.save_many(
            records,
            expected_revisions={request["request_id"]: request["current_revision_id"]},
            event={
                "session_id": request["session_id"],
                "turn_id": turn_id,
                "event_type": "plan_updated",
                "actor": "Supervisor",
                "request_id": request["request_id"],
                "payload": {
                    "plan": serialized,
                    "revision_id": request["current_revision_id"],
                    "plan_version": updated_request["plan_version"],
                },
            },
        )
        request.update(updated_request)

    def refresh_outputs(self, request):
        resolved = {}
        for name, ref in self.requests.plan_bindings(request).items():
            try:
                item = self.resolve(request["session_id"], ref)
            except ValueError:
                continue
            if item.get("artifact_id"):
                resolved[name] = {"kind": "artifact", "id": item["artifact_id"]}
            elif item.get("event"):
                resolved[name] = ref
            elif item.get("semantic_binding"):
                resolved[name] = ref
        request["output_refs"] = resolved
        request["updated_at"] = now()
        self.stores.save_many(
            [("requests", request)],
            expected_revisions={request["request_id"]: request["current_revision_id"]},
        )
        return resolved
