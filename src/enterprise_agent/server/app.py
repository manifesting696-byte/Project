"""HTTP adapter: middleware, exception mapping and route registration.

Business execution lives in ``flow.application.Application``; this module only
assembles transport concerns.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from enterprise_agent.server.deps import close_application
from enterprise_agent.server.identity import close as close_identity
from enterprise_agent.server.identity import setup
from enterprise_agent.server.routes import (
    admin,
    artifacts,
    ask,
    auth,
    catalog,
    plugins,
    requests,
    ui,
)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
    ),
}


@asynccontextmanager
async def lifespan(app):
    setup()
    try:
        yield
    finally:
        close_application()
        close_identity()


app = FastAPI(title="企业数据智能问析", version="3.0.0", lifespan=lifespan)


@app.exception_handler(PermissionError)
async def forbidden(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


for module in (ui, auth, ask, requests, artifacts, catalog, admin, plugins):
    app.include_router(module.router)
