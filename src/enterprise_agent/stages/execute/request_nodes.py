"""Checkpointed Request execution, replanning, and interrupt/resume control flow."""

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from enterprise_agent.flow.state import ExecutionContext
from enterprise_agent.ledger.ledger import now
from enterprise_agent.settings import MAX_REPLANS


def begin(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    if (
        not runtime.context.get("unified") or state["preview"]
    ) and not self.upstream_ready(request):
        return {
            "route": "waiting_answer",
            "wait_kind": "upstream",
            "outcome": "needs_clarification",
        }
    if state["preview"]:
        return {
            "route": "waiting_answer",
            "wait_kind": "preview",
            "outcome": "waiting_input",
        }
    return {"route": "execute"}


def execute(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    supplied = runtime.context.get("schedule_result")
    if supplied is None and not self.upstream_ready(request):
        return {
            "route": "waiting_answer",
            "wait_kind": "upstream",
            "outcome": "needs_clarification",
        }
    request.update(status="running", updated_at=now())
    self.app.stores.put("requests", request)
    if supplied is not None and supplied["key"] != self.key(state, "schedule"):
        raise ValueError("调度结果与当前执行阶段不匹配")
    execution = (
        supplied["outcome"]
        if supplied is not None
        else self.app.scheduler.run(
            request,
            runtime.context["turn_id"],
            state["pause"],
            execution_key=self.key(state, "schedule"),
        )
    )
    update = {
        "new_results": execution["new_results"],
        "executions": state["executions"] + 1,
        "pause": False,
        "preview": False,
        "reply": None,
    }
    if execution.get("paused"):
        return {
            **update,
            "route": "waiting_answer",
            "wait_kind": "batch",
            "outcome": "waiting_input",
        }
    if execution.get("waiting_for_requests"):
        return {
            **update,
            "route": "waiting_answer",
            "wait_kind": "upstream",
            "outcome": "needs_clarification",
        }
    if execution["decision_required"]:
        return {
            **update,
            "route": "replan" if update["executions"] <= MAX_REPLANS else "finalize",
        }
    return {**update, "route": "check"}


def wait(self, state, runtime: Runtime[ExecutionContext]):
    interrupt(
        {
            "request_id": state["request_id"],
            "revision_id": state["revision_id"],
            "reason": state["wait_kind"],
            "blocks": state["reply"]["blocks"],
        }
    )
    self.request(state)
    decision = state["wait_kind"] == "clarification" or (
        state["wait_kind"] == "batch"
        and any(
            item.get("replan_after") or item.get("suggestions")
            for item in state["new_results"]
        )
    )
    return {
        "route": "replan" if decision else "execute",
        "preview": False,
        "pause": False,
        "run_id": runtime.context["turn_id"],
        "executions": 0,
        "completion": None,
        "reply": None,
    }
