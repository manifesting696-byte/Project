"""The four specialist roles and their dispatch table."""

from enterprise_agent.stages.execute.agents.invocation import ArtifactAgent
from enterprise_agent.stages.execute.prompt import PRESENTATION


class PresentationAgent(ArtifactAgent):
    role = "PresentationAgent"
    instruction = PRESENTATION
    output_types = frozenset({"table", "chart", "report"})

    def role_tools(self, toolbox, context):
        return toolbox.presentation_tools()
