"""Stage-specific context over the request ledger and managed artifact contents."""

from enterprise_agent.settings import RECENT_EVENTS
from enterprise_agent.stages.execute.agents.capabilities import CAPABILITIES


class ContextManager:
    def __init__(self, stores, artifacts, requests, tasks, registry, capabilities=None):
        self.stores, self.artifacts = stores, artifacts
        self.requests, self.tasks, self.registry = requests, tasks, registry
        self.capabilities = capabilities if capabilities is not None else CAPABILITIES

    @staticmethod
    def overview(item):
        return {
            key: item.get(key)
            for key in (
                "artifact_id",
                "content_type",
                "request_id",
                "title",
                "summary",
                "source_artifact_ids",
                "session_id",
                "revision_id",
                "task_id",
                "producer",
                "created_by_turn_id",
                "content_ref",
                "payload",
            )
        }

    def session_artifacts(self, session_id):
        return self.stores.list("artifacts", "session_id", session_id)

    def initial(self, session_id, query):
        session = self.stores.get("sessions", session_id)
        events = self.stores.list("session_events", "session_id", session_id)
        messages = [
            event
            for event in events
            if event["event_type"] in ("user_message", "assistant_response")
        ]
        summaries = self.stores.list("session_summaries", "session_id", session_id)
        requests = self.stores.list("requests", "session_id", session_id)
        return {
            "stage": "initial",
            "query": query,
            "session_hints": {
                key: session.get(key)
                for key in ("last_active_request_id", "last_response_event_id")
            },
            "recent_messages": [
                {
                    "event_id": item["event_id"],
                    "event_type": item["event_type"],
                    "payload": item["payload"],
                }
                for item in messages[-RECENT_EVENTS:]
            ],
            "candidate_requests": [
                {
                    key: item[key]
                    for key in (
                        "request_id",
                        "title",
                        "status",
                        "current_revision_id",
                        "spec",
                    )
                }
                for item in requests[-12:]
            ],
            "candidate_artifacts": [
                self.overview(item) for item in self.session_artifacts(session_id)[-16:]
            ],
            "history_summary": summaries[-1] if summaries else None,
            "database_metadata": self.registry.overview(),
            "available_capabilities": dict(self.capabilities),
        }

    def preparation(
        self,
        session_id,
        operation,
        previous,
        parameters,
        drafts=None,
        required_outputs=None,
        feedback=None,
    ):
        request = (
            self.stores.get("requests", operation.request_id) if previous else None
        )
        return {
            "stage": "preparation",
            "request_id": operation.request_id,
            "existing_tasks": self.stores.list(
                "tasks", "request_id", operation.request_id
            )
            if request
            else [],
            "deliveries": self.requests.outputs(request) if request else {},
            "output_bindings": self.requests.plan_bindings(request) if request else {},
            "validation_feedback": feedback,
            "upstream_requests": list((drafts or {}).values()),
            "available_capabilities": dict(self.capabilities),
            "operation": operation.model_dump(exclude_none=True),
            "previous_revision": previous,
            "supplied_parameters": parameters,
            "required_output_names": required_outputs or [],
            "referenced_inputs": self.ref_views(
                session_id,
                [ref.model_dump(exclude_none=True) for ref in operation.refs],
                drafts,
            ),
            "database_metadata": self.registry.overview(),
        }

    def ref_views(self, session_id, refs, drafts=None):
        values = []
        for ref in refs:
            self.requests.validate_ref(session_id, ref, set(drafts or {}))
            if ref["kind"] == "request":
                item = (drafts or {}).get(ref["id"]) or self.stores.get(
                    "requests", ref["id"]
                )
                values.append(
                    {
                        "ref": ref,
                        "request": item,
                        "deliveries": {}
                        if ref["id"] in (drafts or {})
                        else self.requests.outputs(item),
                    }
                )
            elif ref["kind"] == "artifact":
                values.append(
                    {
                        "ref": ref,
                        "artifact": self.overview(self.artifacts.inspect(ref["id"])),
                    }
                )
            elif ref["kind"] == "task":
                values.append({"ref": ref, "task": self.stores.get("tasks", ref["id"])})
            else:
                values.append({"ref": ref, **self.requests.resolve(session_id, ref)})
        return values

    def planning(self, request):
        return {
            "stage": "planning",
            "request": request,
            "spec": request["spec"],
            "deliveries": self.requests.outputs(request),
            "tasks": self.stores.list("tasks", "request_id", request["request_id"]),
            "output_bindings": self.requests.plan_bindings(request),
            "existing_results": [
                self.overview(item)
                for item in self.session_artifacts(request["session_id"])[-30:]
            ],
            "available_capabilities": dict(self.capabilities),
            "semantic_registry": self.registry.overview(),
        }

    def task(self, node):
        request = self.stores.get("requests", node["request_id"])
        inputs = []
        for binding in node["input_bindings"]:
            value = {
                "slot": binding["slot"],
                **self.requests.resolve(request["session_id"], binding["source"]),
            }
            if value.get("artifact_id"):
                artifact = self.overview(self.artifacts.inspect(value["artifact_id"]))
                if (
                    artifact["content_type"] == "table"
                    and artifact["payload"]["row_count"] <= 20
                ):
                    artifact["exact_data"] = self.artifacts.read(
                        value["artifact_id"], {"limit": 20}
                    )
                value["artifact"] = artifact
            inputs.append(value)
        allowed = self.requests.artifact_scope(
            [binding["source"] for binding in node["input_bindings"]]
        )
        revision = self.stores.get("request_revisions", node["revision_id"])
        return {
            "stage": "task",
            "task": node,
            "request": request,
            "spec": revision["spec"],
            "parameters": revision["spec"]["parameters"],
            "inputs": inputs,
        }, allowed

    def replanning(self, request, new_results):
        context = self.planning(request)
        context.update(stage="replanning", new_results=new_results)
        return context

    def answer(self, request, query, completion=None, output_names=None):
        refs = [
            ref
            for name, ref in self.requests.outputs(request).items()
            if output_names is None or name in output_names
        ]
        artifact_ids = self.requests.artifact_scope(refs)
        results = [
            self.overview(self.artifacts.inspect(key)) for key in sorted(artifact_ids)
        ]
        for item in results:
            if item["content_type"] == "table" and item["payload"]["row_count"] <= 20:
                item["exact_data"] = self.artifacts.read(
                    item["artifact_id"], {"limit": 20}
                )
        return {
            "stage": "answer",
            "query": query,
            "scope_request_ids": [request["request_id"]],
            "request": request,
            "spec": request["spec"],
            "deliveries": self.requests.outputs(request),
            "completion": completion,
            "results": results,
            "response_output_names": output_names,
            "semantic_results": [
                node["semantic_binding"]
                for node in self.stores.list(
                    "tasks", "request_id", request["request_id"]
                )
                if node.get("semantic_binding")
                and node["status"] not in ("superseded", "cancelled")
            ],
        }
