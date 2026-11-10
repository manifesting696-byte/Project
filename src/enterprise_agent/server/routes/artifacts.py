"""Artifact inspection: metadata, paged content, SQL/provenance and Markdown download."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from enterprise_agent.server import deps
from enterprise_agent.server.auth import current_user

router = APIRouter()


def authorize_artifact(artifact_id, user):
    engine = deps.application()
    item = engine.artifacts.inspect(artifact_id)
    engine.stores.session(user["id"], item["session_id"])
    return engine, item


@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    return item


@router.get("/artifacts/{artifact_id}/content")
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


@router.get("/artifacts/{artifact_id}/sql")
def sql(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    return {
        "sql": engine.artifacts.sql(artifact_id),
        "semantic_binding": item["payload"].get("semantic_binding"),
        "provenance": item["payload"].get("provenance"),
        "source_artifact_ids": item["source_artifact_ids"],
    }


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, user=Depends(current_user)):
    engine, item = authorize_artifact(artifact_id, user)
    if item["content_type"] not in ("text", "report"):
        raise ValueError("该产物不是 Markdown 文档")
    return Response(
        engine.artifacts.content(artifact_id),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}.md"'},
    )
