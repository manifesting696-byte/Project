"""LangGraph fan-out scheduling over the persisted execution Task DAG."""

from langgraph.graph import END

from enterprise_agent.domain.errors import UpstreamPending, public_message
from enterprise_agent.ledger.ledger import now
from enterprise_agent.settings import MAX_PARALLEL


def dispatch(self, state):
    if state["decision_required"] or state["paused"]:
        return {"ready": [], "retry_dispatch": False}
    nodes = self.stores.list("tasks", "request_id", state["request_id"])
    by_id = {
        node["task_id"]: node for node in self.stores.session_tasks(state["session_id"])
    }
    ready, waiting, changed = [], False, False
    for node in nodes:
        if node["status"] not in ("pending", "running"):
            continue
        dependencies = [by_id.get(key) for key in node["depends_on"]]
        if any(
            dep is None
            or dep["status"]
            in (
                "failed",
                "skipped",
                "needs_clarification",
                "cancelled",
                "superseded",
            )
            for dep in dependencies
        ):
            node.update(
                status="skipped", summary="依赖没有可用输出", completed_at=now()
            )
            self.stores.save_many(
                [("tasks", node)],
                expected_revisions={state["request_id"]: state["revision_id"]},
            )
            changed = True
            continue
        try:
            for binding in node["input_bindings"]:
                if binding["source"]["kind"] == "request":
                    self.context.requests.resolve(
                        state["session_id"], binding["source"]
                    )
        except UpstreamPending:
            waiting = True
            continue
        if all(dep["status"] == "completed" for dep in dependencies):
            ready.append(node["task_id"])
    return {
        "ready": ready[:MAX_PARALLEL],
        "waiting_for_requests": waiting,
        "blocked": "上游执行尚未完成" if waiting else None,
        "retry_dispatch": changed and not ready,
    }


def execute(self, state):
    node = self.stores.get("tasks", state["task_id"])
    expected = {state["request_id"]: state["revision_id"]}
    if node["status"] != "completed":
        node.update(status="running", started_at=node.get("started_at") or now())
        self.stores.save_many([("tasks", node)], expected_revisions=expected)

        def emit(kind, payload):
            record = self.stores.event(
                state["session_id"],
                state["turn_id"],
                kind,
                node["assigned_agent"],
                payload,
                request_id=node["request_id"],
                task_id=node["task_id"],
            )
            if self.specialists.events is not None:
                self.specialists.events.notify("session/event", record)
            return record

        try:
            for binding in node["input_bindings"]:
                value = self.context.requests.resolve(
                    state["session_id"], binding["source"]
                )
                if value.get("artifact_id"):
                    binding["source"] = {
                        "kind": "artifact",
                        "id": value["artifact_id"],
                    }
            self.stores.save_many([("tasks", node)], expected_revisions=expected)
            context, allowed = self.context.task(node)
            result = self.specialists.execute(context, allowed, emit)
        except Exception as exc:
            result = {
                "status": "failed",
                "summary": public_message(exc),
                "produced_outputs": {},
            }
        node.update(**result, completed_at=now())
        self.stores.save_many(
            [("tasks", node)],
            expected_revisions=expected,
            event={
                "session_id": state["session_id"],
                "turn_id": state["turn_id"],
                "event_type": "agent_output",
                "actor": node["assigned_agent"],
                "request_id": node["request_id"],
                "task_id": node["task_id"],
                "payload": {
                    "summary": result["summary"],
                    "status": result["status"],
                    "refs": list(result["produced_outputs"].values()),
                    "execution_key": state["execution_key"],
                },
            },
        )
    return {"results": {node["task_id"]: self.task_result(node)}}


def collect(state):
    wave = [state["results"][key] for key in state["ready"]]
    decision = any(node.get("replan_after") or node.get("suggestions") for node in wave)
    stop = state["pause"] or decision
    return {
        "paused": state["pause"],
        "decision_required": decision,
        "route": END if stop else "dispatch",
    }
