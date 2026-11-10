"""Session, Request and Task read/write endpoints."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.auth import current_user, require
from enterprise_agent.server.schemas import Resume

router = APIRouter()


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
