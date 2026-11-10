"""LangGraph routing for intent, preparation, Request processing, and publication."""

from enterprise_agent.domain.errors import public_message
from enterprise_agent.stages.intent.schema import RequestOperation


def prepare(self, state):
    operations = [RequestOperation.model_validate(item) for item in state["ordered"]]
    ordered, bundles, proposals, errors = self.app.preparation.prepare(
        state["session_id"],
        state["turn_id"],
        operations,
        state["constraints"],
        state["intent_event_id"],
        lambda key: self.app.emitter(state["session_id"], state["turn_id"], key),
    )
    previous = {}
    for item in ordered:
        if item.action == "update_task":
            request = self.app.stores.get("requests", item.request_id)
            previous[item.request_id] = self.app.stores.get(
                "request_revisions", request["current_revision_id"]
            )
    return {
        "ordered": [item.model_dump(exclude_none=True) for item in ordered],
        "bundles": bundles,
        "proposals": {
            key: value.model_dump(exclude_none=True) for key, value in proposals.items()
        },
        "errors": {key: public_message(value) for key, value in errors.items()},
        "previous": previous,
    }
