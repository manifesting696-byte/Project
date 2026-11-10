"""Session, Request and Task read/write endpoints."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.auth import current_user, require

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
