"""LangGraph fan-out scheduling over the persisted execution Task DAG."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from enterprise_agent.settings import MAX_PARALLEL, MAX_TASKS
from enterprise_agent.flow.state import ScheduleState
from enterprise_agent.ledger.ledger import now


class Scheduler:
    def __init__(self, stores, context, specialists):
        self.stores, self.context, self.specialists = stores, context, specialists
        builder = StateGraph(ScheduleState)
        builder.add_node("dispatch", self.dispatch)
        builder.add_node("execute_task", self.execute)
        builder.add_node("collect", self.collect)
        builder.add_edge(START, "dispatch")
        builder.add_conditional_edges("dispatch", self.send_ready)
        builder.add_edge("execute_task", "collect")
        builder.add_conditional_edges("collect", lambda state: state["route"])
        self.graph = builder.compile(checkpointer=False, name="task_scheduler")

    def run(self, request, turn_id, pause=False, execution_key=None):
        completed = {}
        if execution_key:
            for event in self.stores.list(
                "session_events", "session_id", request["session_id"]
            ):
                if event["payload"].get("execution_key") == execution_key:
                    node = self.stores.get("tasks", event["task_id"])
                    completed[node["task_id"]] = self.task_result(node)
        result = self.graph.invoke(
            {
                "request_id": request["request_id"],
                "session_id": request["session_id"],
                "revision_id": request["current_revision_id"],
                "turn_id": turn_id,
                "execution_key": execution_key,
                "pause": pause,
                "results": completed,
                "decision_required": any(
                    node.get("replan_after") or node.get("suggestions")
                    for node in completed.values()
                ),
                "paused": bool(pause and completed),
                "waiting_for_requests": False,
            },
            {"max_concurrency": MAX_PARALLEL, "recursion_limit": MAX_TASKS * 3 + 16},
        )
        return {
            "new_results": list(result["results"].values()),
            "decision_required": result["decision_required"],
            "paused": result["paused"],
            "waiting_for_requests": result["waiting_for_requests"],
            "blocked": result.get("blocked"),
        }

    def dispatch(self, state):
        if state["decision_required"] or state["paused"]:
            return {"ready": [], "retry_dispatch": False}
        nodes = self.stores.list("tasks", "request_id", state["request_id"])
        by_id = {
            node["task_id"]: node
            for node in self.stores.session_tasks(state["session_id"])
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
            except ValueError:
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

    @staticmethod
    def send_ready(state):
        if not state["ready"]:
            return "dispatch" if state["retry_dispatch"] else END
        return [
            Send("execute_task", {**state, "task_id": key}) for key in state["ready"]
        ]

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
                    "summary": str(exc)[:600],
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

    @staticmethod
    def task_result(node):
        return {
            key: node.get(key)
            for key in (
                "task_id",
                "status",
                "summary",
                "produced_outputs",
                "suggestions",
                "replan_after",
            )
        }

    @staticmethod
    def collect(state):
        wave = [state["results"][key] for key in state["ready"]]
        decision = any(
            node.get("replan_after") or node.get("suggestions") for node in wave
        )
        stop = state["pause"] or decision
        return {
            "paused": state["pause"],
            "decision_required": decision,
            "route": END if stop else "dispatch",
        }
