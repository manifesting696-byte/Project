"""One physical Supervisor, with separate Intent, Planning, Completion and Answer phases."""
from ..core.models import Answer, Completion, Plan
from ..intent.models import Intent
from .base import ModelAgent
from .prompt import (
    SUPERVISOR_ANSWER,
    SUPERVISOR_COMPLETION,
    SUPERVISOR_INTENT,
    SUPERVISOR_PLANNING,
)
from .tools import supervisor_tools


class Supervisor(ModelAgent):
    def __init__(self, model, context, stores, artifacts):
        super().__init__(model)
        self.context, self.stores, self.artifacts = context, stores, artifacts

    def tools(self, session_id):
        return supervisor_tools(session_id, self.context, self.stores, self.artifacts)

    def intent(self, session_id, query, emit):
        return self.invoke("UnifiedSupervisor.Intent", SUPERVISOR_INTENT,
            self.context.initial(session_id, query), Intent, self.tools(session_id), emit)

    def plan(self, task, intent, emit, new_results=None):
        context = self.context.planning(task, intent) if new_results is None else self.context.replanning(task, intent, new_results)
        return self.invoke("UnifiedSupervisor.Planning", SUPERVISOR_PLANNING,
            context, Plan, self.tools(task["origin_session_id"]), emit)

    def check(self, task, query, emit):
        return self.invoke("UnifiedSupervisor.Completion", SUPERVISOR_COMPLETION,
            self.context.answer(task, query), Completion, self.tools(task["origin_session_id"]), emit)

    def answer(self, session_id, context, emit):
        return self.invoke("UnifiedSupervisor.Answer", SUPERVISOR_ANSWER,
            context, Answer, self.tools(session_id), emit)
