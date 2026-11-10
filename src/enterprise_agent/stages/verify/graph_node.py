"""Checkpointed Request execution, replanning, and interrupt/resume control flow."""

from langgraph.runtime import Runtime

from enterprise_agent.flow.state import ExecutionContext
from enterprise_agent.settings import MAX_REPLANS
from enterprise_agent.stages.verify.retry import verify
from enterprise_agent.stages.verify.validate import validate_completion


def check(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    turn_id = runtime.context["turn_id"]
    emit = self.app.emitter(state["session_id"], turn_id, state["request_id"])
    actual = self.app.requests.outputs(request)
    missing_text = any(
        item["content_type"] == "text" and item["name"] not in actual
        for item in request["spec"]["outputs"]
    )
    nodes = self.app.stores.list("tasks", "request_id", state["request_id"])
    reply = None
    if missing_text and not any(
        node["status"] in ("pending", "running", "needs_clarification", "failed")
        for node in nodes
    ):
        draft = self.app.supervisor.answer(
            state["session_id"],
            self.app.context.answer(
                request, state["query"], output_names=state["output_names"]
            ),
            emit,
        )
        reply = self.app.materialize_answer(
            request, turn_id, draft, output_names=state["output_names"], bind=True
        ).model_dump(exclude_none=True)
    completion = verify(
        lambda feedback: self.app.supervisor.check(
            request, state["query"], emit, feedback
        ),
        lambda proposal: validate_completion(self.app, request, proposal),
    )
    route = (
        "replan"
        if completion.next_action == "replan" and state["executions"] <= MAX_REPLANS
        else "finalize"
    )
    return {
        "completion": completion.model_dump(exclude_none=True),
        "reply": reply,
        "route": route,
    }


def finalize(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    completion = state["completion"]
    pending = any(
        node["status"] in ("pending", "running")
        for node in self.app.stores.list("tasks", "request_id", state["request_id"])
    )
    complete = (
        completion
        and completion["next_action"] == "answer"
        and all(item["status"] == "completed" for item in completion["outputs"])
        and not pending
    )
    clarify = completion and (
        completion["next_action"] == "clarify"
        or any(
            item["status"] == "needs_clarification" for item in completion["outputs"]
        )
    )
    if complete:
        outcome = "completed"
    elif clarify:
        outcome = "needs_clarification"
    else:
        outcome = "partial"
    data = completion or {"reason": "本轮规划预算已用完，仍有未完成交付"}
    self.app.stores.event(
        state["session_id"],
        runtime.context["turn_id"],
        "agent_output",
        "Supervisor",
        {"summary": data["reason"], "completion": data},
        request_id=request["request_id"],
        workflow_key=self.key(state, "completion"),
    )
    if clarify:
        return {
            "outcome": outcome,
            "wait_kind": "clarification",
            "route": "waiting_answer",
            "reply": None,
        }
    return {
        "outcome": outcome,
        "route": "answer",
        "reply": state["reply"] if complete else None,
    }
