"""Stage-owned implementation behind the application facade."""

from enterprise_agent.ledger.ledger import now


def finish_operation(
    self,
    request,
    turn_id,
    answer,
    extra_refs=(),
    direct=False,
    output_names=None,
    workflow_key=None,
    answer_context=None,
):
    response = self.materialize_answer(
        request,
        turn_id,
        answer,
        extra_refs,
        output_names,
        bind=direct,
        workflow_key=workflow_key,
        answer_context=answer_context,
    )
    if direct and (
        not response.blocks
        or set(self.requests.outputs(request))
        != {item["name"] for item in request["spec"]["outputs"]}
    ):
        raise ValueError("直接回答需要完整的实际交付")
    request["updated_at"] = now()
    payload = {
        "summary": "本项回答已生成",
        "revision_id": request["current_revision_id"],
        "schema_version": 2,
        "blocks": [block.model_dump(exclude_none=True) for block in response.blocks],
    }
    if workflow_key:
        payload["workflow_key"] = workflow_key
    self.stores.save_many(
        [("requests", request)],
        expected_revisions={request["request_id"]: request["current_revision_id"]},
        event={
            "session_id": request["session_id"],
            "turn_id": turn_id,
            "event_type": "agent_output",
            "actor": "Supervisor",
            "request_id": request["request_id"],
            "payload": payload,
        },
    )
    return response


def respond(self, session_id, turn_id, answer, request_ids, status="completed"):
    for index, block in enumerate(answer.blocks, 1):
        block.block_id = f"block_{index:03d}"
        self.requests.validate_ref(
            session_id, {"kind": "artifact", "id": block.artifact_id}
        )
    event = self.stores.event(
        session_id,
        turn_id,
        "assistant_response",
        "Supervisor",
        {
            "schema_version": 2,
            "status": status,
            "blocks": [block.model_dump(exclude_none=True) for block in answer.blocks],
        },
        workflow_key=f"{turn_id}:response",
    )
    result = self.snapshot(session_id, turn_id, request_ids, status)
    visible = {block["artifact_id"] for block in event["payload"]["blocks"]}
    known = {item["artifact_id"] for item in result["artifacts"]}
    result["artifacts"].extend(
        self.artifacts.inspect(key) for key in sorted(visible - known)
    )
    result.update(
        schema_version=2,
        blocks=event["payload"]["blocks"],
        response_event_id=event["event_id"],
    )
    self.runtime.events.notify(
        "response/published",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "response_event_id": event["event_id"],
        },
    )
    return result


def snapshot(self, session_id, turn_id=None, request_ids=None, status=None):
    selected = (
        request_ids
        if request_ids is not None
        else [
            item["request_id"]
            for item in self.stores.list("requests", "session_id", session_id)
        ]
    )
    requests = [self.stores.get("requests", key) for key in dict.fromkeys(selected)]
    artifact_ids = self.requests.artifact_scope(
        [ref for item in requests for ref in self.requests.outputs(item).values()]
        + [binding["source"] for item in requests for binding in item["spec"]["inputs"]]
    )
    selected_ids = set(selected)
    return {
        "status": status
        or (
            "completed"
            if all(item["status"] == "completed" for item in requests)
            else "partial"
        ),
        "session_id": session_id,
        "turn_id": turn_id,
        "requests": requests,
        "deliveries": {
            item["request_id"]: self.requests.outputs(item) for item in requests
        },
        "revisions": [
            self.stores.get("request_revisions", item["current_revision_id"])
            for item in requests
        ],
        "tasks": [
            node
            for item in requests
            for node in self.stores.list("tasks", "request_id", item["request_id"])
        ],
        "artifacts": [
            item
            for item in self.stores.list("artifacts", "session_id", session_id)
            if item.get("request_id") in selected_ids
            or item["artifact_id"] in artifact_ids
        ],
        "blocks": [],
        "snapshot_id": self.registry.snapshot_id,
        "semantic_version": self.registry.catalog["version"],
        "catalog_version": self.registry.catalog_version,
    }


def clarification_block(self, session_id, turn_id, item, owner=None):
    refs = [
        ref.model_dump(exclude_none=True)
        for ref in item.refs
        if ref.kind == "artifact" or self.stores.exists("requests", ref.id)
    ]
    sources = sorted(self.requests.artifact_scope(refs))
    return self.responses.message(session_id, turn_id, item.question, owner, sources)


def turn_status(states, clarifications=False):
    if not states:
        return "needs_clarification" if clarifications else "completed"
    if all(state == "completed" for state in states) and not clarifications:
        return "completed"
    if any(state == "waiting_input" for state in states):
        return "waiting_input"
    if all(state == "failed" for state in states):
        return "failed"
    if not any(state == "completed" for state in states) and (
        clarifications or any(state == "needs_clarification" for state in states)
    ):
        return "needs_clarification"
    return "partial"
