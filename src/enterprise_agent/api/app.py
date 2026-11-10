"""HTTP/authentication adapter. Business execution lives in application.Application."""

from psycopg import IntegrityError, OperationalError
from psycopg_pool import PoolTimeout
from fastapi import HTTPException
from contextlib import asynccontextmanager
from functools import lru_cache
from fastapi import Depends, FastAPI, Header
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from ..application.service import Application
from .identity import (
    setup,
    current_user,
    require,
    login,
    logout,
    change_password,
    create_user,
    app_db,
    audit,
    list_audit,
)


@lru_cache(maxsize=1)
def application():
    return Application()


@asynccontextmanager
async def lifespan(app):
    setup()
    try:
        yield
    finally:
        if hasattr(application, "cache_info") and application.cache_info().currsize:
            try:
                application().close()
            finally:
                application.cache_clear()
        from .identity import close

        close()


app = FastAPI(title="企业数据智能问析", version="3.0.0", lifespan=lifespan)
UI = __import__("pathlib").Path(__file__).resolve().parents[1] / "ui"


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    session_id: str | None = None
    constraints: dict = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    pause_after_batch: bool = False


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(Credentials):
    role: str = "analyst"


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class Resume(BaseModel):
    continue_analysis: bool = True
    answer: str | None = Field(default=None, min_length=1, max_length=4000)


class Feedback(BaseModel):
    audit_id: int | None = None
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class PluginLoad(BaseModel):
    id: str = Field(min_length=1)
    plugin: str | None = None
    config: dict = Field(default_factory=dict)


class PluginUnload(BaseModel):
    cascade: bool = False


class PluginReload(BaseModel):
    config: dict | None = None


class ProfileReload(BaseModel):
    profile: str | None = None


class TableRelation(BaseModel):
    from_field: str = Field(alias="from", min_length=1)
    to_field: str = Field(alias="to", min_length=1)


class TableEnrollment(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    column_meanings: dict[str, str] | None = None
    relations: list[TableRelation] | None = None
    expected_version: str | None = None


class CatalogRefresh(BaseModel):
    expected_version: str | None = None


@app.exception_handler(PermissionError)
async def forbidden(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(UI / "index.html")


@app.get("/app.js", include_in_schema=False)
def javascript():
    return FileResponse(UI / "app.js", media_type="text/javascript")


@app.get("/app.css", include_in_schema=False)
def css():
    return FileResponse(UI / "app.css", media_type="text/css")


@app.get("/chat.css", include_in_schema=False)
def chat_css():
    return FileResponse(UI / "chat.css", media_type="text/css")


@app.get("/health")
def health():
    engine = application()
    try:
        with engine.stores.db() as db:
            db.execute("SELECT 1")
        with engine.database.connection() as db:
            db.execute("SELECT 1")
    except (OperationalError, PoolTimeout) as exc:
        raise HTTPException(503, "PostgreSQL 暂不可用") from exc
    return {
        "status": "ok",
        "persistence": "postgresql",
        "snapshot_id": engine.registry.snapshot_id,
        "agents": len(engine.specialists.capabilities),
        "profile": engine.profile,
    }


@app.get("/runtime/plugins")
def plugins(user=Depends(require("admin"))):
    engine = application()
    return {**engine.runtime.inventory(), "profile": engine.profile}


@app.post("/runtime/plugins/load")
def load_plugin(body: PluginLoad, user=Depends(require("admin"))):
    return application().runtime.load(body.model_dump())


@app.post("/runtime/plugins/{plugin_id}/unload")
def unload_plugin(plugin_id: str, body: PluginUnload, user=Depends(require("admin"))):
    return application().runtime.unload(plugin_id, body.cascade)


@app.post("/runtime/plugins/{plugin_id}/reload")
def reload_plugin(plugin_id: str, body: PluginReload, user=Depends(require("admin"))):
    return application().runtime.reload(plugin_id, body.config)


@app.post("/runtime/profile/reload")
def reload_profile(body: ProfileReload, user=Depends(require("admin"))):
    return application().reload_profile(body.profile)


@app.post("/auth/login")
def sign_in(body: Credentials):
    return login(body.username, body.password)


@app.get("/auth/me")
def me(user=Depends(current_user)):
    return user


@app.post("/auth/logout")
def sign_out(
    authorization: str | None = Header(default=None), user=Depends(current_user)
):
    logout(authorization)
    return {"ok": True}


@app.post("/auth/password")
def password(body: PasswordChange, user=Depends(current_user)):
    change_password(user["id"], body.old_password, body.new_password)
    return {"ok": True}


@app.get("/catalog")
def catalog(user=Depends(current_user)):
    registry = application().registry
    return {**registry.catalog, "published_metrics": registry.published("metrics")}


@app.get("/tables")
def tables(user=Depends(current_user)):
    registry = application().registry
    return {
        "snapshot_id": registry.snapshot_id,
        "catalog_version": registry.catalog_version,
        "total_rows": sum(entry["rows"] for entry in registry.tables.values()),
        "tables": [
            {
                "name": name,
                "rows": entry["rows"],
                "columns": [
                    {"name": col, **definition}
                    for col, definition in entry["columns"].items()
                ],
            }
            for name, entry in registry.tables.items()
        ],
    }


@app.get("/catalog/tables/available")
def available_tables(user=Depends(require("admin"))):
    return application().available_tables()


@app.post("/catalog/tables")
def enroll_table(body: TableEnrollment, user=Depends(require("admin"))):
    options = body.model_dump(exclude={"relations"})
    options["relations"] = (
        [relation.model_dump(by_alias=True) for relation in body.relations]
        if body.relations is not None
        else None
    )
    return application().change_tables("enroll", **options)


@app.delete("/catalog/tables/{table_name}")
def remove_table(
    table_name: str, expected_version: str | None = None, user=Depends(require("admin"))
):
    return application().change_tables(
        "remove", name=table_name, expected_version=expected_version
    )


@app.post("/catalog/tables/refresh")
def refresh_tables(body: CatalogRefresh, user=Depends(require("admin"))):
    return application().change_tables(
        "refresh", expected_version=body.expected_version
    )


def execute_question(body, user, preview=False):
    constraints = dict(body.constraints)
    for key in ("start", "end"):
        if key in body.model_fields_set:
            constraints[key] = getattr(body, key)
    result = application().ask(
        user["id"],
        body.question,
        body.session_id,
        constraints,
        body.pause_after_batch,
        preview,
    )
    audit_id = audit(
        user["id"],
        "ask",
        result["status"],
        question=body.question,
        snapshot_id=result["snapshot_id"],
        metric_version=result["semantic_version"],
    )
    return {**result, "audit_id": audit_id}


@app.post("/ask")
def ask(body: Question, user=Depends(require("admin", "analyst"))):
    return execute_question(body, user)


@app.post("/bind")
def preview(body: Question, user=Depends(require("admin", "analyst"))):
    return execute_question(body, user, preview=True)


@app.get("/sessions")
def sessions(user=Depends(current_user)):
    return application().stores.list("sessions", "user_id", user["id"])


@app.get("/sessions/{session_id}/events")
def events(session_id: str, user=Depends(current_user)):
    engine = application()
    engine.stores.session(user["id"], session_id)
    return engine.stores.list("session_events", "session_id", session_id)


@app.post("/sessions/{session_id}/turns/{turn_id}/resume")
def resume_turn(
    session_id: str, turn_id: str, user=Depends(require("admin", "analyst"))
):
    return application().resume_turn(user["id"], session_id, turn_id)


@app.get("/tasks/{task_id}")
def task(task_id: str, user=Depends(current_user)):
    engine = application()
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


@app.get("/requests")
def requests(session_id: str, user=Depends(current_user)):
    engine = application()
    engine.stores.session(user["id"], session_id)
    return engine.stores.list("requests", "session_id", session_id)


@app.get("/requests/{request_id}")
def request_state(request_id: str, user=Depends(current_user)):
    engine = application()
    record = engine.stores.owned_request(user["id"], request_id)
    return engine.snapshot(record["session_id"], request_ids=[request_id])


@app.get("/requests/{request_id}/revisions")
def request_revisions(request_id: str, user=Depends(current_user)):
    engine = application()
    engine.stores.owned_request(user["id"], request_id)
    return engine.stores.list("request_revisions", "request_id", request_id)


@app.post("/requests/{request_id}/resume")
def resume_request(
    request_id: str, body: Resume, user=Depends(require("admin", "analyst"))
):
    return application().resume(
        user["id"], request_id, body.continue_analysis, body.answer
    )


@app.post("/tasks/{task_id}/resume")
def resume(task_id: str, body: Resume, user=Depends(require("admin", "analyst"))):
    engine = application()
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


def authorize_artifact(artifact_id, user):
    engine = application()
    item = engine.artifacts.inspect(artifact_id)
    engine.stores.session(user["id"], item["session_id"])
    return engine, item


@app.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    return item


@app.get("/artifacts/{artifact_id}/content")
def content(
    artifact_id: str, offset: int = 0, limit: int = 100, user=Depends(current_user)
):
    engine, item = authorize_artifact(artifact_id, user)
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("无效分页范围")
    return (
        engine.artifacts.read(artifact_id, {"offset": offset, "limit": limit})
        if item["content_type"] == "table"
        else {"content": engine.artifacts.content(artifact_id)}
    )


@app.get("/artifacts/{artifact_id}/sql")
def sql(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    return {
        "sql": engine.artifacts.sql(artifact_id),
        "semantic_binding": item["payload"].get("semantic_binding"),
        "provenance": item["payload"].get("provenance"),
        "source_artifact_ids": item["source_artifact_ids"],
    }


@app.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    if item["content_type"] not in ("text", "report"):
        raise ValueError("该产物不是 Markdown 文档")
    return Response(
        engine.artifacts.content(artifact_id),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}.md"'},
    )


@app.get("/history")
def history(user=Depends(current_user)):
    return list_audit(user)


@app.post("/feedback")
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


@app.get("/admin/users")
def users(user=Depends(require("admin"))):
    with app_db() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,username,role,active,created_at FROM users ORDER BY id"
            )
        ]


@app.post("/admin/users")
def add_user(body: NewUser, user=Depends(require("admin"))):
    try:
        return {
            "id": create_user(body.username, body.password, body.role),
            "username": body.username,
        }
    except IntegrityError as exc:
        raise ValueError("账号已存在") from exc


@app.post("/admin/users/{user_id}/deactivate")
def deactivate(user_id: int, user=Depends(require("admin"))):
    if user_id == user["id"]:
        raise ValueError("不能停用自己")
    with app_db() as db:
        db.execute("UPDATE users SET active=FALSE WHERE id=%s", (user_id,))
        db.execute("DELETE FROM auth_sessions WHERE user_id=%s", (user_id,))
    return {"ok": True}
