"""Audit history, feedback and administrator user management."""

from fastapi import APIRouter, Depends

from enterprise_agent.server.audit import list_audit
from enterprise_agent.server.auth import app_db, current_user
from enterprise_agent.server.schemas import Feedback

router = APIRouter()


@router.get("/history")
def history(user=Depends(current_user)):
    return list_audit(user)


@router.post("/feedback")
def feedback(body: Feedback, user=Depends(current_user)):
    with app_db() as db:
        if body.audit_id:
            row = db.execute(
                "SELECT user_id FROM audit WHERE id=%s", (body.audit_id,)
            ).fetchone()
            if not row or row["user_id"] != user["id"]:
                raise PermissionError("无权评价此记录")
        cursor = db.execute(
            "INSERT INTO feedback(at,user_id,audit_id,rating,comment) VALUES(now(),%s,%s,%s,%s) RETURNING id",
            (user["id"], body.audit_id, body.rating, body.comment),
        )
        return {"id": cursor.fetchone()["id"], "ok": True}
