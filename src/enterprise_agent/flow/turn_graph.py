"""LangGraph routing for intent, preparation, Request processing, and publication."""

from langgraph.graph import END, START, StateGraph

from enterprise_agent.domain.answer import Response, ResponseBlock
from enterprise_agent.flow.state import TurnState
from enterprise_agent.stages.intent.schema import Clarification, Intent, RequestOperation
from enterprise_agent.ledger.ledger import now


class Turns:
    def __init__(self, application, checkpoints):
        self.app, self.checkpoints = application, checkpoints
        builder = StateGraph(TurnState)
        for name in ("intent", "prepare", "commit", "process", "respond"):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "intent")
        builder.add_conditional_edges(
            "intent", lambda state: "respond" if state.get("error") else "prepare"
        )
        builder.add_edge("prepare", "commit")
        builder.add_conditional_edges("commit", self.next_operation)
        builder.add_conditional_edges("process", self.next_operation)
        builder.add_edge("respond", END)
        self.builder = builder

    @staticmethod
    def thread_id(session_id, turn_id):
        return f"turn:{session_id}:{turn_id}"

    def run(
        self,
        session_id,
        turn_id,
        query,
        constraints,
        pause,
        preview,
        forced_operation=None,
    ):
        initial = {
            "session_id": session_id,
            "turn_id": turn_id,
            "query": query,
            "constraints": constraints,
            "pause": pause,
            "preview": preview,
            "forced_operation": forced_operation,
            "cursor": 0,
            "answers": {},
            "states": {},
            "touched": [],
            "error": None,
        }
        return self.checkpoints.invoke(
            self.builder, self.thread_id(session_id, turn_id), initial
        )["result"]

    def resume(self, session_id, turn_id):
        snapshot = self.checkpoints.snapshot(
            self.builder, self.thread_id(session_id, turn_id)
        )
        if not snapshot or not snapshot.values:
            raise ValueError("该轮没有可恢复的执行现场")
        if not snapshot.next:
            return snapshot.values["result"]
        return self.checkpoints.invoke(
            self.builder, self.thread_id(session_id, turn_id), None
        )["result"]

    def intent(self, state):
        session_id, turn_id = state["session_id"], state["turn_id"]
        cached = self.app.stores.workflow_event(session_id, f"{turn_id}:intent")
        if cached:
            intent = Intent.model_validate(cached["payload"]["intent"])
            ordered = self.app.requests.order_operations(intent.operations)
        else:
            try:
                feedback = None
                for attempt in range(2):
                    intent = (
                        Intent(
                            operations=[
                                RequestOperation.model_validate(
                                    state["forced_operation"]
                                )
                            ]
                        )
                        if state["forced_operation"]
                        else self.app.supervisor.intent(
                            session_id,
                            state["query"],
                            self.app.emitter(session_id, turn_id),
                            feedback,
                        )
                    )
                    try:
                        ordered = self.app.requests.normalize(session_id, intent)
                        break
                    except ValueError as exc:
                        if attempt:
                            raise
                        feedback = {
                            "error": str(exc),
                            "rejected_intent": intent.model_dump(exclude_none=True),
                        }
            except Exception as exc:
                return {"error": str(exc)[:600]}
            cached = self.app.stores.event(
                session_id,
                turn_id,
                "intent_resolved",
                "Supervisor",
                {"intent": intent.model_dump(exclude_none=True)},
                workflow_key=f"{turn_id}:intent",
            )
        return {
            "intent": intent.model_dump(exclude_none=True),
            "ordered": [item.model_dump(exclude_none=True) for item in ordered],
            "intent_event_id": cached["event_id"],
        }

    def prepare(self, state):
        operations = [
            RequestOperation.model_validate(item) for item in state["ordered"]
        ]
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
                key: value.model_dump(exclude_none=True)
                for key, value in proposals.items()
            },
            "errors": {key: str(value) for key, value in errors.items()},
            "previous": previous,
        }

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
                errors[key] = str(exc)
        return {"prepared": prepared, "errors": errors}

    @staticmethod
    def next_operation(state):
        return "process" if state["cursor"] < len(state["ordered"]) else "respond"

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
        cached = self.app.stores.workflow_event(
            session_id, f"{turn_id}:operation:{key}"
        )
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
                        raise ValueError("该轮对应的需求版本已失效，请使用当前版本")
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
                        session_id, turn_id, "本项未完成：" + str(exc)[:600], owner
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

    def respond(self, state):
        cached = self.app.stores.workflow_event(
            state["session_id"], f"{state['turn_id']}:response"
        )
        if cached:
            response = Response.model_validate({"blocks": cached["payload"]["blocks"]})
            return {
                "result": self.app.respond(
                    state["session_id"],
                    state["turn_id"],
                    response,
                    state["touched"],
                    cached["payload"]["status"],
                )
            }
        if state.get("error"):
            block = self.app.responses.message(
                state["session_id"], state["turn_id"], "本轮未完成：" + state["error"]
            )
            return {
                "result": self.app.respond(
                    state["session_id"],
                    state["turn_id"],
                    Response(blocks=[block]),
                    [],
                    "failed",
                )
            }
        intent = Intent.model_validate(state["intent"])
        blocks = [
            ResponseBlock.model_validate(block)
            for operation in intent.operations
            for block in state["answers"].get(operation.request_id, [])
        ]
        for item in intent.clarifications:
            owner = (
                self.app.stores.get("requests", item.request_id)
                if item.request_id
                and self.app.stores.exists("requests", item.request_id)
                else None
            )
            blocks.append(
                self.app.clarification_block(
                    state["session_id"], state["turn_id"], item, owner
                )
            )
        status = self.app.turn_status(
            list(state["states"].values()), bool(intent.clarifications)
        )
        return {
            "result": self.app.respond(
                state["session_id"],
                state["turn_id"],
                Response(blocks=blocks),
                state["touched"],
                status,
            )
        }
