"""Deterministic scheduling of execution Tasks within the current request scope."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.config import MAX_PARALLEL
from ..persistence.stores import now


class Scheduler:
    def __init__(self, stores, context, specialists):
        self.stores, self.context, self.specialists = stores, context, specialists

    def run(self, request, turn_id, pause=False):
        new_results = []
        session_id = request["session_id"]
        expected = {request["request_id"]: request["current_revision_id"]}

        def execute(node):
            def emit(kind, payload):
                return self.stores.event(
                    session_id,
                    turn_id,
                    kind,
                    node["assigned_agent"],
                    payload,
                    request_id=node["request_id"],
                    task_id=node["task_id"],
                )

            node.update(status="running", started_at=now())
            self.stores.save_many([("tasks", node)], expected_revisions=expected)
            try:
                for binding in node["input_bindings"]:
                    value = self.context.requests.resolve(session_id, binding["source"])
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
            try:
                self.stores.save_many([("tasks", node)], expected_revisions=expected)
            except ValueError:
                return self.stores.get("tasks", node["task_id"])
            emit(
                "agent_output",
                {
                    "summary": result["summary"],
                    "status": result["status"],
                    "refs": list(result["produced_outputs"].values()),
                },
            )
            return node

        while True:
            nodes = self.stores.list("tasks", "request_id", request["request_id"])
            by_id = {
                node["task_id"]: node for node in self.stores.session_tasks(session_id)
            }
            pending = [node for node in nodes if node["status"] == "pending"]
            if not pending:
                return {"new_results": new_results, "decision_required": False}
            blocked = [
                node
                for node in pending
                if any(
                    dep not in by_id
                    or by_id[dep]["status"]
                    in (
                        "failed",
                        "skipped",
                        "needs_clarification",
                        "cancelled",
                        "superseded",
                    )
                    for dep in node["depends_on"]
                )
            ]
            for node in blocked:
                node.update(
                    status="skipped", summary="依赖没有可用输出", completed_at=now()
                )
                self.stores.save_many([("tasks", node)], expected_revisions=expected)
            ready = [
                node
                for node in pending
                if node not in blocked
                and all(
                    by_id[dep]["status"] == "completed" for dep in node["depends_on"]
                )
            ]
            if not ready:
                if blocked:
                    continue
                return {
                    "new_results": new_results,
                    "decision_required": False,
                    "blocked": "上游执行尚未完成",
                }
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                futures = [pool.submit(execute, node) for node in ready[:MAX_PARALLEL]]
                wave = [future.result() for future in as_completed(futures)]
            new_results.extend(
                {
                    "task_id": node["task_id"],
                    "status": node["status"],
                    "summary": node.get("summary", ""),
                    "produced_outputs": node["produced_outputs"],
                    "suggestions": node.get("suggestions", []),
                }
                for node in wave
            )
            if pause:
                return {"new_results": new_results, "paused": True}
            if any(
                node.get("replan_after") or node.get("suggestions") for node in wave
            ):
                return {"new_results": new_results, "decision_required": True}
