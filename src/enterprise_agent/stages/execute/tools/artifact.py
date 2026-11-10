from enterprise_agent.domain.errors import PermissionDenied
from enterprise_agent.stages.execute.tools.compute import analysis_tools
from enterprise_agent.stages.execute.tools.presentation import presentation_tools
from enterprise_agent.stages.execute.tools.read import common_tools
from enterprise_agent.stages.execute.tools.sql import query_tools


class SpecialistToolbox:
    """Per-task access scope and produced artifact tracking, shared by capability tools."""

    common_tools = common_tools
    query_tools = query_tools
    analysis_tools = analysis_tools
    presentation_tools = presentation_tools

    def __init__(self, node, allowed, registry, artifacts, gateway):
        self.node = node
        self.allowed = set(allowed)
        self.created = set()
        self.registry = registry
        self.artifacts = artifacts
        self.gateway = gateway

    def authorize(self, artifact_id):
        if artifact_id not in self.allowed | self.created:
            raise PermissionDenied("仅允许访问当前输入及其来源产物")

    def inspect(self, artifact_id):
        self.authorize(artifact_id)
        return self.artifacts.inspect(artifact_id)

    def read(self, artifact_id, selector=None):
        self.authorize(artifact_id)
        return self.artifacts.read(artifact_id, selector)

    def save(self, kind, title, summary, content, source_ids, payload=None, sql=None):
        for key in source_ids:
            self.authorize(key)
        result = self.artifacts.save(
            self.node,
            kind,
            title,
            summary,
            content,
            list(dict.fromkeys(source_ids)),
            payload,
            sql,
        )
        self.created.add(result["artifact_id"])
        return {**result, "overview": self.artifacts.inspect(result["artifact_id"])}
