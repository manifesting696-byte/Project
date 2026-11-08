"""Dependency scheduling is deterministic; independent ready nodes run concurrently."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..core.config import MAX_PARALLEL
from ..persistence.stores import now


class Scheduler:
    def __init__(self, stores, context, specialists):
        self.stores, self.context, self.specialists = stores, context, specialists

    def run(self, task, turn_id, pause=False):
        new_results = []
        session_id = task["origin_session_id"]
        def execute(node):
            def emit(kind, payload):
                return self.stores.event(session_id, turn_id, kind, node["assigned_agent"], payload,
                                         task["task_id"], node["subtask_id"])
            node.update(status="running", started_at=now())
            self.stores.put("subtasks", node) # 把变化的状态和时间放进去
            try:
                context, allowed = self.context.subtask(task, node) # 得到subtask+限制+inputs_bindings以及allowed（允许访问的artifacts）
                result = self.specialists.execute(context, allowed, emit)
            except Exception as exc:
                result = {"status": "failed", "summary": str(exc)[:600], "produced_outputs": {}}
            node.update(**result, completed_at=now())
            self.stores.put("subtasks", node)
            emit("agent_output", {"summary": result["summary"], "status": result["status"],
                                   "refs": list(result["produced_outputs"].values())})
            return node
        while True:
            nodes = self.stores.list("subtasks", "task_id", task["task_id"])
            by_id = {node["subtask_id"]: node for node in nodes}
            pending = [node for node in nodes if node["status"] == "pending"]
            if not pending:
                return {"new_results": new_results, "decision_required": False}
            blocked = [node for node in pending if any(by_id[dep]["status"] in
                       ("failed", "skipped", "needs_clarification", "cancelled", "superseded") for dep in node["depends_on"])]
            for node in blocked: # 依赖不可用
                node.update(status="skipped", summary="依赖没有可用输出", completed_at=now())
                self.stores.put("subtasks", node)
            ready = [node for node in pending if node not in blocked and # 可用的node
                     all(by_id[dep]["status"] == "completed" for dep in node["depends_on"])]
            if not ready:
                if blocked:
                    continue
                return {"new_results": new_results, "decision_required": False}
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                futures = [pool.submit(execute, node) for node in ready[:MAX_PARALLEL]]
                wave = [future.result() for future in as_completed(futures)]
            additions = [{"subtask_id": node["subtask_id"], "status": node["status"],
                          "summary": node["summary"], "produced_outputs": node["produced_outputs"],
                          "suggestions": node.get("suggestions", [])} for node in wave]
            new_results.extend(additions)
            if pause:
                task["status"] = "paused"
                self.stores.put("tasks", task)
                return {"new_results": new_results, "paused": True}
            if any(node.get("replan_after") or node.get("suggestions") for node in wave):
                return {"new_results": new_results, "decision_required": True}
