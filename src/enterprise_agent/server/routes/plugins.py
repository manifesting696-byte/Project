"""Runtime plugin and profile management (administrator only)."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.auth import require
from enterprise_agent.server.schemas import (
    PluginLoad,
    PluginReload,
    PluginUnload,
    ProfileReload,
)

router = APIRouter()


@router.get("/runtime/plugins")
def plugins(user=Depends(require("admin"))):
    engine = deps.application()
    return {**engine.runtime.inventory(), "profile": engine.profile}


@router.post("/runtime/plugins/load")
def load_plugin(body: PluginLoad, user=Depends(require("admin"))):
    return deps.application().runtime.load(body.model_dump())


@router.post("/runtime/plugins/{plugin_id}/unload")
def unload_plugin(plugin_id: str, body: PluginUnload, user=Depends(require("admin"))):
    return deps.application().runtime.unload(plugin_id, body.cascade)


@router.post("/runtime/plugins/{plugin_id}/reload")
def reload_plugin(plugin_id: str, body: PluginReload, user=Depends(require("admin"))):
    return deps.application().runtime.reload(plugin_id, body.config)


@router.post("/runtime/profile/reload")
def reload_profile(body: ProfileReload, user=Depends(require("admin"))):
    return deps.application().reload_profile(body.profile)
