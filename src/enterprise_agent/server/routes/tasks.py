"""Session, Request and Task read/write endpoints."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.auth import current_user, require
from enterprise_agent.server.schemas import Resume

router = APIRouter()


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
    archived_ids = engine.stores.archived_request_ids(task_id)
    if not archived_ids:
        raise ValueError("执行 Task 不存在")
    owners = [engine.stores.owned_request(user["id"], key) for key in archived_ids]
    return engine.snapshot(owners[0]["session_id"], request_ids=archived_ids)


@router.post("/tasks/{task_id}/resume")
def resume(task_id: str, body: Resume, user=Depends(require("admin", "analyst"))):
    engine = deps.application()
    if engine.stores.exists("tasks", task_id):
        request_ids = [engine.stores.get("tasks", task_id)["request_id"]]
    else:
        request_ids = engine.stores.archived_request_ids(task_id)
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
