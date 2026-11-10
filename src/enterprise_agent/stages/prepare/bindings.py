"""bindings for persisted request contracts."""


def plan_bindings(self, request, revision=None):
    events = self.stores.list("session_events", "session_id", request["session_id"])
    revision = revision or self.stores.get(
        "request_revisions", request["current_revision_id"]
    )
    names = {item["name"] for item in request["spec"]["outputs"]}
    while names:
        for event in reversed(events):
            if (
                event["event_type"] == "plan_updated"
                and event.get("request_id") == request["request_id"]
                and event["payload"].get("revision_id") == revision["revision_id"]
            ):
                return {
                    name: ref
                    for name, ref in event["payload"]["plan"]["output_bindings"].items()
                    if name in names
                }
        if not revision.get("parent_revision_id"):
            break
        previous = self.stores.get("request_revisions", revision["parent_revision_id"])
        old = {item["name"]: item for item in previous["spec"]["outputs"]}
        current = {item["name"]: item for item in revision["spec"]["outputs"]}
        changed = {name for name, item in old.items() if current.get(name) != item}
        if (
            previous["spec"]["description"] != revision["spec"]["description"]
            or previous["spec"]["parameters"] != revision["spec"]["parameters"]
            or (
                previous["spec"]["inputs"] != revision["spec"]["inputs"]
                and not self.presentation_only_inputs(
                    request, previous["spec"], revision["spec"], changed
                )
            )
        ):
            break
        names &= {name for name, item in current.items() if old.get(name) == item}
        revision = previous
    return {}
