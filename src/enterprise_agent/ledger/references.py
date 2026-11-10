"""references for persisted request contracts."""

from enterprise_agent.domain.errors import PermissionDenied, RepairableError


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
        raise PermissionDenied("引用不属于当前会话")
    output = ref.get("output")
    if kind == "request" and output:
        if output not in {value["name"] for value in item["spec"]["outputs"]}:
            raise RepairableError(
                "Request 未声明该交付名称", code="ledger.invalid_proposal"
            )
    if kind == "task" and output:
        semantic = (
            item["assigned_agent"] == "SemanticAgent" and output == "semantic_binding"
        )
        if output not in item["expected_outputs"] and not semantic:
            raise RepairableError(
                "执行 Task 未声明该输出", code="ledger.invalid_proposal"
            )
    if kind == "event" and ref.get("block_id"):
        if not any(
            block["block_id"] == ref["block_id"]
            for block in item["payload"].get("blocks", [])
        ):
            raise RepairableError("历史回答块不存在", code="ledger.invalid_proposal")
