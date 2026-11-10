"""Materialize answer drafts and publish ordered references to immutable content."""

from enterprise_agent.domain.answer import Response, ResponseBlock
from enterprise_agent.domain.task import Plan


class Responses:
    def __init__(self, stores, artifacts, requests, tasks):
        self.stores = stores
        self.artifacts = artifacts
        self.requests = requests
        self.tasks = tasks

    def message(self, session_id, turn_id, content, request=None, source_ids=()):
        origin = {
            "session_id": session_id,
            "created_by_turn_id": turn_id,
            "producer": "System",
        }
        if request:
            origin.update(
                request_id=request["request_id"],
                revision_id=request["current_revision_id"],
            )
        saved = self.artifacts.save(
            origin, "text", "回复", content[:160], content, list(source_ids)
        )
        return self.block(saved["artifact_id"], request)

    @staticmethod
    def block(artifact_id, request=None):
        return ResponseBlock(
            block_id="pending",
            artifact_id=artifact_id,
            request_id=request["request_id"] if request else None,
            revision_id=request["current_revision_id"] if request else None,
        )

    def materialize(
        self,
        request,
        turn_id,
        draft,
        extra_refs=(),
        output_names=None,
        bind=False,
        workflow_key=None,
    ):
        if isinstance(draft, Response):
            return draft
        deliveries = self.requests.outputs(request)
        refs = [
            ref
            for name, ref in deliveries.items()
            if output_names is None or name in output_names
        ]
        refs += [binding["source"] for binding in request["spec"]["inputs"]]
        refs += [ref.model_dump(exclude_none=True) for ref in extra_refs]
        allowed = self.requests.artifact_scope(refs)
        specs = {item["name"]: item for item in request["spec"]["outputs"]}
        input_ids = []
        for binding in request["spec"]["inputs"]:
            try:
                value = self.requests.resolve(request["session_id"], binding["source"])
                if value.get("artifact_id"):
                    input_ids.append(value["artifact_id"])
            except ValueError:
                # A clarification can be published before its upstream work is ready.
                pass
        input_ids = sorted(set(input_ids))
        blocks, bindings, displayed = [], {}, set()
        text_outputs = {}

        def display(key):
            if key not in displayed:
                blocks.append(self.block(key, request))
                displayed.add(key)

        for item in draft.blocks:
            if item.request_id not in (None, request["request_id"]):
                raise ValueError("本轮回答包含其他 Request 的交付")
            name = item.output_name
            if name is not None and (
                name not in specs
                or (output_names is not None and name not in output_names)
            ):
                raise ValueError("回答块的 output_name 不属于本轮交付")
            sources = []
            for ref in item.refs:
                value = ref.model_dump(exclude_none=True)
                if (
                    ref.kind == "request"
                    and ref.id == request["request_id"]
                    and ref.output not in deliveries
                ):
                    raise ValueError(
                        "回答引用了当前 Request 尚未生成的交付。"
                        "新文字应写入 content，可用 output_name 指定交付名称；"
                        "refs 只引用已有证据，普通常识或系统介绍使用空 refs。"
                    )
                self.requests.validate_ref(request["session_id"], value)
                resolved = self.requests.resolve(request["session_id"], value)
                key = resolved.get("artifact_id")
                if key:
                    if key not in allowed:
                        raise ValueError("回答产物不属于本轮需求的输入或交付")
                    sources.append(key)
            if item.content_type == "text" and item.content:
                cited_sources = list(sources)
                origin = {
                    "session_id": request["session_id"],
                    "created_by_turn_id": turn_id,
                    "request_id": request["request_id"],
                    "revision_id": request["current_revision_id"],
                    "producer": "Supervisor",
                    "workflow_key": workflow_key,
                }
                sources = list(dict.fromkeys(sources + input_ids))
                provenance = {"method": "answer", "input_artifact_ids": input_ids}
                saved = self.artifacts.save(
                    origin,
                    "text",
                    request["title"],
                    item.content[:160],
                    item.content,
                    sources,
                    {"provenance": provenance},
                )
                key = saved["artifact_id"]
                display(key)
                if name is None and bind:
                    candidates = [
                        value
                        for value in specs
                        if specs[value]["content_type"] == "text"
                        and value not in deliveries
                    ]
                    if len(candidates) == 1:
                        name = candidates[0]
                if name:
                    if specs[name]["content_type"] != "text":
                        raise ValueError("文字交付必须绑定到 text 输出")
                    text_outputs.setdefault(name, []).append(key)
                # Preserve visible evidence cards, while source links remain provenance.
                for source_id in cited_sources:
                    if self.artifacts.inspect(source_id)["content_type"] != "text":
                        display(source_id)
            else:
                if not sources:
                    raise ValueError("展示块必须引用真实 Artifact")
                for key in sources:
                    if self.artifacts.inspect(key)["content_type"] != item.content_type:
                        raise ValueError("展示块类型与实际产物不一致")
                    display(key)
                if name:
                    if (
                        len(sources) != 1
                        or specs[name]["content_type"] != item.content_type
                    ):
                        raise ValueError("命名交付必须唯一绑定到匹配的 Artifact")
                    bindings[name] = {"kind": "artifact", "id": sources[0]}
        for name, keys in text_outputs.items():
            key = keys[0]
            if len(keys) > 1:
                content = "\n\n".join(self.artifacts.content(value) for value in keys)
                key = self.artifacts.save(
                    origin,
                    "text",
                    specs[name]["description"],
                    content[:160],
                    content,
                    keys,
                    {
                        "provenance": {
                            "method": "answer",
                            "input_artifact_ids": input_ids,
                        }
                    },
                )["artifact_id"]
            bindings[name] = {"kind": "artifact", "id": key}
        if bindings and any(
            deliveries.get(name) != ref for name, ref in bindings.items()
        ):
            self.tasks.apply_plan(
                request,
                turn_id,
                Plan(output_bindings=bindings, reason="回答交付已保存为 Artifact"),
            )
        for index, block in enumerate(blocks, 1):
            block.block_id = f"block_{index:03d}"
        return Response(blocks=blocks)
