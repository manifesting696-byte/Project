"""One Supervisor owns intent, full specification, planning, checking and answers."""

from enterprise_agent.domain.models import AnswerDraft, Completion, Plan
from enterprise_agent.stages.intent.schema import Intent, RequestPreparation
from enterprise_agent.stages.execute.agents.base import ModelAgent
from enterprise_agent.prompt.stages import (
    SUPERVISOR_ANSWER,
    SUPERVISOR_COMPLETION,
    SUPERVISOR_PREPARATION,
    SUPERVISOR_INTENT,
    SUPERVISOR_PLANNING,
)
from enterprise_agent.stages.execute.agents.toolbox import supervisor_tools


class Supervisor(ModelAgent):
    def __init__(self, model, context, stores, artifacts, tool_registry=None):
        super().__init__(model)
        self.context, self.stores, self.artifacts = context, stores, artifacts
        self.tool_registry = tool_registry

    def tools(self, session_id, drafts=None, confirmed_refs=None):
        if self.tool_registry is not None:
            return self.tool_registry.build(
                "Supervisor",
                {
                    "session_id": session_id,
                    "drafts": drafts,
                    "confirmed_refs": confirmed_refs,
                },
            )
        return supervisor_tools(
            session_id,
            self.context,
            self.stores,
            self.artifacts,
            drafts,
            confirmed_refs,
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

    def prepare(
        self,
        session_id,
        operation,
        previous,
        parameters,
        emit,
        drafts=None,
        required_outputs=None,
        feedback=None,
        confirmed_refs=None,
    ):
        return self.invoke(
            "UnifiedSupervisor.Preparation",
            SUPERVISOR_PREPARATION,
            self.context.preparation(
                session_id,
                operation,
                previous,
                parameters,
                drafts,
                required_outputs,
                feedback,
            ),
            RequestPreparation,
            self.tools(session_id, drafts, confirmed_refs),
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
