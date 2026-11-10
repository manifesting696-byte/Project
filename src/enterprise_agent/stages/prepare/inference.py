"""Stage-specific model invocation; injected supervisor supplies tools and context."""

from enterprise_agent.domain.task import Plan
from enterprise_agent.stages.prepare.prompt import (
    SUPERVISOR_PLANNING,
    SUPERVISOR_PREPARATION,
)
from enterprise_agent.stages.prepare.schema import RequestPreparation


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
