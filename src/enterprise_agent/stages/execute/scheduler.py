"""LangGraph fan-out scheduling over the persisted execution Task DAG."""

from enterprise_agent.domain.errors import (
    AgentError,
    PermissionDenied,
    UpstreamPending,
    VersionConflict,
)
from enterprise_agent.flow.task_graph import build
from enterprise_agent.ledger.ledger import now
from enterprise_agent.settings import MAX_PARALLEL, MAX_TASKS


class Scheduler:
    def __init__(self, stores, context, specialists):
        self.stores, self.context, self.specialists = stores, context, specialists
        self.graph = build(self).compile(checkpointer=False, name="task_scheduler")

    def run(self, request, turn_id, pause=False, execution_key=None):
        scope = {
            "request_id": request["request_id"],
            "revision_id": request["current_revision_id"],
            "execution_key": execution_key,
            "pause": pause,
        }
        result = self.run_many(request["session_id"], turn_id, [scope])[
            request["request_id"]
        ]
        self.ensure_success(result)
        return result

    def run_many(self, session_id, turn_id, scopes):
        """One Task DAG dispatch loop, one concurrency budget, many owners."""
        scopes = {item["request_id"]: item for item in scopes}
        completed, errors = {}, {}
        for key, scope in scopes.items():
            request = self.stores.get("requests", key)
            if request["session_id"] != session_id:
                raise PermissionDenied("调度范围不属于当前会话")
            if request["current_revision_id"] != scope["revision_id"]:
                errors[key] = {
                    "kind": "version_conflict",
                    "message": "执行现场对应的需求版本已失效，请使用当前版本",
                }
                continue
            try:
                self.stores.save_many(
                    [
                        (
                            "requests",
                            {**request, "status": "running", "updated_at": now()},
                        )
                    ],
                    expected_revisions={key: scope["revision_id"]},
                )
            except VersionConflict as exc:
                errors[key] = {"kind": "version_conflict", "message": str(exc)}
        events = self.stores.list("session_events", "session_id", session_id)
        by_execution = {
            s["execution_key"]: key for key, s in scopes.items() if s["execution_key"]
        }
        for event in events:
            owner = by_execution.get(event["payload"].get("execution_key"))
            if owner and event.get("task_id"):
                node = self.stores.get("tasks", event["task_id"])
                if node["request_id"] == owner:
                    completed[node["task_id"]] = self.task_result(node)
        stopped = set(errors)
        for key, scope in scopes.items():
            own = [
                value
                for tid, value in completed.items()
                if self.stores.get("tasks", tid)["request_id"] == key
            ]
            if (scope["pause"] and own) or any(
                x.get("replan_after") or x.get("suggestions") for x in own
            ):
                stopped.add(key)
        result = self.graph.invoke(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "scopes": scopes,
                "stopped": list(stopped),
                "scope_errors": errors,
                "results": completed,
            },
            {
                "max_concurrency": MAX_PARALLEL,
                "recursion_limit": MAX_TASKS * 3 * max(1, len(scopes)) + 16,
            },
        )
        outcomes = {}
        for key, scope in scopes.items():
            own = [
                value
                for tid, value in result["results"].items()
                if self.stores.get("tasks", tid)["request_id"] == key
            ]
            waiting = False
            if key not in result["scope_errors"]:
                request = self.stores.get("requests", key)
                try:
                    for binding in request["spec"]["inputs"]:
                        if binding["source"]["kind"] == "request":
                            self.context.requests.resolve(session_id, binding["source"])
                    for node in self.stores.list("tasks", "request_id", key):
                        if node["status"] in ("pending", "running"):
                            for binding in node["input_bindings"]:
                                if binding["source"]["kind"] == "request":
                                    if binding["source"]["id"] in result["stopped"]:
                                        raise UpstreamPending(
                                            "上游正在重规划或等待继续"
                                        )
                                    self.context.requests.resolve(
                                        session_id, binding["source"]
                                    )
                except UpstreamPending:
                    waiting = True
            outcomes[key] = {
                "new_results": own,
                "decision_required": any(
                    x.get("replan_after") or x.get("suggestions") for x in own
                ),
                "paused": bool(scope["pause"] and own),
                "waiting_for_requests": waiting,
                "blocked": "上游执行尚未完成" if waiting else None,
            }
            if key in result["scope_errors"]:
                outcomes[key]["error"] = result["scope_errors"][key]
        return outcomes

    @staticmethod
    def ensure_success(outcome):
        failure = outcome.get("error")
        if failure:
            error = {
                "version_conflict": VersionConflict,
                "permission_denied": PermissionDenied,
            }.get(failure["kind"], AgentError)
            raise error(failure["message"])

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
