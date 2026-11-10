"""normalize for persisted request contracts."""

from enterprise_agent.domain.errors import PermissionDenied, RepairableError


def normalize(self, session_id, intent):
    aliases = {}
    for operation in intent.operations:
        if operation.action == "update_task":
            request = self.stores.get("requests", operation.request_id)
            if request["session_id"] != session_id or request["status"] == "archived":
                raise PermissionDenied("更新目标不属于当前可用需求")
        else:
            if self.stores.exists("requests", operation.request_id):
                raise RepairableError(
                    "新请求应使用本轮局部 ID，已有工作请使用 update_task",
                    code="intent.invalid_proposal",
                )
            aliases[operation.request_id] = self.stores.new_id("req")
    for operation in intent.operations:
        operation.request_id = aliases.get(operation.request_id, operation.request_id)
        for ref in operation.refs:
            if ref.kind == "request":
                ref.id = aliases.get(ref.id, ref.id)
                # Freeze a reference to the target's old output before replacing its revision.
                if (
                    ref.id == operation.request_id
                    and operation.action == "update_task"
                    and ref.output
                ):
                    previous = self.outputs(self.stores.get("requests", ref.id)).get(
                        ref.output
                    )
                    if not previous:
                        raise RepairableError(
                            "目标需求尚无可复用的该项结果",
                            code="intent.invalid_proposal",
                        )
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
            self.validate_ref(session_id, ref.model_dump(exclude_none=True), pending)
    for item in intent.clarifications:
        if item.request_id:
            self.validate_ref(
                session_id, {"kind": "request", "id": item.request_id}, pending
            )
        for ref in item.refs:
            self.validate_ref(session_id, ref.model_dump(exclude_none=True), pending)
    return self.order_operations(intent.operations)


def order_operations(operations, specs=None):
    by_id = {item.request_id: item for item in operations}
    ordered, visiting, done = [], set(), set()

    def visit(key):
        if key in visiting:
            raise RepairableError(
                "本轮 Request 依赖存在循环", code="intent.invalid_proposal"
            )
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
