"""The four specialist roles and their dispatch table."""

from ..core.models import SemanticBinding
from .base import ArtifactAgent, SpecialistAgent
from .prompt import ANALYSIS, PRESENTATION, QUERY, SEMANTIC
from .tools import semantic_tools


class SemanticAgent(SpecialistAgent):
    def __init__(self, model, registry):
        super().__init__(model)
        self.registry = registry

    def execute(self, context, allowed, emit):
        binding = self.invoke(
            "SemanticAgent",
            SEMANTIC,
            context,
            SemanticBinding,
            semantic_tools(self.registry),
            emit,
        )
        self.registry.validate_binding(binding)
        return {
            "status": "completed"
            if binding.status == "bound"
            else "needs_clarification",
            "summary": binding.explanation,
            "semantic_binding": binding.model_dump(exclude_none=True),
            "produced_outputs": {},
            "suggestions": binding.ambiguities,
        }


class QueryAgent(ArtifactAgent):
    role = "QueryAgent"
    instruction = QUERY
    output_types = frozenset({"table"})

    def role_tools(self, toolbox, context):
        return toolbox.query_tools(context)


class AnalysisAgent(ArtifactAgent):
    role = "AnalysisAgent"
    instruction = ANALYSIS
    output_types = frozenset({"text", "table"})

    def role_tools(self, toolbox, context):
        return toolbox.analysis_tools()


class PresentationAgent(ArtifactAgent):
    role = "PresentationAgent"
    instruction = PRESENTATION
    output_types = frozenset({"table", "chart", "report"})

    def role_tools(self, toolbox, context):
        return toolbox.presentation_tools()


class Specialists:
    """Scheduler-facing adapter; role implementations can be replaced at composition."""

    def __init__(self, model, registry, artifacts, gateway, overrides=None):
        self.agents = {
            "SemanticAgent": SemanticAgent(model, registry),
            "QueryAgent": QueryAgent(model, registry, artifacts, gateway),
            "AnalysisAgent": AnalysisAgent(model, registry, artifacts, gateway),
            "PresentationAgent": PresentationAgent(model, registry, artifacts, gateway),
        }
        if overrides:
            unknown = set(overrides) - self.agents.keys()
            if unknown:
                raise ValueError(f"未知专业 Agent：{sorted(unknown)}")
            self.agents.update(overrides)

    def execute(self, context, allowed, emit):
        role = context["task"]["assigned_agent"]
        return self.agents[role].execute(context, allowed, emit)
