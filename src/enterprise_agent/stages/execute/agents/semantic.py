"""The four specialist roles and their dispatch table."""

from enterprise_agent.domain.task import SemanticBinding
from enterprise_agent.stages.execute.agents.invocation import SpecialistAgent
from enterprise_agent.stages.execute.prompt import SEMANTIC
from enterprise_agent.stages.execute.tools.semantic import semantic_tools


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
