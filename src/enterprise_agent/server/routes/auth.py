"""Login, logout, identity and password endpoints."""

from fastapi import APIRouter, Depends, Header

from enterprise_agent.server.identity import (
    change_password,
    current_user,
    login,
    logout,
)
from enterprise_agent.server.schemas import Credentials, PasswordChange

router = APIRouter()


@router.post("/auth/login")
def sign_in(body: Credentials):
    return login(body.username, body.password)


@router.get("/auth/me")
def me(user=Depends(current_user)):
    return user


@router.post("/auth/logout")
def sign_out(
    authorization: str | None = Header(default=None), user=Depends(current_user)
):
    logout(authorization)
    return {"ok": True}


@router.post("/auth/password")
def password(body: PasswordChange, user=Depends(current_user)):
    change_password(user["id"], body.old_password, body.new_password)
    return {"ok": True}
