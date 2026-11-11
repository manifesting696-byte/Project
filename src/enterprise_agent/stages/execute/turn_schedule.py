"""Cooperative Request control, unified Task execution for the current turn.

Request graphs stop before execute; no Request worker pool runs private schedulers.
Every dispatch round uses one shared Task graph and one MAX_PARALLEL budget.
"""

from enterprise_agent.domain.errors import VersionConflict
from enterprise_agent.stages.execute.operations import visible_outputs


def process_turn(runner, state):
    from enterprise_agent.stages.execute.graph_node import process_one

    app = runner.app
    turn_id, session_id = state["turn_id"], state["session_id"]
    remaining = {
        op["request_id"]: (index, op)
        for index, op in enumerate(state["ordered"])
        if index >= state["cursor"]
    }
    working = dict(state)

    def finish(key, resolved=None, failure=None):
        index, _ = remaining[key]
        update = process_one(runner, {**working, "cursor": index}, resolved, failure)
        working.update(update)
        del remaining[key]

    def waiting_on_current_turn(request):
        return any(
            binding["source"]["kind"] == "request"
            and binding["source"]["id"] in remaining
            for binding in request["spec"]["inputs"]
        )

    def advance(key, operation, outcome=None, snapshot=None):
        request = app.stores.get("requests", key)
        if (
            request["current_revision_id"]
            != state["prepared"][key]["current_revision_id"]
        ):
            raise VersionConflict("该轮对应的需求版本已失效，请使用当前版本")
        supplied = (
            None
            if outcome is None
            else {
                "key": app.execution.key(snapshot.values, "schedule"),
                "outcome": outcome,
            }
        )
        scene = app.execution.advance(
            request,
            turn_id,
            operation["instruction"],
            state["pause"],
            visible_outputs(request, state["previous"].get(key)),
            state["preview"],
            supplied,
        )
        if not scene.next or any(task.interrupts for task in scene.tasks):
            answer, status = app.execution.result(request, scene.values)
            finish(key, (request, answer, status))
            return None
        if scene.next != ("execute",):
            raise RuntimeError("执行图没有停在 Task 调度边界")
        return scene

    while remaining:
        barriers = {}
        completed_before = len(remaining)
        for key, (_, operation) in list(remaining.items()):
            try:
                cached = app.stores.workflow_event(
                    session_id, f"{turn_id}:operation:{key}"
                )
                if (
                    cached
                    or key in state["errors"]
                    or state["proposals"][key]["clarifications"]
                ):
                    finish(key)
                    continue
                request = app.stores.get("requests", key)
                if operation["action"] == "none":
                    if not app.execution.upstream_ready(
                        request
                    ) and waiting_on_current_turn(request):
                        continue
                    finish(key)
                    continue
                scene = advance(key, operation)
                if scene:
                    barriers[key] = scene
            except Exception as exc:
                finish(key, failure=exc)

        if not barriers:
            if remaining and len(remaining) == completed_before:
                # Only unresolved direct answers remain; publish their upstream waits.
                for key in list(remaining):
                    finish(key)
            continue

        scopes = [
            {
                "request_id": key,
                "revision_id": scene.values["revision_id"],
                "execution_key": app.execution.key(scene.values, "schedule"),
                "pause": scene.values["pause"],
            }
            for key, scene in barriers.items()
        ]
        outcomes = app.scheduler.run_many(session_id, turn_id, scopes)
        for key, scene in barriers.items():
            try:
                outcome = outcomes[key]
                app.scheduler.ensure_success(outcome)
                if outcome["waiting_for_requests"]:
                    request = app.stores.get("requests", key)
                    if waiting_on_current_turn(request) or app.execution.upstream_ready(
                        request
                    ):
                        # The upstream control graph may still need to materialize text
                        # or add tasks. Keep this checkpoint at the same execution key.
                        continue
                advance(key, remaining[key][1], outcome, scene)
            except Exception as exc:
                finish(key, failure=exc)

    touched = set(working["touched"])
    return {
        "answers": working["answers"],
        "states": working["states"],
        "touched": [
            op["request_id"] for op in state["ordered"] if op["request_id"] in touched
        ],
        "cursor": len(state["ordered"]),
    }
