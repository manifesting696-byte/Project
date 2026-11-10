"""Delivery verification: prove every declared output has a real artifact."""


def validate_completion(app, request, completion):
    """Every spec output must be checked once, and completed ones need evidence."""
    outputs = {item["name"]: item for item in request["spec"]["outputs"]}
    names = [item.name for item in completion.outputs]
    if len(names) != len(set(names)) or set(names) != set(outputs):
        raise ValueError("验收必须覆盖当前 Request 的每个交付名称")
    for check in completion.outputs:
        for ref in check.refs:
            app.requests.validate_ref(
                request["session_id"], ref.model_dump(exclude_none=True)
            )
        if check.status != "completed":
            continue
        actual = app.requests.outputs(request).get(check.name)
        if not actual or not check.refs:
            raise ValueError("完成的交付必须绑定真实输出并提供证据")
        resolved = app.requests.resolve(request["session_id"], actual)
        actual_id = resolved.get("artifact_id", actual["id"])
        if actual_id not in {ref.id for ref in check.refs}:
            raise ValueError("验收证据没有指向该项实际交付")
        content_type = outputs[check.name]["content_type"]
        if not resolved.get("artifact_id"):
            raise ValueError("交付需要实际 Artifact")
        if (
            app.artifacts.inspect(resolved["artifact_id"])["content_type"]
            != content_type
        ):
            raise ValueError("实际产物类型与交付要求不一致")
