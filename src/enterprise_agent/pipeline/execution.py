"""Checkpointed Request execution, replanning, and interrupt/resume control flow."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from ..core.config import MAX_REPLANS
from ..core.models import AnswerDraft, AnswerDraftBlock, Response
from ..persistence.stores import now


class ExecutionContext(TypedDict):
    turn_id: str


class ExecutionState(TypedDict, total=False):
    request_id: str
    revision_id: str
    session_id: str
    run_id: str
    query: str
    output_names: list[str] | None
    pause: bool
    preview: bool
    executions: int
    completion: dict | None
    reply: dict | None
    new_results: list[dict]
    outcome: str
    wait_kind: str
    route: str


class Execution:
    def __init__(self, application, checkpoints):
        self.app, self.checkpoints = application, checkpoints
        builder = StateGraph(ExecutionState, context_schema=ExecutionContext)
        for name in (
            "begin",
            "execute",
            "check",
            "replan",
            "finalize",
            "answer",
            "waiting_answer",
            "wait",
        ):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "begin")
        for name in ("begin", "execute", "check", "replan", "finalize", "wait"):
            builder.add_conditional_edges(name, lambda state: state["route"])
        builder.add_edge("waiting_answer", "wait")
        builder.add_edge("answer", END)
        self.builder = builder

    @staticmethod
    def thread_id(request):
        return f"request:{request['request_id']}:{request['current_revision_id']}"

    def request(self, state):
        request = self.app.stores.get("requests", state["request_id"])
        if request["current_revision_id"] != state["revision_id"]:
            raise ValueError("执行现场对应的需求版本已失效，请使用当前版本")
        return request

    def snapshot(self, request):
        return self.checkpoints.snapshot(self.builder, self.thread_id(request))

    def active(self, request):
        snapshot = self.snapshot(request)
        return bool(snapshot and snapshot.next)

    def start(
        self, request, turn_id, query, pause=False, output_names=None, preview=False
    ):
        initial = {
            "request_id": request["request_id"],
            "revision_id": request["current_revision_id"],
            "session_id": request["session_id"],
            "run_id": turn_id,
            "query": query,
            "pause": pause,
            "preview": preview,
            "output_names": output_names,
            "executions": 0,
            "completion": None,
            "reply": None,
            "new_results": [],
            "outcome": "pending",
            "wait_kind": "",
        }
        result = self.checkpoints.invoke(
            self.builder, self.thread_id(request), initial, {"turn_id": turn_id}
        )
        return self.result(request, result)

    def resume(self, request, turn_id):
        snapshot = self.snapshot(request)
        if not snapshot or not snapshot.next:
            return self.start(request, turn_id, request["spec"]["description"])
        waiting = any(task.interrupts for task in snapshot.tasks)
        value = Command(resume=True) if waiting else None
        result = self.checkpoints.invoke(
            self.builder, self.thread_id(request), value, {"turn_id": turn_id}
        )
        return self.result(request, result)

    def result(self, request, state):
        request.update(self.app.stores.get("requests", request["request_id"]))
        return Response.model_validate(state["reply"]), state["outcome"]

    def key(self, state, phase):
        scope = f"request:{state['request_id']}:{state['revision_id']}"
        return f"{scope}:{state['run_id']}:{state['executions']}:{phase}"

    def cached(self, state, phase):
        key = self.key(state, phase)
        return self.app.stores.workflow_event(state["session_id"], key)

    def upstream_ready(self, request):
        try:
            for binding in request["spec"]["inputs"]:
                if binding["source"]["kind"] == "request":
                    self.app.requests.resolve(request["session_id"], binding["source"])
        except ValueError:
            return False
        return True

    def begin(self, state):
        request = self.request(state)
        if not self.upstream_ready(request):
            return {
                "route": "waiting_answer",
                "wait_kind": "upstream",
                "outcome": "needs_clarification",
            }
        if state["preview"]:
            return {
                "route": "waiting_answer",
                "wait_kind": "preview",
                "outcome": "waiting_input",
            }
        return {"route": "execute"}

    def execute(self, state, runtime: Runtime[ExecutionContext]):
        request = self.request(state)
        if not self.upstream_ready(request):
            return {
                "route": "waiting_answer",
                "wait_kind": "upstream",
                "outcome": "needs_clarification",
            }
        request.update(status="running", updated_at=now())
        self.app.stores.put("requests", request)
        execution = self.app.scheduler.run(
            request,
            runtime.context["turn_id"],
            state["pause"],
            execution_key=self.key(state, "schedule"),
        )
        update = {
            "new_results": execution["new_results"],
            "executions": state["executions"] + 1,
            "pause": False,
            "preview": False,
            "reply": None,
        }
        if execution.get("paused"):
            return {
                **update,
                "route": "waiting_answer",
                "wait_kind": "batch",
                "outcome": "waiting_input",
            }
        if execution.get("waiting_for_requests"):
            return {
                **update,
                "route": "waiting_answer",
                "wait_kind": "upstream",
                "outcome": "needs_clarification",
            }
        if execution["decision_required"]:
            return {
                **update,
                "route": "replan"
                if update["executions"] <= MAX_REPLANS
                else "finalize",
            }
        return {**update, "route": "check"}

    def check(self, state, runtime: Runtime[ExecutionContext]):
        request = self.request(state)
        turn_id = runtime.context["turn_id"]
        emit = self.app.emitter(state["session_id"], turn_id, state["request_id"])
        actual = self.app.requests.outputs(request)
        missing_text = any(
            item["content_type"] == "text" and item["name"] not in actual
            for item in request["spec"]["outputs"]
        )
        nodes = self.app.stores.list("tasks", "request_id", state["request_id"])
        reply = None
        if missing_text and not any(
            node["status"] in ("pending", "running", "needs_clarification", "failed")
            for node in nodes
        ):
            draft = self.app.supervisor.answer(
                state["session_id"],
                self.app.context.answer(
                    request, state["query"], output_names=state["output_names"]
                ),
                emit,
            )
            reply = self.app.materialize_answer(
                request, turn_id, draft, output_names=state["output_names"], bind=True
            ).model_dump(exclude_none=True)
        completion = self.app.supervisor.check(request, state["query"], emit)
        self.app.validate_completion(request, completion)
        route = (
            "replan"
            if completion.next_action == "replan" and state["executions"] <= MAX_REPLANS
            else "finalize"
        )
        return {
            "completion": completion.model_dump(exclude_none=True),
            "reply": reply,
            "route": route,
        }

    def replan(self, state, runtime: Runtime[ExecutionContext]):
        request = self.request(state)
        if not self.cached(state, "replan"):
            emit = self.app.emitter(
                state["session_id"], runtime.context["turn_id"], state["request_id"]
            )
            evidence = list(state["new_results"])
            if state["completion"]:
                evidence.append({"completion": state["completion"]})
            plan = self.app.supervisor.plan(request, emit, evidence)
            self.app.apply_plan(
                request,
                runtime.context["turn_id"],
                plan,
                workflow_key=self.key(state, "replan"),
            )
        return {"route": "execute", "completion": None, "reply": None}

    def finalize(self, state, runtime: Runtime[ExecutionContext]):
        request = self.request(state)
        completion = state["completion"]
        pending = any(
            node["status"] in ("pending", "running")
            for node in self.app.stores.list("tasks", "request_id", state["request_id"])
        )
        complete = (
            completion
            and completion["next_action"] == "answer"
            and all(item["status"] == "completed" for item in completion["outputs"])
            and not pending
        )
        clarify = completion and (
            completion["next_action"] == "clarify"
            or any(
                item["status"] == "needs_clarification"
                for item in completion["outputs"]
            )
        )
        if complete:
            outcome = "completed"
        elif clarify:
            outcome = "needs_clarification"
        else:
            outcome = "partial"
        data = completion or {"reason": "本轮规划预算已用完，仍有未完成交付"}
        self.app.stores.event(
            state["session_id"],
            runtime.context["turn_id"],
            "agent_output",
            "Supervisor",
            {"summary": data["reason"], "completion": data},
            request_id=request["request_id"],
            workflow_key=self.key(state, "completion"),
        )
        if clarify:
            return {
                "outcome": outcome,
                "wait_kind": "clarification",
                "route": "waiting_answer",
                "reply": None,
            }
        return {
            "outcome": outcome,
            "route": "answer",
            "reply": state["reply"] if complete else None,
        }

    def answer(self, state, runtime: Runtime[ExecutionContext]):
        request = self.request(state)
        request["status"] = (
            "completed" if state["outcome"] == "completed" else "pending"
        )
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

    def wait(self, state, runtime: Runtime[ExecutionContext]):
        interrupt(
            {
                "request_id": state["request_id"],
                "revision_id": state["revision_id"],
                "reason": state["wait_kind"],
                "blocks": state["reply"]["blocks"],
            }
        )
        self.request(state)
        decision = state["wait_kind"] == "clarification" or (
            state["wait_kind"] == "batch"
            and any(
                item.get("replan_after") or item.get("suggestions")
                for item in state["new_results"]
            )
        )
        return {
            "route": "replan" if decision else "execute",
            "preview": False,
            "pause": False,
            "run_id": runtime.context["turn_id"],
            "executions": 0,
            "completion": None,
            "reply": None,
        }
