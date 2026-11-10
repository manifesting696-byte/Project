"""Recognize, normalize and journal intent with one bounded repair attempt."""

from enterprise_agent.domain.errors import public_message
from enterprise_agent.stages.intent.retry import recognize
from enterprise_agent.stages.intent.schema import Intent, RequestOperation


def intent(self, state):
    session_id, turn_id = state["session_id"], state["turn_id"]
    cached = self.app.stores.workflow_event(session_id, f"{turn_id}:intent")
    if cached:
        proposal = Intent.model_validate(cached["payload"]["intent"])
        ordered = self.app.requests.order_operations(proposal.operations)
    else:

        def infer(feedback):
            if state["forced_operation"]:
                return Intent(
                    operations=[
                        RequestOperation.model_validate(state["forced_operation"])
                    ]
                )
            return self.app.supervisor.intent(
                session_id,
                state["query"],
                self.app.emitter(session_id, turn_id),
                feedback,
            )

        try:
            proposal, ordered = recognize(
                infer, lambda value: self.app.requests.normalize(session_id, value)
            )
        except Exception as exc:
            return {"error": public_message(exc)}
        cached = self.app.stores.event(
            session_id,
            turn_id,
            "intent_resolved",
            "Supervisor",
            {"intent": proposal.model_dump(exclude_none=True)},
            workflow_key=f"{turn_id}:intent",
        )
    return {
        "intent": proposal.model_dump(exclude_none=True),
        "ordered": [item.model_dump(exclude_none=True) for item in ordered],
        "intent_event_id": cached["event_id"],
    }
