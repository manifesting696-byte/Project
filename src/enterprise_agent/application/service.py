"""Turn orchestration over persistent Requests and their execution Task DAGs."""

from threading import Lock

from ..agents.specialists import Specialists
from ..agents.supervisor import Supervisor
from ..context.manager import ContextManager
from ..core.config import MAX_REPLANS
from ..core.model import ModelClient
from ..core.models import Answer, AnswerBlock, RequestSpec
from ..persistence.artifacts import Artifacts
from ..persistence.stores import Stores, now
from ..persistence.summaries import compress
from ..pipeline.scheduler import Scheduler
from ..query.gateway import QueryGateway
from ..semantic.registry import Registry
from .requests import Requests
from .tasks import Tasks


class Application:
    def __init__(
        self,
        stores=None,
        model=None,
        registry=None,
        query_gateway=None,
        specialist_overrides=None,
    ):
        self.stores = stores or Stores()
        if stores is None:
            self.stores.recover_interrupted()
        self.model = model or ModelClient()
        self.registry = registry or Registry()
        self.artifacts = Artifacts(self.stores)
        self.requests = Requests(self.stores)
        self.tasks = Tasks(self.stores, self.requests)
        self.context = ContextManager(
            self.stores, self.artifacts, self.requests, self.tasks, self.registry
        )
        self.supervisor = Supervisor(
            self.model, self.context, self.stores, self.artifacts
        )
        self.query_gateway = query_gateway or QueryGateway(self.registry)
        self.specialists = Specialists(
            self.model,
            self.registry,
            self.artifacts,
            self.query_gateway,
            overrides=specialist_overrides,
        )
        self.scheduler = Scheduler(self.stores, self.context, self.specialists)
        self._locks, self._guard = {}, Lock()

    def _lock(self, session_id):
        with self._guard:
            return self._locks.setdefault(session_id, Lock())

    def emitter(self, session_id, turn_id, request_id=None):
        return lambda kind, payload: self.stores.event(
            session_id, turn_id, kind, "Supervisor", payload, request_id=request_id
        )

    def ask(
        self,
        user_id,
        query,
        session_id=None,
        constraints=None,
        pause=False,
        preview=False,
    ):
        session = self.stores.session(user_id, session_id, query)
        session_id = session["session_id"]
        with self._lock(session_id):
            turn_id = self.stores.new_id("turn")
            self.stores.event(
                session_id, turn_id, "user_message", "User", {"content": query}
            )
            emit = self.emitter(session_id, turn_id)
            feedback = None
            for attempt in range(2):
                intent = self.supervisor.intent(session_id, query, emit, feedback)
                try:
                    ordered = self.requests.normalize(session_id, intent)
                    break
                except ValueError as exc:
                    if attempt:
                        raise
                    feedback = {
                        "error": str(exc),
                        "rejected_intent": intent.model_dump(exclude_none=True),
                    }
            event = self.stores.event(
                session_id,
                turn_id,
                "intent_resolved",
                "Supervisor",
                {"intent": intent.model_dump(exclude_none=True)},
            )
            answers, states = {}, {}
            touched = []
            for operation in ordered:
                try:
                    request, answer, state = self.process_operation(
                        session_id,
                        turn_id,
                        operation,
                        event["event_id"],
                        constraints or {},
                        pause,
                        preview,
                    )
                    touched.append(request["request_id"])
                    answers[request["request_id"]], states[request["request_id"]] = (
                        answer.blocks,
                        state,
                    )
                except Exception as exc:
                    if self.stores.exists("requests", operation.request_id):
                        request = self.stores.get("requests", operation.request_id)
                        request.update(status="failed", updated_at=now())
                        self.stores.put("requests", request)
                        touched.append(operation.request_id)
                    self.stores.event(
                        session_id,
                        turn_id,
                        "agent_output",
                        "Supervisor",
                        {"summary": str(exc)[:600], "status": "failed"},
                        request_id=operation.request_id,
                    )
                    answers[operation.request_id] = [
                        AnswerBlock(
                            request_id=operation.request_id
                            if operation.request_id in touched
                            else None,
                            kind="text",
                            content="本项未完成：" + str(exc)[:600],
                        )
                    ]
                    states[operation.request_id] = "failed"
            blocks = [
                block
                for operation in intent.operations
                for block in answers.get(operation.request_id, [])
            ]
            for item in intent.clarifications:
                blocks.append(
                    AnswerBlock(
                        request_id=item.request_id,
                        kind="text",
                        content=item.question,
                        refs=item.refs,
                    )
                )
            status = self.turn_status(
                list(states.values()), bool(intent.clarifications)
            )
            return self.respond(
                session_id, turn_id, Answer(blocks=blocks), touched, status
            )

    @staticmethod
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

    def process_operation(
        self, session_id, turn_id, operation, event_id, parameters, pause, preview
    ):
        previous = None
        if operation.action == "update_task":
            old = self.stores.get("requests", operation.request_id)
            previous = self.stores.get("request_revisions", old["current_revision_id"])
        emit = self.emitter(session_id, turn_id, operation.request_id)
        if operation.action == "none":
            spec = RequestSpec.model_validate(
                {
                    "description": operation.instruction,
                    "inputs": [
                        {
                            "slot": f"source_{index}",
                            "source": ref.model_dump(exclude_none=True),
                        }
                        for index, ref in enumerate(operation.refs, 1)
                    ],
                    "outputs": [
                        {
                            "name": "answer",
                            "kind": "text",
                            "description": operation.instruction,
                        }
                    ],
                }
            )
        else:
            spec = self.supervisor.define(
                session_id, operation, previous, parameters, emit
            )
            for key, value in parameters.items():
                if value is None:
                    spec.parameters.pop(key, None)
                else:
                    spec.parameters[key] = value
        request, revision = self.requests.revise(
            session_id, turn_id, operation, spec, event_id
        )
        visible_names = None
        if previous:
            old_outputs = {item["name"]: item for item in previous["spec"]["outputs"]}
            current_outputs = {
                item["name"]: item for item in revision["spec"]["outputs"]
            }
            changed = {
                name
                for name, item in current_outputs.items()
                if old_outputs.get(name) != item
            }
            if changed or old_outputs.keys() != current_outputs.keys():
                visible_names = sorted(changed)
        direct_update = (
            operation.action == "update_task"
            and operation.category in ("general_qa", "business_knowledge")
            and not self.stores.list("tasks", "request_id", request["request_id"])
            and all(output.kind == "text" for output in spec.outputs)
        )
        if operation.action == "none" or direct_update:
            context = {
                **self.context.initial(session_id, operation.instruction),
                "operation": operation.model_dump(exclude_none=True),
                "request": request,
                "scope_request_ids": [request["request_id"]],
                "referenced_inputs": self.context.ref_views(
                    session_id,
                    [ref.model_dump(exclude_none=True) for ref in operation.refs],
                ),
            }
            answer = self.supervisor.answer(session_id, context, emit)
            request["status"] = "completed"
            self.finish_operation(request, turn_id, answer, operation.refs, direct=True)
            return request, answer, "completed"
        # Resolve named cross-request inputs before allowing a consumer to query or calculate.
        for binding in revision["spec"]["inputs"]:
            ref = binding["source"]
            if ref["kind"] == "request":
                try:
                    self.tasks.resolve(session_id, ref)
                except ValueError:
                    request["status"] = "needs_clarification"
                    answer = Answer(
                        blocks=[
                            AnswerBlock(
                                kind="text",
                                content="本项依赖的上游结果尚未交付，请先补全上游需求。",
                            )
                        ]
                    )
                    self.finish_operation(request, turn_id, answer, operation.refs)
                    return request, answer, "needs_clarification"
        plan = self.supervisor.plan(request, emit)
        self.apply_plan(request, turn_id, plan)
        if preview:
            answer = Answer(
                blocks=[
                    AnswerBlock(
                        kind="text", content="已保存完整需求和执行计划，等待继续执行。"
                    )
                ]
            )
            self.finish_operation(request, turn_id, answer, operation.refs)
            return request, answer, "waiting_input"
        answer, state = self.run(
            request, turn_id, operation.instruction, pause, visible_names
        )
        self.finish_operation(
            request, turn_id, answer, operation.refs, output_names=visible_names
        )
        return request, answer, state

    def apply_plan(self, request, turn_id, plan):
        for attempt in range(2):
            try:
                self.tasks.apply_plan(request, turn_id, plan)
                return
            except ValueError as exc:
                self.stores.event(
                    request["session_id"],
                    turn_id,
                    "agent_output",
                    "Supervisor",
                    {
                        "summary": "计划校验失败：" + str(exc),
                        "rejected_plan": plan.model_dump(exclude_none=True),
                    },
                    request_id=request["request_id"],
                )
                if attempt:
                    raise
                plan = self.supervisor.plan(
                    request,
                    self.emitter(request["session_id"], turn_id, request["request_id"]),
                    [
                        {
                            "plan_validation_error": str(exc),
                            "rejected_proposal": plan.model_dump(exclude_none=True),
                        }
                    ],
                )

    def run(self, request, turn_id, query, pause=False, output_names=None):
        request.update(status="running", updated_at=now())
        self.stores.put("requests", request)
        emit = self.emitter(request["session_id"], turn_id, request["request_id"])
        completion = None
        for _ in range(MAX_REPLANS + 1):
            execution = self.scheduler.run(request, turn_id, pause)
            pause = False
            self.tasks.refresh_outputs(request)
            if execution.get("paused"):
                request["status"] = "pending"
                answer = Answer(
                    blocks=[
                        AnswerBlock(
                            kind="text", content="本批执行已保存，可以继续执行。"
                        )
                    ]
                )
                return answer, "waiting_input"
            if execution["decision_required"]:
                self.apply_plan(
                    request,
                    turn_id,
                    self.supervisor.plan(request, emit, execution["new_results"]),
                )
                continue
            completion = self.supervisor.check(request, query, emit)
            self.validate_completion(request, completion)
            if completion.next_action != "replan":
                break
            self.apply_plan(
                request,
                turn_id,
                self.supervisor.plan(
                    request,
                    emit,
                    [
                        {
                            "completion": completion.model_dump(exclude_none=True),
                            "results": execution["new_results"],
                        }
                    ],
                ),
            )
        pending = any(
            node["status"] in ("pending", "running")
            for node in self.stores.list("tasks", "request_id", request["request_id"])
        )
        complete = (
            completion is not None
            and completion.next_action == "answer"
            and all(item.status == "completed" for item in completion.outputs)
            and not pending
        )
        if complete:
            request["status"], state = "completed", "completed"
        elif completion and (
            completion.next_action == "clarify"
            or any(item.status == "needs_clarification" for item in completion.outputs)
        ):
            request["status"], state = "needs_clarification", "needs_clarification"
        else:
            request["status"], state = "pending", "partial"
        completion_data = (
            completion.model_dump(exclude_none=True)
            if completion
            else {"reason": "本轮规划预算已用完，仍有未完成交付"}
        )
        self.stores.event(
            request["session_id"],
            turn_id,
            "agent_output",
            "Supervisor",
            {"summary": completion_data["reason"], "completion": completion_data},
            request_id=request["request_id"],
        )
        answer = self.supervisor.answer(
            request["session_id"],
            self.context.answer(request, query, completion_data, output_names),
            emit,
        )
        return answer, state

    def validate_completion(self, request, completion):
        revision = self.stores.get("request_revisions", request["current_revision_id"])
        outputs = {item["name"]: item for item in revision["spec"]["outputs"]}
        names = [item.name for item in completion.outputs]
        if len(names) != len(set(names)) or set(names) != set(outputs):
            raise ValueError("验收必须覆盖当前 Request 的每个交付名称")
        for check in completion.outputs:
            for ref in check.refs:
                self.requests.validate_ref(
                    request["session_id"], ref.model_dump(exclude_none=True)
                )
            if check.status != "completed":
                continue
            actual = request["output_refs"].get(check.name)
            if not actual or not check.refs:
                raise ValueError("完成的交付必须绑定真实输出并提供证据")
            resolved = self.tasks.resolve(request["session_id"], actual)
            actual_id = resolved.get("artifact_id", actual["id"])
            if actual_id not in {ref.id for ref in check.refs}:
                raise ValueError("验收证据没有指向该项实际交付")
            kind = outputs[check.name]["kind"]
            if kind != "text":
                if not resolved.get("artifact_id"):
                    raise ValueError("展示交付需要实际 Artifact")
                expected = {
                    "table": "query_result",
                    "chart": "chart",
                    "report": "report",
                }[kind]
                if self.artifacts.inspect(resolved["artifact_id"])["type"] != expected:
                    raise ValueError("实际产物类型与交付要求不一致")

    def validate_answer(self, request, answer, extra_refs=(), output_names=None):
        revision = self.stores.get("request_revisions", request["current_revision_id"])
        allowed = self.requests.artifact_scope(
            [
                ref
                for name, ref in request["output_refs"].items()
                if output_names is None or name in output_names
            ]
            + [binding["source"] for binding in revision["spec"]["inputs"]]
            + [ref.model_dump(exclude_none=True) for ref in extra_refs]
        )
        for index, block in enumerate(answer.blocks, 1):
            if block.request_id not in (None, request["request_id"]):
                raise ValueError("本轮回答包含其他 Request 的交付")
            block.request_id = request["request_id"]
            block.block_id = f"block_{index:03d}"
            for ref in block.refs:
                self.requests.validate_ref(
                    request["session_id"], ref.model_dump(exclude_none=True)
                )
                if ref.kind == "artifact" and ref.id not in allowed:
                    raise ValueError("回答产物不属于本轮需求的输入或交付")
            if block.kind != "text":
                if not block.refs or any(ref.kind != "artifact" for ref in block.refs):
                    raise ValueError("展示块必须引用真实 Artifact")
                expected = {
                    "table": "query_result",
                    "chart": "chart",
                    "report": "report",
                }[block.kind]
                if any(
                    self.artifacts.inspect(ref.id)["type"] != expected
                    for ref in block.refs
                ):
                    raise ValueError("展示块类型与实际产物不一致")

    def finish_operation(
        self, request, turn_id, answer, extra_refs=(), direct=False, output_names=None
    ):
        self.validate_answer(request, answer, extra_refs, output_names)
        event = self.stores.event(
            request["session_id"],
            turn_id,
            "agent_output",
            "Supervisor",
            {
                "summary": "本项回答已生成",
                "blocks": [
                    block.model_dump(exclude_none=True) for block in answer.blocks
                ],
            },
            request_id=request["request_id"],
        )
        if direct and answer.blocks:
            revision = self.stores.get(
                "request_revisions", request["current_revision_id"]
            )
            request["output_refs"] = {
                item["name"]: {
                    "kind": "event",
                    "id": event["event_id"],
                    "block_id": answer.blocks[0].block_id,
                }
                for item in revision["spec"]["outputs"]
            }
        request["updated_at"] = now()
        self.stores.save_many(
            [("requests", request)],
            expected_revisions={request["request_id"]: request["current_revision_id"]},
        )

    def respond(self, session_id, turn_id, answer, request_ids, status="completed"):
        for index, block in enumerate(answer.blocks, 1):
            block.block_id = f"block_{index:03d}"
            for ref in block.refs:
                self.requests.validate_ref(
                    session_id, ref.model_dump(exclude_none=True)
                )
        event = self.stores.event(
            session_id,
            turn_id,
            "assistant_response",
            "Supervisor",
            {
                "blocks": [
                    block.model_dump(exclude_none=True) for block in answer.blocks
                ]
            },
        )
        result = self.snapshot(session_id, turn_id, request_ids, status)
        result.update(
            blocks=event["payload"]["blocks"], response_event_id=event["event_id"]
        )
        try:
            compress(self.stores, self.model, session_id, self.requests.validate_ref)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Session summary compression deferred"
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
                artifact
                for item in requests
                for artifact in self.stores.list(
                    "artifacts", "request_id", item["request_id"]
                )
            ],
            "blocks": [],
            "snapshot_id": self.registry.snapshot_id,
            "semantic_version": self.registry.catalog["version"],
        }

    def resume(self, user_id, request_id, continue_analysis=True):
        request = self.stores.owned_request(user_id, request_id)
        with self._lock(request["session_id"]):
            if request["status"] == "archived":
                raise ValueError("历史归档需求不能直接恢复")
            turn_id = self.stores.new_id("turn")
            query = self.stores.get(
                "request_revisions", request["current_revision_id"]
            )["spec"]["description"]
            self.stores.event(
                request["session_id"],
                turn_id,
                "user_message",
                "User",
                {"content": "继续执行：" + query},
                request_id=request_id,
            )
            if continue_analysis:
                answer, state = self.run(request, turn_id, query)
            else:
                answer = self.supervisor.answer(
                    request["session_id"],
                    self.context.answer(request, query),
                    self.emitter(request["session_id"], turn_id, request_id),
                )
                state = "partial"
            self.finish_operation(request, turn_id, answer)
            return self.respond(
                request["session_id"], turn_id, answer, [request_id], state
            )
