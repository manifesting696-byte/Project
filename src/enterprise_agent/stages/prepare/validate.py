"""validate for persisted request contracts."""

from enterprise_agent.domain.errors import RepairableError


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
                raise RepairableError(
                    "需求输入使用 Request 命名交付或具体 Artifact",
                    code="prepare.invalid_proposal",
                )
            if source.kind == "request":
                if source.id == key:
                    raise RepairableError(
                        "历史输入应使用具体 Artifact，需求不能依赖自身交付",
                        code="prepare.invalid_proposal",
                    )
                if source.id not in graph:
                    raise RepairableError(
                        "输入引用的 Request 不存在于当前会话",
                        code="prepare.invalid_proposal",
                    )
                names = [item["name"] for item in graph[source.id]["outputs"]]
                if source.output is None and len(names) == 1:
                    source.output = names[0]
                if source.output not in names:
                    raise RepairableError(
                        "输入引用的上游交付名称未声明", code="prepare.invalid_proposal"
                    )
            else:
                self.validate_ref(session_id, source.model_dump(exclude_none=True))
        graph[key] = spec.model_dump(exclude_none=True)
    visiting, done = set(), set()

    def visit(key):
        if key in visiting:
            raise RepairableError(
                "Request 输入依赖存在循环", code="prepare.invalid_proposal"
            )
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
