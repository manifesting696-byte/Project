"""Checkpointed Request execution, replanning, and interrupt/resume control flow."""

from langgraph.runtime import Runtime

from enterprise_agent.flow.state import ExecutionContext


def replan(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    if not self.cached(state, "replan"):
        emit = self.app.emitter(
            state["session_id"], runtime.context["turn_id"], state["request_id"]
        )
        evidence = list(state["new_results"])
        if state["completion"]:
            evidence.append({"completion": state["completion"]})
        plan = self.app.supervisor.plan(request, emit, evidence)
        self.app.apply_plan(
            request,
            runtime.context["turn_id"],
            plan,
            workflow_key=self.key(state, "replan"),
        )
    return {"route": "execute", "completion": None, "reply": None}
