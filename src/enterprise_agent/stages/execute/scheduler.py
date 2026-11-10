"""LangGraph fan-out scheduling over the persisted execution Task DAG."""

from enterprise_agent.flow.task_graph import build
from enterprise_agent.settings import MAX_PARALLEL, MAX_TASKS


class Scheduler:
    def __init__(self, stores, context, specialists):
        self.stores, self.context, self.specialists = stores, context, specialists
        self.graph = build(self).compile(checkpointer=False, name="task_scheduler")

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
