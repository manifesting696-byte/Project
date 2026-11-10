"""Audit history, feedback and administrator user management."""

from fastapi import APIRouter, Depends
from psycopg import IntegrityError

from enterprise_agent.server.identity import (
    app_db,
    create_user,
    current_user,
    list_audit,
    require,
)
from enterprise_agent.server.schemas import Feedback, NewUser

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


@router.get("/admin/users")
def users(user=Depends(require("admin"))):
    with app_db() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,username,role,active,created_at FROM users ORDER BY id"
            )
        ]


@router.post("/admin/users")
def add_user(body: NewUser, user=Depends(require("admin"))):
    try:
        return {
            "id": create_user(body.username, body.password, body.role),
            "username": body.username,
        }
    except IntegrityError as exc:
        raise ValueError("账号已存在") from exc


@router.post("/admin/users/{user_id}/deactivate")
def deactivate(user_id: int, user=Depends(require("admin"))):
    if user_id == user["id"]:
        raise ValueError("不能停用自己")
    with app_db() as db:
        db.execute("UPDATE users SET active=FALSE WHERE id=%s", (user_id,))
        db.execute("DELETE FROM auth_sessions WHERE user_id=%s", (user_id,))
    return {"ok": True}
