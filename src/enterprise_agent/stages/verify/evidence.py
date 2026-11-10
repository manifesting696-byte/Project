"""evidence for persisted request contracts."""

from copy import deepcopy

from enterprise_agent.domain.errors import UpstreamPending


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
            raise UpstreamPending("上游 Request 的交付尚未完成")
        return self.resolve_delivery(request, bindings[name], visiting)
    task = self.stores.get("tasks", key)
    if task["status"] != "completed":
        raise UpstreamPending("上游执行 Task 尚未完成")
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
        raise UpstreamPending("上游未生成声明的输出")
    return self.resolve(session_id, outputs[name], visiting)


def resolve_delivery(self, request, ref, visiting=None):
    value = self.resolve(request["session_id"], ref, visiting)
    if (
        value.get("event")
        and value["event"].get("request_id") == request["request_id"]
        and request["status"] != "completed"
    ):
        raise UpstreamPending("该回答交付需要依据当前输入重新生成")
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
                raise UpstreamPending("该文字交付需要依据当前输入重新生成")
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
                    raise UpstreamPending("该交付的执行结果已经失效")
    return value


def outputs(self, request):
    resolved = {}
    specs = {item["name"]: item for item in request["spec"]["outputs"]}
    for name, ref in self.plan_bindings(request).items():
        try:
            item = self.resolve_delivery(request, ref)
        except (ValueError, UpstreamPending):
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
                    pending.extend([{"kind": "artifact", "id": block["artifact_id"]}])
        elif ref["kind"] == "artifact" and ref["id"] not in allowed:
            allowed.add(ref["id"])
            pending.extend(
                {"kind": "artifact", "id": key}
                for key in self.stores.get("artifacts", ref["id"])[
                    "source_artifact_ids"
                ]
            )
    return allowed
