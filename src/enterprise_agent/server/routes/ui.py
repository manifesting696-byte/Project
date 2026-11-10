"""Static workspace assets and the health probe."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from enterprise_agent.server import deps

router = APIRouter()


@router.get("/", include_in_schema=False)
def index():
    return FileResponse(deps.STATIC / "index.html")


@router.get("/app.js", include_in_schema=False)
def javascript():
    return FileResponse(deps.STATIC / "app.js", media_type="text/javascript")


@router.get("/app.css", include_in_schema=False)
def css():
    return FileResponse(deps.STATIC / "app.css", media_type="text/css")


@router.get("/chat.css", include_in_schema=False)
def chat_css():
    return FileResponse(deps.STATIC / "chat.css", media_type="text/css")


@router.get("/health")
def health():
    engine = deps.application()
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
