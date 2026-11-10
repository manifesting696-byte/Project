"""Audit history, feedback and administrator user management."""

from fastapi import APIRouter, Depends
from psycopg import IntegrityError

from enterprise_agent.server.auth import app_db, create_user, require
from enterprise_agent.server.schemas import NewUser

router = APIRouter()


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
