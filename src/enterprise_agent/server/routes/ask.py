"""Turn entry points: ask (execute) and bind (preview the plan before running)."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.audit import audit
from enterprise_agent.server.auth import require
from enterprise_agent.server.schemas import Question

router = APIRouter()


def execute_question(body, user, preview=False):
    constraints = dict(body.constraints)
    for key in ("start", "end"):
        if key in body.model_fields_set:
            constraints[key] = getattr(body, key)
    result = deps.application().ask(
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


@router.post("/ask")
def ask(body: Question, user=Depends(require("admin", "analyst"))):
    return execute_question(body, user)


@router.post("/bind")
def preview(body: Question, user=Depends(require("admin", "analyst"))):
    return execute_question(body, user, preview=True)
