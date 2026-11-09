"""Validate execution DAGs, bind logical outputs and resolve exact inputs."""

from copy import deepcopy

from ..core.config import MAX_TASKS
from ..core.models import Plan, PlannedTask
from ..persistence.stores import now


class Tasks:
    def __init__(self, stores, requests):
        self.stores, self.requests = stores, requests

    def allowed_artifacts(self, request, request_views=None):
        refs = []
        for binding in request["spec"]["inputs"]:
            ref = binding["source"]
            if ref["kind"] == "request" and ref["id"] in (request_views or {}):
                candidate = request_views[ref["id"]]
                if not self.stores.exists("requests", ref["id"]):
                    continue
                stored = self.stores.get("requests", ref["id"])
                if candidate["current_revision_id"] != stored["current_revision_id"]:
                    continue
            refs.append(ref)
        return self.requests.artifact_scope(refs)

    def validate_ref(self, request, ref, request_views):
        session_id = request["session_id"]
        if ref["kind"] == "request" and not any(
            binding["source"] == ref for binding in request["spec"]["inputs"]
        ):
            raise ValueError("执行计划的 Request 来源需在完整需求输入中声明")
        if ref["kind"] == "request" and ref["id"] in request_views:
            source = request_views[ref["id"]]
            if source["session_id"] != session_id:
                raise PermissionError("引用不属于当前会话")
            if ref.get("output") not in {
                item["name"] for item in source["spec"]["outputs"]
            }:
                raise ValueError("Request 未声明该命名交付")
            return
        self.requests.validate_ref(session_id, ref)

    def validate_artifact_input(self, request, artifact_id, explicit, nodes):
        artifact = self.stores.get("artifacts", artifact_id)
        self.requests.validate_ref(
            request["session_id"], {"kind": "artifact", "id": artifact_id}
        )
        if (
            artifact.get("request_id") != request["request_id"]
            and artifact_id not in explicit
        ):
            raise ValueError("其他 Request 的产物需在当前需求输入中声明")
        producer = nodes.get(artifact.get("task_id"))
        if producer and producer["status"] == "completed":
            return
        if (
            not artifact.get("task_id")
            and artifact.get("request_id") == request["request_id"]
            and artifact.get("revision_id") == request["current_revision_id"]
        ):
            return
        if artifact_id not in explicit:
            raise ValueError("失效执行的产物需要在当前需求输入中明确指定后才能复用")

    def prepare_plan(
        self,
        request,
        turn_id,
        plan: Plan,
        nodes=None,
        request_views=None,
        inherited_bindings=None,
    ):
        nodes = (
            self.stores.session_tasks(request["session_id"]) if nodes is None else nodes
        )
        request_views = request_views or {}
        existing = {node["task_id"]: node for node in nodes}
        output_names = {item["name"] for item in request["spec"]["outputs"]}
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
                    self.validate_ref(request, ref, request_views)
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
        explicit = self.allowed_artifacts(request, request_views)
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
                    if (
                        node in additions
                        and source["request_id"] != request["request_id"]
                    ):
                        raise ValueError(
                            "跨需求输入使用 Request 命名交付或显式 Artifact"
                        )
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
                    self.validate_ref(request, ref, request_views)
                    if ref["kind"] == "artifact":
                        self.validate_artifact_input(
                            request, ref["id"], explicit, combined
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
        inherited_bindings = (
            self.requests.plan_bindings(request)
            if inherited_bindings is None
            else inherited_bindings
        )
        bindings = {
            name: ref
            for name, ref in inherited_bindings.items()
            if name in output_names
            and ref["id"] not in cancelled
            and (
                ref["kind"] != "task"
                or (
                    ref["id"] in combined
                    and combined[ref["id"]]["status"] not in ("superseded", "cancelled")
                )
            )
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
                if source["request_id"] != request["request_id"]:
                    raise ValueError("跨需求交付使用 Request 命名交付或显式 Artifact")
                semantic = (
                    source["assigned_agent"] == "SemanticAgent"
                    and ref.get("output") == "semantic_binding"
                )
                if ref.get("output") not in source["expected_outputs"] and not semantic:
                    raise ValueError("交付绑定必须明确实际输出名称")
            else:
                self.validate_ref(request, ref, request_views)
                if ref["kind"] == "artifact":
                    self.validate_artifact_input(request, ref["id"], explicit, combined)
                    expected = next(
                        item["content_type"]
                        for item in request["spec"]["outputs"]
                        if item["name"] == name
                    )
                    if (
                        self.stores.get("artifacts", ref["id"])["content_type"]
                        != expected
                    ):
                        raise ValueError("实际产物类型与交付要求不一致")
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
        return {
            "request": updated_request,
            "records": records,
            "event": {
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
        }

    def apply_plan(self, request, turn_id, plan: Plan):
        prepared = self.prepare_plan(request, turn_id, plan)
        self.stores.save_many(
            prepared["records"],
            expected_revisions={request["request_id"]: request["current_revision_id"]},
            expected_plan_versions={request["request_id"]: request["plan_version"]},
            event=prepared["event"],
        )
        request.update(prepared["request"])
