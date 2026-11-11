"""LangGraph fan-out scheduling over the persisted execution Task DAG."""

from enterprise_agent.domain.errors import (
    UpstreamPending,
    VersionConflict,
    public_message,
)
from enterprise_agent.ledger.ledger import now
from enterprise_agent.settings import MAX_PARALLEL


def dispatch(self, state):
    by_id = {
        node["task_id"]: node for node in self.stores.session_tasks(state["session_id"])
    }
    ready_by_owner = {key: [] for key in state["scopes"]}
    errors, changed = dict(state["scope_errors"]), False
    for key, scope in state["scopes"].items():
        if key in state["stopped"] or key in errors:
            continue
        for node in by_id.values():
            if node["request_id"] != key or node["status"] not in (
                "pending",
                "running",
            ):
                continue
            try:
                dependencies = [by_id.get(tid) for tid in node["depends_on"]]
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
                        expected_revisions={key: scope["revision_id"]},
                    )
                    changed = True
                    continue
                for binding in node["input_bindings"]:
                    if binding["source"]["kind"] == "request":
                        if binding["source"]["id"] in state["stopped"]:
                            raise UpstreamPending("上游正在重规划或等待继续")
                        self.context.requests.resolve(
                            state["session_id"], binding["source"]
                        )
                if all(dep["status"] == "completed" for dep in dependencies):
                    ready_by_owner[key].append(node["task_id"])
            except UpstreamPending:
                continue
            except Exception as exc:
                errors[key] = failure(exc)
                ready_by_owner[key] = []
                break
    # Round-robin selection prevents one large Request occupying every slot.
    ready = []
    while len(ready) < MAX_PARALLEL and any(ready_by_owner.values()):
        for values in ready_by_owner.values():
            if values and len(ready) < MAX_PARALLEL:
                ready.append(values.pop(0))
    return {
        "ready": ready,
        "scope_errors": errors,
        "ready_scopes": {
            tid: state["scopes"][by_id[tid]["request_id"]] for tid in ready
        },
        "retry_dispatch": changed and not ready,
    }


def execute(self, state):
    try:
        return execute_task(self, state)
    except (VersionConflict, PermissionError) as exc:
        # Do not overwrite stale records; isolate this owner from the other Tasks.
        return {
            "results": {
                state["task_id"]: {
                    "task_id": state["task_id"],
                    "status": "failed",
                    "scope_failure": failure(exc),
                    "summary": public_message(exc),
                }
            }
        }


def failure(exc):
    kind = (
        "version_conflict"
        if isinstance(exc, VersionConflict)
        else (
            "permission_denied"
            if isinstance(exc, PermissionError)
            else "execution_failed"
        )
    )
    return {"kind": kind, "message": public_message(exc)}


def execute_task(self, state):
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
    stopped = set(state["stopped"])
    errors = dict(state["scope_errors"])
    for key in state["ready"]:
        scope = state["ready_scopes"][key]
        result = state["results"][key]
        if result.get("scope_failure"):
            errors[scope["request_id"]] = result["scope_failure"]
        if (
            scope["request_id"] in errors
            or scope["pause"]
            or result.get("replan_after")
            or result.get("suggestions")
        ):
            stopped.add(scope["request_id"])
    return {"stopped": list(stopped), "scope_errors": errors, "route": "dispatch"}
