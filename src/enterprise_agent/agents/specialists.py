"""The four specialist roles and their dispatch table."""

from ..core.models import SemanticBinding
from .capabilities import CAPABILITIES
from .base import ArtifactAgent, SpecialistAgent
from .prompt import ANALYSIS, PRESENTATION, QUERY, SEMANTIC
from .tools import semantic_tools


class SemanticAgent(SpecialistAgent):
    def __init__(self, model, registry, tool_registry=None):
        super().__init__(model)
        self.registry = registry
        self.tool_registry = tool_registry

    def execute(self, context, allowed, emit):
        binding = self.invoke(
            "SemanticAgent",
            SEMANTIC,
            context,
            SemanticBinding,
            self.tool_registry.build("SemanticAgent", context)
            if self.tool_registry is not None
            else semantic_tools(self.registry),
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

    def __init__(self, events=None):
        self.agents = {}
        self.capabilities = {"Supervisor": CAPABILITIES["Supervisor"]}
        self.events = events

    def register(self, role, agent, description):
        if role in self.agents or role == "Supervisor":
            raise ValueError(f"Agent 重复注册：{role}")
        if not role or not description or not callable(getattr(agent, "execute", None)):
            raise ValueError("Agent 必须提供名称、职责说明和 execute")
        self.agents[role] = agent
        self.capabilities[role] = description

        def dispose():
            self.agents.pop(role, None)
            self.capabilities.pop(role, None)

        return dispose

    def execute(self, context, allowed, emit):
        role = context["task"]["assigned_agent"]
        if role not in self.agents:
            raise ValueError(f"Agent 当前未启用：{role}")
        scope = {"role": role, "task_id": context["task"]["task_id"]}
        if self.events:
            self.events.emit("agent/before_execute", scope)
        result = self.agents[role].execute(context, allowed, emit)
        if self.events:
            self.events.notify(
                "agent/after_execute", {**scope, "status": result["status"]}
            )
        return result
