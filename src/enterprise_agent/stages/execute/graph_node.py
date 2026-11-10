"""LangGraph routing for intent, preparation, Request processing, and publication."""

from enterprise_agent.domain.errors import VersionConflict, public_message
from enterprise_agent.ledger.ledger import now
from enterprise_agent.stages.intent.schema import Clarification, RequestOperation


def process(self, state):
    operation = RequestOperation.model_validate(state["ordered"][state["cursor"]])
    key, session_id, turn_id = (
        operation.request_id,
        state["session_id"],
        state["turn_id"],
    )
    touched, answers, states = (
        list(state["touched"]),
        dict(state["answers"]),
        dict(state["states"]),
    )
    cached = self.app.stores.workflow_event(session_id, f"{turn_id}:operation:{key}")
    if cached:
        blocks, status = cached["payload"]["blocks"], cached["payload"]["status"]
    else:
        try:
            if key in state["errors"]:
                raise ValueError(state["errors"][key])
            proposal = state["proposals"][key]
            if proposal["clarifications"]:
                owner = (
                    self.app.stores.get("requests", key)
                    if self.app.stores.exists("requests", key)
                    else None
                )
                blocks = [
                    self.app.clarification_block(
                        session_id,
                        turn_id,
                        Clarification.model_validate(item),
                        owner,
                    ).model_dump(exclude_none=True)
                    for item in proposal["clarifications"]
                ]
                status = "needs_clarification"
            else:
                request = self.app.stores.get("requests", key)
                if (
                    request["current_revision_id"]
                    != state["prepared"][key]["current_revision_id"]
                ):
                    raise VersionConflict("该轮对应的需求版本已失效，请使用当前版本")
                request, answer, status = self.app.process_operation(
                    session_id,
                    turn_id,
                    operation,
                    request,
                    state["previous"].get(key),
                    state["pause"],
                    state["preview"],
                )
                blocks = [
                    block.model_dump(exclude_none=True) for block in answer.blocks
                ]
        except Exception as exc:
            owner = (
                self.app.stores.get("requests", key)
                if self.app.stores.exists("requests", key)
                else None
            )
            if (
                owner
                and key in state["prepared"]
                and owner["current_revision_id"]
                == state["prepared"][key]["current_revision_id"]
            ):
                owner.update(status="failed", updated_at=now())
                self.app.stores.put("requests", owner)
            blocks = [
                self.app.responses.message(
                    session_id, turn_id, "本项未完成：" + public_message(exc), owner
                ).model_dump(exclude_none=True)
            ]
            status = "failed"
        self.app.stores.event(
            session_id,
            turn_id,
            "agent_output",
            "Supervisor",
            {
                "summary": "本项处理已记录",
                "schema_version": 2,
                "blocks": blocks,
                "status": status,
            },
            request_id=key if self.app.stores.exists("requests", key) else None,
            workflow_key=f"{turn_id}:operation:{key}",
        )
    if self.app.stores.exists("requests", key) and key not in touched:
        touched.append(key)
    answers[key], states[key] = blocks, status
    return {
        "answers": answers,
        "states": states,
        "touched": touched,
        "cursor": state["cursor"] + 1,
    }
