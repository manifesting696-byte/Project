"""One Supervisor owns intent, full specification, planning, checking and answers."""

from ..core.models import AnswerDraft, Completion, Plan, RequestSpec
from ..intent.models import Intent
from .base import ModelAgent
from .prompt import (
    SUPERVISOR_ANSWER,
    SUPERVISOR_COMPLETION,
    SUPERVISOR_DEFINITION,
    SUPERVISOR_INTENT,
    SUPERVISOR_PLANNING,
)
from .tools import supervisor_tools


class Supervisor(ModelAgent):
    def __init__(self, model, context, stores, artifacts):
        super().__init__(model)
        self.context, self.stores, self.artifacts = context, stores, artifacts

    def tools(self, session_id, drafts=None):
        return supervisor_tools(
            session_id, self.context, self.stores, self.artifacts, drafts
        )

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

    def define(
        self,
        session_id,
        operation,
        previous,
        parameters,
        emit,
        drafts=None,
        required_outputs=None,
    ):
        return self.invoke(
            "UnifiedSupervisor.Definition",
            SUPERVISOR_DEFINITION,
            self.context.definition(
                session_id, operation, previous, parameters, drafts, required_outputs
            ),
            RequestSpec,
            self.tools(session_id, drafts),
            emit,
        )

    def plan(self, request, emit, new_results=None):
        context = (
            self.context.planning(request)
            if new_results is None
            else self.context.replanning(request, new_results)
        )
        return self.invoke(
            "UnifiedSupervisor.Planning",
            SUPERVISOR_PLANNING,
            context,
            Plan,
            self.tools(request["session_id"]),
            emit,
        )

    def check(self, request, query, emit):
        return self.invoke(
            "UnifiedSupervisor.Completion",
            SUPERVISOR_COMPLETION,
            self.context.answer(request, query),
            Completion,
            self.tools(request["session_id"]),
            emit,
        )

    def answer(self, session_id, context, emit):
        return self.invoke(
            "UnifiedSupervisor.Answer",
            SUPERVISOR_ANSWER,
            context,
            AnswerDraft,
            self.tools(session_id),
            emit,
        )
