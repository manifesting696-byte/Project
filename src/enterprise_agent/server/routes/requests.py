"""Session, Request and Task read/write endpoints."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.identity import current_user, require
from enterprise_agent.server.schemas import Resume

router = APIRouter()


@router.get("/sessions")
def sessions(user=Depends(current_user)):
    return deps.application().stores.list("sessions", "user_id", user["id"])


@router.get("/sessions/{session_id}/events")
def events(session_id: str, user=Depends(current_user)):
    engine = deps.application()
    engine.stores.session(user["id"], session_id)
    return engine.stores.list("session_events", "session_id", session_id)


@router.post("/sessions/{session_id}/turns/{turn_id}/resume")
def resume_turn(
    session_id: str, turn_id: str, user=Depends(require("admin", "analyst"))
):
    return deps.application().resume_turn(user["id"], session_id, turn_id)


@router.get("/tasks/{task_id}")
def task(task_id: str, user=Depends(current_user)):
    engine = deps.application()
    if engine.stores.exists("tasks", task_id):
        record = engine.stores.get("tasks", task_id)
        owner = engine.stores.owned_request(user["id"], record["request_id"])
        return {
            "task": record,
            "request": owner,
            "revision": engine.stores.get("request_revisions", record["revision_id"]),
        }
    legacy_ids = engine.stores.legacy_request_ids(task_id)
    if not legacy_ids:
        raise ValueError("执行 Task 不存在")
    owners = [engine.stores.owned_request(user["id"], key) for key in legacy_ids]
    return engine.snapshot(owners[0]["session_id"], request_ids=legacy_ids)


@router.get("/requests")
def requests(session_id: str, user=Depends(current_user)):
    engine = deps.application()
    engine.stores.session(user["id"], session_id)
    return engine.stores.list("requests", "session_id", session_id)


@router.get("/requests/{request_id}")
def request_state(request_id: str, user=Depends(current_user)):
    engine = deps.application()
    record = engine.stores.owned_request(user["id"], request_id)
    return engine.snapshot(record["session_id"], request_ids=[request_id])


@router.get("/requests/{request_id}/revisions")
def request_revisions(request_id: str, user=Depends(current_user)):
    engine = deps.application()
    engine.stores.owned_request(user["id"], request_id)
    return engine.stores.list("request_revisions", "request_id", request_id)


@router.post("/requests/{request_id}/resume")
def resume_request(
    request_id: str, body: Resume, user=Depends(require("admin", "analyst"))
):
    return deps.application().resume(
        user["id"], request_id, body.continue_analysis, body.answer
    )


@router.post("/tasks/{task_id}/resume")
def resume(task_id: str, body: Resume, user=Depends(require("admin", "analyst"))):
    engine = deps.application()
    if engine.stores.exists("tasks", task_id):
        request_ids = [engine.stores.get("tasks", task_id)["request_id"]]
    else:
        request_ids = engine.stores.legacy_request_ids(task_id)
    if not request_ids:
        raise ValueError("执行 Task 不存在")
    for key in request_ids:
        engine.stores.owned_request(user["id"], key)
    results = [
        engine.resume(user["id"], key, body.continue_analysis, body.answer)
        for key in request_ids
    ]
    last = results[-1]
    return {
        **engine.snapshot(
            last["session_id"], last["turn_id"], request_ids, last["status"]
        ),
        "blocks": [block for result in results for block in result["blocks"]],
    }
