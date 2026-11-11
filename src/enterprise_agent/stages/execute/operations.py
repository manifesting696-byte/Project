"""Stage-owned implementation behind the application facade."""

from enterprise_agent.domain.answer import Response
from enterprise_agent.domain.errors import public_message
from enterprise_agent.ledger.ledger import now


def process_operation(
    self, session_id, turn_id, operation, request, previous, pause, preview
):
    emit = self.emitter(session_id, turn_id, operation.request_id)
    visible_names = visible_outputs(request, previous)
    if not self.execution.upstream_ready(request):
        answer, state = self.execution.start(
            request, turn_id, operation.instruction, pause, visible_names, preview
        )
        return request, answer, state
    if operation.action == "none":
        workflow_key = f"{turn_id}:direct:{request['request_id']}"
        cached = self.stores.workflow_event(session_id, workflow_key)
        if cached:
            return (
                request,
                Response.model_validate({"blocks": cached["payload"]["blocks"]}),
                "completed",
            )
        context = {
            **self.context.initial(session_id, operation.instruction),
            "operation": operation.model_dump(exclude_none=True),
            "request": request,
            "scope_request_ids": [request["request_id"]],
            "referenced_inputs": self.context.ref_views(
                session_id,
                [binding["source"] for binding in request["spec"]["inputs"]],
            ),
        }
        answer = self.supervisor.answer(session_id, context, emit)
        request["status"] = "completed"
        answer = self.finish_operation(
            request,
            turn_id,
            answer,
            operation.refs,
            direct=True,
            workflow_key=workflow_key,
            answer_context=context,
        )
        return request, answer, "completed"
    snapshot = self.execution.snapshot(request)
    if snapshot and snapshot.values.get("run_id") == turn_id:
        if snapshot.next:
            answer, state = self.execution.resume(request, turn_id)
        else:
            answer, state = self.execution.result(request, snapshot.values)
    else:
        answer, state = self.execution.start(
            request, turn_id, operation.instruction, pause, visible_names, preview
        )
    return request, answer, state


def _resume(self, user_id, request_id, continue_analysis, answer):
    request = self.stores.owned_request(user_id, request_id)
    if answer is not None:
        if not answer.strip():
            raise ValueError("补充内容不能为空")
        with self._lock(request["session_id"]):
            turn_id = self.stores.new_id("turn")
            self.stores.event(
                request["session_id"],
                turn_id,
                "user_message",
                "User",
                {"content": answer},
                request_id=request_id,
            )
            tasks = self.stores.list("tasks", "request_id", request_id)
            category = (
                "data_analysis"
                if any(
                    node["assigned_agent"]
                    in ("SemanticAgent", "QueryAgent", "AnalysisAgent")
                    for node in tasks
                )
                else "result_interaction"
            )
            operation = {
                "request_id": request_id,
                "action": "update_task",
                "category": category,
                "instruction": answer,
                "refs": [],
            }
            return self.turns.run(
                request["session_id"], turn_id, answer, {}, False, False, operation
            )
    with self._lock(request["session_id"]):
        request = self.stores.owned_request(user_id, request_id)
        if request["status"] == "archived":
            raise ValueError("历史归档需求不能直接恢复")
        turn_id = self.stores.new_id("turn")
        query = request["spec"]["description"]
        self.stores.event(
            request["session_id"],
            turn_id,
            "user_message",
            "User",
            {"content": "继续执行：" + query},
            request_id=request_id,
        )
        try:
            if continue_analysis:
                answer, state = self.execution.resume(request, turn_id)
            else:
                answer = self.supervisor.answer(
                    request["session_id"],
                    self.context.answer(request, query),
                    self.emitter(request["session_id"], turn_id, request_id),
                )
                state = "partial"
                answer = self.finish_operation(request, turn_id, answer)
        except Exception as exc:
            request.update(status="failed", updated_at=now())
            self.stores.put("requests", request)
            answer = Response(
                blocks=[
                    self.responses.message(
                        request["session_id"],
                        turn_id,
                        "本项未完成：" + public_message(exc),
                        request,
                    )
                ]
            )
            state = "failed"
        return self.respond(request["session_id"], turn_id, answer, [request_id], state)


def visible_outputs(request, previous):
    visible_names = None
    if previous:
        old_outputs = {item["name"]: item for item in previous["spec"]["outputs"]}
        current_outputs = {item["name"]: item for item in request["spec"]["outputs"]}
        changed = {
            name
            for name, item in current_outputs.items()
            if old_outputs.get(name) != item
        }
        if changed or old_outputs.keys() != current_outputs.keys():
            visible_names = sorted(changed)
    return visible_names
