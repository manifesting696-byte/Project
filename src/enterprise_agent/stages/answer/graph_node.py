"""LangGraph routing for intent, preparation, Request processing, and publication."""

from langgraph.runtime import Runtime

from enterprise_agent.domain.answer import (
    AnswerDraft,
    AnswerDraftBlock,
    Response,
    ResponseBlock,
)
from enterprise_agent.flow.state import ExecutionContext
from enterprise_agent.stages.intent.schema import Intent


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
            if item.request_id and self.app.stores.exists("requests", item.request_id)
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


def answer(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    request["status"] = "completed" if state["outcome"] == "completed" else "pending"
    cached = self.cached(state, "answer")
    if cached:
        self.app.stores.save_many(
            [("requests", request)],
            expected_revisions={state["request_id"]: state["revision_id"]},
        )
        return {"reply": {"blocks": cached["payload"]["blocks"]}}
    turn_id = runtime.context["turn_id"]
    answer = (
        Response.model_validate(state["reply"])
        if state["reply"]
        else self.app.supervisor.answer(
            state["session_id"],
            self.app.context.answer(
                request, state["query"], state["completion"], state["output_names"]
            ),
            self.app.emitter(state["session_id"], turn_id, state["request_id"]),
        )
    )
    reply = self.app.finish_operation(
        request,
        turn_id,
        answer,
        output_names=state["output_names"],
        workflow_key=self.key(state, "answer"),
    )
    return {"reply": reply.model_dump(exclude_none=True)}


def waiting_answer(self, state, runtime: Runtime[ExecutionContext]):
    request = self.request(state)
    request["status"] = (
        "pending" if state["outcome"] == "waiting_input" else "needs_clarification"
    )
    cached = self.cached(state, "wait:" + state["wait_kind"])
    if cached:
        self.app.stores.save_many(
            [("requests", request)],
            expected_revisions={state["request_id"]: state["revision_id"]},
        )
        return {"reply": {"blocks": cached["payload"]["blocks"]}}
    turn_id = runtime.context["turn_id"]
    if state["wait_kind"] == "clarification":
        answer = self.app.supervisor.answer(
            state["session_id"],
            self.app.context.answer(
                request, state["query"], state["completion"], state["output_names"]
            ),
            self.app.emitter(state["session_id"], turn_id, state["request_id"]),
        )
    else:
        text = {
            "preview": "已保存完整需求和执行计划，等待继续执行。",
            "batch": "本批执行已保存，可以继续执行。",
            "upstream": "本项依赖的上游结果尚未交付，请先补全上游需求。",
        }[state["wait_kind"]]
        answer = AnswerDraft(
            blocks=[AnswerDraftBlock(content_type="text", content=text)]
        )
    reply = self.app.finish_operation(
        request,
        turn_id,
        answer,
        workflow_key=self.key(state, "wait:" + state["wait_kind"]),
    )
    return {"reply": reply.model_dump(exclude_none=True)}
