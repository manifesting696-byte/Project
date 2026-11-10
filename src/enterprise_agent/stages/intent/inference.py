"""Stage-specific model invocation; injected supervisor supplies tools and context."""

from enterprise_agent.stages.intent.prompt import SUPERVISOR_INTENT
from enterprise_agent.stages.intent.schema import Intent


def intent(self, session_id, query, emit, feedback=None):
    context = self.context.initial(session_id, query)
    if feedback:
        context["validation_feedback"] = feedback
    return self.invoke(
        "UnifiedSupervisor.Intent",
        SUPERVISOR_INTENT,
        context,
        Intent,
        self.tools(session_id),
        emit,
    )
