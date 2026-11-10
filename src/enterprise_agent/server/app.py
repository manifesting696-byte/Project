"""FastAPI transport assembly. See flow/FLOW.md for business execution."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from enterprise_agent.domain.errors import (
    AgentError,
    PermissionDenied,
    UpstreamPending,
    VersionConflict,
    public_message,
)
from enterprise_agent.server.auth import close as close_identity
from enterprise_agent.server.auth import setup
from enterprise_agent.server.deps import close_application
from enterprise_agent.server.routes import (
    admin,
    artifacts,
    ask,
    auth,
    catalog,
    feedback,
    plugins,
    requests,
    sessions,
    tasks,
    ui,
)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
}


@asynccontextmanager
async def lifespan(app):
    setup()
    try:
        yield
    finally:
        close_application()
        close_identity()


async def forbidden(request, exc):
    return JSONResponse(status_code=403, content={"detail": public_message(exc)})


async def invalid(request, exc):
    return JSONResponse(status_code=400, content={"detail": public_message(exc)})


async def conflict(request, exc):
    return JSONResponse(status_code=409, content={"detail": public_message(exc)})


async def headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app():
    app = FastAPI(title="企业数据智能问析", version="3.0.0", lifespan=lifespan)
    app.add_exception_handler(PermissionError, forbidden)
    app.add_exception_handler(PermissionDenied, forbidden)
    app.add_exception_handler(ValueError, invalid)
    app.add_exception_handler(AgentError, invalid)
    app.add_exception_handler(VersionConflict, conflict)
    app.add_exception_handler(UpstreamPending, conflict)
    app.middleware("http")(headers)
    for module in (
        ui,
        auth,
        ask,
        sessions,
        tasks,
        requests,
        artifacts,
        catalog,
        feedback,
        admin,
        plugins,
    ):
        app.include_router(module.router)
    return app


app = create_app()
