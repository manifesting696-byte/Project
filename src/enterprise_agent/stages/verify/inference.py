"""Stage-specific model invocation; injected supervisor supplies tools and context."""

from enterprise_agent.stages.verify.prompt import SUPERVISOR_COMPLETION
from enterprise_agent.stages.verify.schema import Completion


def check(self, request, query, emit, feedback=None):
    context = self.context.answer(request, query)
    if feedback:
        context["validation_feedback"] = feedback
    return self.invoke(
        "UnifiedSupervisor.Completion",
        SUPERVISOR_COMPLETION,
        context,
        Completion,
        self.tools(request["session_id"]),
        emit,
    )
