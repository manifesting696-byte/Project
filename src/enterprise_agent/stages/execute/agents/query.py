"""The four specialist roles and their dispatch table."""

from enterprise_agent.stages.execute.agents.invocation import ArtifactAgent
from enterprise_agent.stages.execute.prompt import QUERY


class QueryAgent(ArtifactAgent):
    role = "QueryAgent"
    instruction = QUERY
    output_types = frozenset({"table"})

    def role_tools(self, toolbox, context):
        return toolbox.query_tools(context)
