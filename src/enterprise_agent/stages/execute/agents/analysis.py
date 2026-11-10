"""The four specialist roles and their dispatch table."""

from enterprise_agent.stages.execute.agents.invocation import ArtifactAgent
from enterprise_agent.stages.execute.prompt import ANALYSIS


class AnalysisAgent(ArtifactAgent):
    role = "AnalysisAgent"
    instruction = ANALYSIS
    output_types = frozenset({"text", "table"})

    def role_tools(self, toolbox, context):
        return toolbox.analysis_tools()
