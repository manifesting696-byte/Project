"""Application use cases coordinating the five Agents and four authoritative stores."""
from threading import Lock

from ..agents.capabilities import CAPABILITIES
from ..agents.specialists import Specialists
from ..agents.supervisor import Supervisor
from ..context.manager import ContextManager
from ..core.config import MAX_REPLANS
from ..core.model import ModelClient
from ..core.models import Answer, AnswerBlock
from ..persistence.artifacts import Artifacts
from ..persistence.stores import Stores, now
from ..persistence.summaries import compress
from ..pipeline.scheduler import Scheduler
from ..query.gateway import QueryGateway
from ..semantic.registry import Registry
from .tasks import Tasks


class Application:
    def __init__(self, stores=None, model=None, registry=None, query_gateway=None, specialist_overrides=None):
        self.stores = stores or Stores()
        self.model = model or ModelClient()
        self.registry = registry or Registry()
        self.artifacts = Artifacts(self.stores)
        self.tasks = Tasks(self.stores)
        self.context = ContextManager(self.stores, self.artifacts, self.tasks, self.registry)
        self.supervisor = Supervisor(self.model, self.context, self.stores, self.artifacts)
        self.query_gateway = query_gateway or QueryGateway(self.registry)
        self.specialists = Specialists(self.model, self.registry, self.artifacts,
                                       self.query_gateway, overrides=specialist_overrides)
        self.scheduler = Scheduler(self.stores, self.context, self.specialists)
        self._locks, self._guard = {}, Lock()

    def _lock(self, session_id):
        with self._guard:
            return self._locks.setdefault(session_id, Lock())

    def ask(self, user_id, query, session_id=None, constraints=None, pause=False, preview=False):
        session = self.stores.session(user_id, session_id, query)
        session_id = session["session_id"]
        with self._lock(session_id):
            turn_id = self.stores.new_id("turn") # turn id
            self.stores.event(session_id, turn_id, "user_message", "User", {"content": query}) # 创建user_message的event
            emit = self.emitter(session_id, turn_id) # 绑定和supervisor有关的event
            intent = self.supervisor.intent(session_id, query, emit) 
            intent.constraints_patch.update(constraints or {})
            self.tasks.canonicalize(intent)
            intent_event = self.stores.event(session_id, turn_id, "intent_resolved", "Supervisor",
                                              {"intent": intent.model_dump(exclude_none=True)})
            if intent.dialogue_action == "clarify": # 这个直接返回中间状态+clarify
                return self.respond(session_id, turn_id, Answer(blocks=[AnswerBlock(kind="text", content=intent.clarification or "请补充需要分析的对象和条件。")]), status="needs_clarification")
            if intent.dialogue_action == "independent":
                answer = self.supervisor.answer(session_id,
                    {**self.context.initial(session_id, query), "available_capabilities": CAPABILITIES,
                     "database_metadata": self.registry.overview()}, emit)
                return self.respond(session_id, turn_id, answer)
            task, _ = self.tasks.revise(session_id, turn_id, intent, intent_event["event_id"])
            intent_data = intent.model_dump(exclude_none=True)
            task["current_turn_id"], task["current_intent"], task["current_query"] = turn_id, intent_data, query
            self.stores.put("tasks", task)
            plan = self.supervisor.plan(task, intent_data, self.emitter(session_id, turn_id, task["task_id"]))
            self.apply_plan(task, turn_id, plan)
            if preview:
                task["status"] = "paused"
                self.stores.put("tasks", task)
                return self.snapshot(session_id, turn_id, task, "waiting_input")
            return self.run(task, turn_id, query, intent_data, pause)

    def emitter(self, session_id, turn_id, task_id=None):
        return lambda kind, payload: self.stores.event(session_id, turn_id, kind, "Supervisor", payload, task_id)

    def apply_plan(self, task, turn_id, plan):
        for attempt in range(2):
            try:
                self.tasks.apply_plan(task, turn_id, plan)
                break
            except ValueError as exc:
                if attempt:
                    raise
                plan = self.supervisor.plan(task, task.get("current_intent", {}),
                    self.emitter(task["origin_session_id"], turn_id, task["task_id"]),
                    [{"plan_validation_error": str(exc), "rejected_proposal": plan.model_dump(exclude_none=True)}])
        self.stores.event(task["origin_session_id"], turn_id, "agent_output", "Supervisor",
            {"summary": plan.reason, "plan_version": task["plan_version"],
             "cancelled": plan.cancel_subtask_ids}, task["task_id"])

    def run(self, task, turn_id, query, intent, pause=False):
        emit = self.emitter(task["origin_session_id"], turn_id, task["task_id"])
        completion = None
        for _ in range(MAX_REPLANS + 1):
            execution = self.scheduler.run(task, turn_id, pause)
            pause = False
            if execution.get("paused"):
                return self.snapshot(task["origin_session_id"], turn_id, task, "waiting_input")
            if execution["decision_required"]:
                plan = self.supervisor.plan(task, intent, emit, execution["new_results"])
                self.apply_plan(task, turn_id, plan)
                continue
            completion = self.supervisor.check(task, query, emit)
            self.validate_completion(task, completion)
            if completion.next_action != "replan":
                break
            plan = self.supervisor.plan(task, intent, emit,
                [{"completion": completion.model_dump(exclude_none=True), "results": execution["new_results"]}])
            self.apply_plan(task, turn_id, plan)
        complete = (completion is not None and completion.next_action == "answer"
                    and all(item.status == "completed" for item in completion.requirements)
                    and not any(node["status"] in ("pending", "running") for node in
                                self.stores.list("subtasks", "task_id", task["task_id"])))
        task["status"] = "completed" if complete else "paused"
        task["updated_at"] = now()
        task["completion"] = completion.model_dump(exclude_none=True) if completion else {"reason": "本次规划预算已用完，仍有未完成需求"}
        self.stores.put("tasks", task)
        answer = self.supervisor.answer(task["origin_session_id"],
            self.context.answer(task, query, task["completion"]), emit)
        return self.respond(task["origin_session_id"], turn_id, answer, task,
                            "completed" if complete else "partial")

    def validate_completion(self, task, completion):
        revision = self.stores.get("task_revisions", task["current_revision_id"])
        expected = {r["request_id"] for r in revision["requirements"]}
        actual = [r.request_id for r in completion.requirements]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("需求验收必须覆盖每一项当前需求")
        nodes = self.stores.list("subtasks", "task_id", task["task_id"])
        for check in completion.requirements:
            for ref in check.refs:
                self.tasks.validate_ref(task["origin_session_id"], ref.model_dump(exclude_none=True))
            if check.status == "completed":
                if not check.refs:
                    raise ValueError("完成的需求必须有实际证据")
                completed = [n for n in nodes if n["status"] == "completed" and check.request_id in n["request_ids"]]
                valid = {n["subtask_id"] for n in completed} | {r["id"] for n in completed for r in n["produced_outputs"].values()}
                revision_request = next(r for r in revision["requirements"] if r["request_id"] == check.request_id)
                valid |= {b["source"]["id"] for b in revision_request["inputs"] if b["source"]["kind"] == "artifact"}
                if any(ref.id not in valid for ref in check.refs):
                    raise ValueError("完成证据不属于当前需求的有效执行结果")

    def respond(self, session_id, turn_id, answer, task=None, status="completed"):
        requirements = None
        if task and task.get("current_revision_id"):
            revision = self.stores.get("task_revisions", task["current_revision_id"])
            requirements = {item["request_id"]: item for item in revision["requirements"]}
        for index, block in enumerate(answer.blocks, 1):
            block.block_id = f"block_{index:03d}"
            for ref in block.refs:
                self.tasks.validate_ref(session_id, ref.model_dump(exclude_none=True), requirements)
            if block.kind != "text":
                if not block.refs or any(ref.kind != "artifact" for ref in block.refs):
                    raise ValueError("展示 Block 必须引用真实产物")
                allowed_types = {"table": {"query_result"}, "chart": {"chart"}, "report": {"report"}}[block.kind]
                if any(self.artifacts.inspect(ref.id)["type"] not in allowed_types for ref in block.refs):
                    raise ValueError("回答展示类型与实际产物不一致")
        event = self.stores.event(session_id, turn_id, "assistant_response", "Supervisor",
            {"blocks": [block.model_dump(exclude_none=True) for block in answer.blocks]}, task["task_id"] if task else None)
        result = self.snapshot(session_id, turn_id, task, status)
        result.update(blocks=event["payload"]["blocks"], response_event_id=event["event_id"])
        # Compression is optional and cannot change an already delivered answer.
        try:
            compress(self.stores, self.model, session_id, self.tasks.validate_ref)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Session summary compression deferred")
        return result

    def snapshot(self, session_id, turn_id, task=None, status=None):
        if status is None:
            status = {"active": "running", "paused": "waiting_input", "completed": "completed"}[task["status"]] if task else "completed"
        nodes = self.stores.list("subtasks", "task_id", task["task_id"]) if task else []
        results = self.stores.list("artifacts", "task_id", task["task_id"]) if task else []
        return {"status": status, "session_id": session_id, "turn_id": turn_id,
                "task": task, "revision": self.stores.get("task_revisions", task["current_revision_id"]) if task else None,
                "subtasks": nodes, "artifacts": results, "blocks": [], "snapshot_id": self.registry.snapshot_id,
                "semantic_version": self.registry.catalog["version"]}

    def resume(self, user_id, task_id, continue_analysis=True):
        task = self.stores.owned_task(user_id, task_id)
        with self._lock(task["origin_session_id"]):
            if task["status"] != "paused":
                raise ValueError("任务当前没有暂停")
            turn_id = task["current_turn_id"]
            if not continue_analysis:
                answer = self.supervisor.answer(task["origin_session_id"], self.context.answer(task, task["current_query"]),
                                               self.emitter(task["origin_session_id"], turn_id, task_id))
                return self.respond(task["origin_session_id"], turn_id, answer, task, "partial")
            task["status"] = "active"
            self.stores.put("tasks", task)
            return self.run(task, turn_id, task["current_query"], task["current_intent"])
