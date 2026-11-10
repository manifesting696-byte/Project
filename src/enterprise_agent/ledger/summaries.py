"""Append-only cumulative conversation compression; never an authority for exact data."""
from pydantic import Field
from enterprise_agent.settings import SUMMARY_THRESHOLD, RECENT_EVENTS
from enterprise_agent.domain.models import Model, ResourceRef
from enterprise_agent.ledger.ledger import now


class SummaryDraft(Model):
    content: str
    key_refs: list[ResourceRef] = Field(default_factory=list)


def compress(stores, model, session_id, validate_ref):
    events = stores.list("session_events", "session_id", session_id)
    existing = stores.list("session_summaries", "session_id", session_id)
    previous = existing[-1] if existing else None
    covered = previous["covered_to_seq"] if previous else 0
    to_cover = events[:-RECENT_EVENTS]
    fresh = [event for event in to_cover if event["seq"] > covered]
    if len(fresh) < SUMMARY_THRESHOLD:
        return
    draft = model.run("SessionSummary", "将较早会话累积压缩为短摘要，保留话题、需求变更和关键真实引用。"
                      "不得将摘要中的试算当成数据事实，不复制查询明细或完整分析。",
                      {"previous_summary": previous, "events": fresh}, SummaryDraft)
    for ref in draft.key_refs:
        validate_ref(session_id, ref.model_dump(exclude_none=True))
    stores.put("session_summaries", {"summary_id": stores.new_id("summary"), "session_id": session_id,
        "covered_to_seq": fresh[-1]["seq"], "content": draft.content,
        "key_refs": [ref.model_dump(exclude_none=True) for ref in draft.key_refs], "created_at": now()})
