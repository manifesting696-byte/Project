from enterprise_agent.model.tool_call import OBJECT, STRING, tool
from enterprise_agent.stages.execute.tools.semantic import semantic_tools


def supervisor_tools(
    session_id, context, stores, artifacts, drafts=None, confirmed_refs=None
):
    def request_state(request_id):
        if request_id in (drafts or {}):
            if confirmed_refs is not None:
                confirmed_refs.update(
                    ("request", request_id, item["name"])
                    for item in drafts[request_id]["spec"]["outputs"]
                )
            return {"request": drafts[request_id], "deliveries": {}, "tasks": []}
        context.requests.validate_ref(session_id, {"kind": "request", "id": request_id})
        request = stores.get("requests", request_id)
        if confirmed_refs is not None:
            confirmed_refs.update(
                ("request", request_id, item["name"])
                for item in request["spec"]["outputs"]
            )
        return {
            "request": request,
            "deliveries": context.requests.outputs(request),
            "tasks": stores.list("tasks", "request_id", request_id),
        }

    def task_state(task_id):
        context.requests.validate_ref(session_id, {"kind": "task", "id": task_id})
        return stores.get("tasks", task_id)

    def inspect(artifact_id):
        context.requests.validate_ref(
            session_id, {"kind": "artifact", "id": artifact_id}
        )
        if confirmed_refs is not None:
            confirmed_refs.add(("artifact", artifact_id, None))
        return context.overview(artifacts.inspect(artifact_id))

    def read(artifact_id, selector=None):
        inspect(artifact_id)
        return artifacts.read(artifact_id, selector)

    def event(event_id, block_id=None):
        ref = {"kind": "event", "id": event_id}
        if block_id:
            ref["block_id"] = block_id
        context.requests.validate_ref(session_id, ref)
        return context.requests.resolve(session_id, ref)["event"]

    return {
        "get_request_state": tool(
            "读取候选 Request 的完整当前要求、交付和执行节点",
            request_state,
            request_id=STRING,
        ),
        "get_task_state": tool(
            "读取一个实际执行 Task 的状态和输出", task_state, task_id=STRING
        ),
        "get_artifact_overview": tool(
            "读取产物概览及来源", inspect, artifact_id=STRING
        ),
        "read_artifact": tool(
            "按需读取精确切片/正文；selector.part=sql 可读取原查询 SQL 和绑定口径，不根据摘要猜测",
            read,
            artifact_id=STRING,
            selector=OBJECT,
        ),
        "read_response_event": tool(
            "读取历史事件或具体回答块，解析刚才第二点等引用",
            event,
            event_id=STRING,
            block_id=STRING,
        ),
        "get_available_capabilities": tool(
            "读取当前已启用 Agent 的职责", lambda: dict(context.capabilities)
        ),
        **semantic_tools(context.registry),
    }
