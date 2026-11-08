"""Five stage-specific views over the same four stores. No business routing rules."""
from ..agents.capabilities import CAPABILITIES
from ..core.config import RECENT_EVENTS


class ContextManager:
    def __init__(self, stores, artifacts, tasks, registry):
        self.stores, self.artifacts, self.tasks, self.registry = stores, artifacts, tasks, registry

    def session_artifacts(self, session_id):
        return [item for task in self.stores.list("tasks", "session_id", session_id)
                for item in self.stores.list("artifacts", "task_id", task["task_id"])]

    def initial(self, session_id, query):
        session = self.stores.get("sessions", session_id)
        events = self.stores.list("session_events", "session_id", session_id)
        messages = [event for event in events if event["event_type"] in ("user_message", "assistant_response")][-RECENT_EVENTS:]
        summaries = self.stores.list("session_summaries", "session_id", session_id)
        return {"view": "conversation", "stage": "initial", "query": query,
            "session_hints": {key: session.get(key) for key in ("last_active_task_id", "last_response_event_id")},
            "recent_messages": [{"event_id": event["event_id"], "event_type": event["event_type"], "payload": event["payload"]} for event in messages],
            "candidate_tasks": [{key: task[key] for key in ("task_id", "goal", "status")} for task in self.stores.list("tasks", "session_id", session_id)[-12:]],
            "candidate_artifacts": [self.overview(item) for item in self.session_artifacts(session_id)[-16:]],
            "history_summary": summaries[-1] if summaries else None}

    @staticmethod
    def overview(item):
        return {key: item[key] for key in ("artifact_id", "type", "title", "summary", "source_refs", "content_ref", "payload")}

    def planning(self, task, intent):
        revision = self.stores.get("task_revisions", task["current_revision_id"])
        return {"view": "execution", "stage": "planning", "task": task, "revision": revision,
            "intent": intent, "subtasks": self.stores.list("subtasks", "task_id", task["task_id"]),
            "existing_results": [self.overview(item) for item in self.session_artifacts(task["origin_session_id"])][-30:],
            "available_capabilities": CAPABILITIES, "semantic_registry": self.registry.overview()}

    def subtask(self, task, node):
        inputs = [self.tasks.resolve(task, binding) for binding in node["input_bindings"]]
        allowed = set()
        for item in inputs:
            ids = [item["artifact_id"]] if item.get("artifact_id") else item.get("artifact_ids", [])
            allowed.update(ids) 
            item["artifacts"] = [self.overview(self.artifacts.inspect(key)) for key in ids]
            for artifact in item["artifacts"]:
                if artifact["type"] == "query_result" and artifact["payload"]["row_count"] <= 20:
                    artifact["exact_data"] = self.artifacts.read(artifact["artifact_id"], {"limit": 20})
        # Chart/report modification must be able to read the actual source data.
        pending = list(allowed) # 后续所有用到的直接artifact_ids
        while pending:
            for ref in self.artifacts.inspect(pending.pop())["source_refs"]:
                if ref["kind"] == "artifact" and ref["id"] not in allowed:
                    allowed.add(ref["id"])
                    pending.append(ref["id"]) # 关联的artifact_ids加进去
        revision = self.stores.get("task_revisions", node["revision_id"])
        return {"view": "execution", "stage": "subtask", "subtask": node,
            "constraints": revision["constraints"],
            "requirements": [r for r in revision["requirements"] if r["request_id"] in node["request_ids"]],
            "inputs": inputs}, allowed

    def replanning(self, task, intent, new_results):
        context = self.planning(task, intent)
        context.update(stage="replanning", new_results=new_results)
        context["unfulfilled_requests"] = self.unfulfilled(task)
        return context

    def unfulfilled(self, task):
        revision = self.stores.get("task_revisions", task["current_revision_id"])
        nodes = self.stores.list("subtasks", "task_id", task["task_id"])
        return [{**request, "subtask_statuses": [{"subtask_id": node["subtask_id"], "status": node["status"]}
                 for node in nodes if request["request_id"] in node["request_ids"] and node["status"] != "superseded"],
                 "execution_notes": [node.get("summary", "") for node in nodes if request["request_id"] in node["request_ids"] and node["status"] not in ("superseded", "cancelled")]}
                for request in revision["requirements"]]

    def answer(self, task, query, completion=None):
        revision = self.stores.get("task_revisions", task["current_revision_id"])
        ids = {ref["id"] for node in self.stores.list("subtasks", "task_id", task["task_id"])
               if node["status"] == "completed" for ref in node["produced_outputs"].values() if ref["kind"] == "artifact"}
        results = [self.overview(self.artifacts.inspect(key)) for key in sorted(ids)]
        for item in results:
            if item["type"] == "query_result" and item["payload"]["row_count"] <= 20:
                item["exact_data"] = self.artifacts.read(item["artifact_id"], {"limit": 20})
        return {"view": "execution", "stage": "answer", "query": query, "task": task,
                "revision": revision, "requirements": self.unfulfilled(task), "completion": completion,
                "results": results,
                "semantic_results": [node["semantic_binding"] for node in self.stores.list("subtasks", "task_id", task["task_id"]) if node.get("semantic_binding") and node["status"] not in ("superseded", "cancelled")]}
