"""LangGraph routing for intent, preparation, Request processing, and publication."""

from copy import deepcopy

from enterprise_agent.domain.errors import public_message


def commit(self, state):
    errors, prepared = dict(state["errors"]), {}
    for operation in state["ordered"]:
        key = operation["request_id"]
        if key not in state["bundles"]:
            continue
        bundle = state["bundles"][key]
        try:
            if any(
                binding["source"]["kind"] == "request"
                and binding["source"]["id"] in errors
                for binding in bundle["request"]["spec"]["inputs"]
            ):
                raise ValueError("上游需求保存失败，请先完成上游需求")
            workflow_key = f"{state['turn_id']}:commit:{key}"
            if self.app.stores.workflow_event(state["session_id"], workflow_key):
                prepared[key] = self.app.stores.get("requests", key)
            else:
                prepared[key] = self.app.preparation.commit(
                    {**bundle, "workflow_key": workflow_key}
                )
        except Exception as exc:
            errors[key] = public_message(exc)
    return {"prepared": prepared, "errors": errors}


def save_bundle(self, bundle):
    events = deepcopy(bundle["events"])
    if bundle.get("workflow_key"):
        for event in events:
            event["payload"]["workflow_key"] = bundle["workflow_key"]
    self.stores.save_many(
        bundle["records"],
        expected_revisions=bundle["expected_revisions"],
        expected_plan_versions=bundle["expected_plan_versions"],
        events=events,
    )
    return bundle["request"]
