"""Stage-specific model invocation; injected supervisor supplies tools and context."""

from enterprise_agent.domain.answer import AnswerDraft
from enterprise_agent.stages.answer.prompt import SUPERVISOR_ANSWER


def answer(self, session_id, context, emit):
    return self.invoke(
        "UnifiedSupervisor.Answer",
        SUPERVISOR_ANSWER,
        context,
        AnswerDraft,
        self.tools(session_id),
        emit,
    )
