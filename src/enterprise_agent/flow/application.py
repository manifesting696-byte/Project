"""Turn orchestration over persistent Requests and their execution Task DAGs."""

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from enterprise_agent.settings import ROOT
from enterprise_agent.domain.answer import Response
from enterprise_agent.plugins.builtin_catalog import catalog
from enterprise_agent.plugins.profiles import environment_patches, load_profile
from enterprise_agent.plugins.runtime import Runtime, Service
from enterprise_agent.ledger.ledger import now


class Application:
    stores = Service()
    model = Service()
    registry = Service()
    database = Service()
    artifacts = Service()
    requests = Service()
    tasks = Service()
    context = Service()
    supervisor = Service()
    query_gateway = Service()
    specialists = Service()
    preparation = Service()
    scheduler = Service()
    responses = Service()
    checkpoints = Service()
    execution = Service()
    turns = Service()

    def __init__(
        self,
        stores=None,
        model=None,
        registry=None,
        query_gateway=None,
        specialist_overrides=None,
        profile=None,
        plugin_config=None,
        plugin_patches=None,
        plugins=(),
    ):
        self._locks, self._guard = {}, Lock()
        self._plugin_path = Path(
            plugin_config
            or os.getenv("AGENT_PLUGIN_CONFIG", str(ROOT / "config" / "harness.yaml"))
        )
        self._patches = (
            environment_patches() if plugin_patches is None else tuple(plugin_patches)
        )
        self.profile = profile or os.getenv("AGENT_PROFILE")
        self.runtime = Runtime(
            catalog=catalog(
                {
                    "stores": stores,
                    "model": model,
                    "registry": registry,
                    "query_gateway": query_gateway,
                    "specialist_overrides": specialist_overrides,
                }
            )
            + list(plugins),
            host={"application": self},
        )
        try:
            self.reload_profile()
            if stores is None:
                self.recover_interrupted()
        except BaseException:
            self.runtime.close()
            raise

    def reload_profile(self, profile=None):
        selected, specs = load_profile(
            self._plugin_path, profile or self.profile, self._patches
        )
        result = self.runtime.configure(specs)
        self.profile = selected
        return {**result, "profile": selected}

    def close(self):
        self.runtime.close()

    def available_tables(self):
        with self.runtime.operation():
            return self.registry.available_tables()

    def change_tables(self, action, **options):
        if action not in ("enroll", "remove", "refresh"):
            raise ValueError("未知目录操作")
        with self.runtime.maintenance():
            self.runtime.events.emit(
                "catalog/before_change",
                {"action": action, "table": options.get("name")},
            )
            result = getattr(self.registry, action)(**options)
            self.runtime.events.notify(
                "catalog/changed",
                {
                    "action": action,
                    "table": options.get("name"),
                    "catalog_version": self.registry.catalog_version,
                },
            )
            return result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _lock(self, session_id):
        with self._guard:
            local_lock = self._locks.setdefault(session_id, Lock())
        with local_lock, self.stores.session_lock(session_id):
            yield

    def emitter(self, session_id, turn_id, request_id=None):
        def emit(kind, payload):
            record = self.stores.event(
                session_id, turn_id, kind, "Supervisor", payload, request_id=request_id
            )
            self.runtime.events.notify("session/event", record)
            return record

        return emit

    def ask(
        self,
        user_id,
        query,
        session_id=None,
        constraints=None,
        pause=False,
        preview=False,
    ):
        with self.runtime.operation():
            session = self.stores.session(user_id, session_id, query)
            session_id = session["session_id"]
            with self._lock(session_id):
                turn_id = self.stores.new_id("turn")
                self.stores.event(
                    session_id, turn_id, "user_message", "User", {"content": query}
                )
                return self.turns.run(
                    session_id, turn_id, query, constraints or {}, pause, preview
                )

    def recover_interrupted(self):
        with self.stores.db() as db:
            requests = [row[0] for row in db.execute("SELECT body FROM requests")]
        resumable = {
            item["request_id"] for item in requests if self.execution.active(item)
        }
        self.stores.recover_interrupted(resumable)

    def resume_turn(self, user_id, session_id, turn_id):
        with self.runtime.operation():
            self.stores.session(user_id, session_id)
            with self._lock(session_id):
                return self.turns.resume(session_id, turn_id)

    def clarification_block(self, session_id, turn_id, item, owner=None):
        refs = [
            ref.model_dump(exclude_none=True)
            for ref in item.refs
            if ref.kind == "artifact" or self.stores.exists("requests", ref.id)
        ]
        sources = sorted(self.requests.artifact_scope(refs))
        return self.responses.message(
            session_id, turn_id, item.question, owner, sources
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
        self, session_id, turn_id, operation, request, previous, pause, preview
    ):
        emit = self.emitter(session_id, turn_id, operation.request_id)
        visible_names = None
        if previous:
            old_outputs = {item["name"]: item for item in previous["spec"]["outputs"]}
            current_outputs = {
                item["name"]: item for item in request["spec"]["outputs"]
            }
            changed = {
                name
                for name, item in current_outputs.items()
                if old_outputs.get(name) != item
            }
            if changed or old_outputs.keys() != current_outputs.keys():
                visible_names = sorted(changed)
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

    def apply_plan(self, request, turn_id, plan, workflow_key=None):
        for attempt in range(2):
            try:
                self.tasks.apply_plan(request, turn_id, plan, workflow_key=workflow_key)
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
        with self.runtime.operation():
            return self.execution.start(request, turn_id, query, pause, output_names)

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
            "blocks": [
                block.model_dump(exclude_none=True) for block in response.blocks
            ],
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

    def materialize_answer(
        self,
        request,
        turn_id,
        answer,
        extra_refs=(),
        output_names=None,
        bind=False,
        workflow_key=None,
        answer_context=None,
    ):
        for attempt in range(2):
            try:
                return self.responses.materialize(
                    request,
                    turn_id,
                    answer,
                    extra_refs,
                    output_names,
                    bind,
                    workflow_key,
                )
            except ValueError as exc:
                if attempt:
                    raise
                context = (
                    dict(answer_context)
                    if answer_context is not None
                    else self.context.answer(
                        request,
                        request["spec"]["description"],
                        output_names=output_names,
                    )
                )
                context["validation_feedback"] = {
                    "error": str(exc),
                    "rejected_answer": answer.model_dump(exclude_none=True),
                }
                answer = self.supervisor.answer(
                    request["session_id"],
                    context,
                    self.emitter(request["session_id"], turn_id, request["request_id"]),
                )

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
                "blocks": [
                    block.model_dump(exclude_none=True) for block in answer.blocks
                ],
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
            + [
                binding["source"]
                for item in requests
                for binding in item["spec"]["inputs"]
            ]
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

    def resume(self, user_id, request_id, continue_analysis=True, answer=None):
        with self.runtime.operation():
            return self._resume(user_id, request_id, continue_analysis, answer)

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
                            "本项未完成：" + str(exc)[:600],
                            request,
                        )
                    ]
                )
                state = "failed"
            return self.respond(
                request["session_id"], turn_id, answer, [request_id], state
            )
