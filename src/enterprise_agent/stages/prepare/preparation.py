"""Prepare complete requirements and initial plans before committing either."""

from copy import deepcopy

from enterprise_agent.domain.errors import RepairableError, VersionConflict
from enterprise_agent.domain.request import RequestSpec
from enterprise_agent.domain.task import Plan
from enterprise_agent.stages.intent.schema import Clarification
from enterprise_agent.stages.prepare.commit import save_bundle
from enterprise_agent.stages.prepare.schema import RequestPreparation


class Preparation:
    commit = save_bundle

    def __init__(self, stores, requests, tasks, supervisor):
        self.stores, self.requests = stores, requests
        self.tasks, self.supervisor = tasks, supervisor

    def propose(
        self,
        session_id,
        operation,
        parameters,
        drafts,
        required_outputs,
        emit,
        feedback=None,
    ):
        previous = None
        baseline = {"revision_id": None, "plan_version": None}
        if operation.action == "update_task":
            request = self.stores.get("requests", operation.request_id)
            baseline = {
                "revision_id": request["current_revision_id"],
                "plan_version": request["plan_version"],
            }
            previous = self.stores.get(
                "request_revisions", request["current_revision_id"]
            )
        confirmed = set()
        if operation.action == "none":
            spec = RequestSpec(
                description=operation.instruction,
                inputs=[
                    {
                        "slot": f"source_{index}",
                        "source": ref.model_dump(exclude_none=True),
                    }
                    for index, ref in enumerate(operation.refs, 1)
                ],
                outputs=[
                    {
                        "name": "answer",
                        "content_type": "text",
                        "description": operation.instruction,
                    }
                ],
            )
            result = RequestPreparation(spec=spec, plan=Plan(reason="直接回答"))
        else:
            upstream = {
                key: value
                for key, value in drafts.items()
                if key != operation.request_id
            }
            result = self.supervisor.prepare(
                session_id,
                operation,
                previous,
                parameters,
                emit,
                upstream,
                required_outputs,
                feedback,
                confirmed,
            )
        if result.spec:
            for key, value in parameters.items():
                if operation.action == "none":
                    if value is not None:
                        result.spec.parameters[key] = value
                elif (value is None and key in result.spec.parameters) or (
                    value is not None and result.spec.parameters.get(key) != value
                ):
                    raise RepairableError(
                        "完整规格需遵循界面参数，并据此生成计划",
                        code="prepare.invalid_proposal",
                    )
            self.validate_sources(operation, previous, result.spec, confirmed)
            if not set(required_outputs) <= {item.name for item in result.spec.outputs}:
                raise RepairableError(
                    "上游规格缺少本轮下游引用的交付名称",
                    code="prepare.invalid_proposal",
                )
        for item in result.clarifications:
            if item.request_id not in (None, operation.request_id):
                raise RepairableError(
                    "澄清目标必须属于当前操作", code="prepare.invalid_proposal"
                )
            item.request_id = operation.request_id
            for ref in item.refs:
                self.requests.validate_ref(
                    session_id, ref.model_dump(exclude_none=True), set(drafts)
                )
        return result, baseline

    def validate_sources(self, operation, previous, spec, confirmed):
        def identity(ref):
            return ref["kind"], ref["id"], ref.get("output")

        current = {
            identity(ref.model_dump(exclude_none=True)) for ref in operation.refs
        }
        inherited = (
            [binding["source"] for binding in previous["spec"]["inputs"]]
            if previous
            else []
        )
        allowed = current | {identity(ref) for ref in inherited} | confirmed
        explicit = [
            ref
            for ref in inherited
            + [ref.model_dump(exclude_none=True) for ref in operation.refs]
            if ref["kind"] == "artifact"
        ]
        allowed.update(
            ("artifact", key, None) for key in self.requests.artifact_scope(explicit)
        )
        sources = {
            identity(binding.source.model_dump(exclude_none=True))
            for binding in spec.inputs
        }
        if not current <= sources:
            raise RepairableError(
                "完整需求遗漏了 Intent 已确认的引用", code="prepare.invalid_proposal"
            )
        if not sources <= allowed:
            raise RepairableError(
                "新增需求来源必须通过工具确认", code="prepare.invalid_proposal"
            )

    def candidates(
        self, session_id, turn_id, operations, proposals, baselines, event_id
    ):
        specs = {key: value.spec for key, value in proposals.items() if value.spec}
        errors, revisions = {}, {}
        for operation in operations:
            key = operation.request_id
            if key not in specs:
                continue
            try:
                stored = (
                    self.stores.get("requests", key)
                    if self.stores.exists("requests", key)
                    else None
                )
                actual = (
                    {
                        "revision_id": stored["current_revision_id"],
                        "plan_version": stored["plan_version"],
                    }
                    if stored
                    else {"revision_id": None, "plan_version": None}
                )
                if actual != baselines[key]:
                    raise VersionConflict(
                        "需求或计划已在准备期间更新，请基于最新状态重新准备"
                    )
                self.requests.validate_specs(session_id, specs, [key])
                revisions[key] = self.requests.prepare_revision(
                    session_id, turn_id, operation, specs[key], event_id, set(specs)
                )
            except Exception as exc:
                errors[key] = exc
        nodes = {
            node["task_id"]: node for node in self.stores.session_tasks(session_id)
        }
        for prepared in revisions.values():
            for table, item in prepared["records"]:
                if table == "tasks":
                    nodes[item["task_id"]] = item
        views = {key: value["request"] for key, value in revisions.items()}
        by_id = {operation.request_id: operation for operation in operations}
        bundles = {}
        for key, prepared in revisions.items():
            try:
                request = deepcopy(prepared["request"])
                request["status"] = "pending"
                if by_id[key].action == "none":
                    plan = {
                        "request": request,
                        "records": [("requests", request)],
                        "event": None,
                    }
                else:
                    bindings = self.requests.plan_bindings(
                        request, prepared["revision"]
                    )
                    plan = self.tasks.prepare_plan(
                        request,
                        turn_id,
                        proposals[key].plan,
                        list(nodes.values()),
                        views,
                        bindings,
                    )
                records = prepared["records"] + plan["records"]
                expected_versions = {
                    item["request_id"]: item["plan_version"]
                    for table, item in prepared["records"]
                    if table == "requests"
                }
                expected_versions[key] = baselines[key]["plan_version"]
                prepared["expected_revisions"][key] = baselines[key]["revision_id"]
                bundles[key] = {
                    "request": plan["request"],
                    "records": records,
                    "expected_revisions": prepared["expected_revisions"],
                    "expected_plan_versions": expected_versions,
                    "events": [
                        *prepared["events"],
                        *([plan["event"]] if plan["event"] else []),
                    ],
                }
            except Exception as exc:
                errors[key] = exc
        return bundles, errors

    def prepare(self, session_id, turn_id, operations, parameters, event_id, emitter):
        proposals, drafts, errors, baselines = {}, {}, {}, {}
        required = {
            operation.request_id: [
                ref.output
                for other in operations
                for ref in other.refs
                if ref.kind == "request" and ref.id == operation.request_id
            ]
            for operation in operations
        }
        for operation in operations:
            key = operation.request_id
            try:
                proposals[key], baselines[key] = self.propose(
                    session_id,
                    operation,
                    parameters,
                    drafts,
                    required[key],
                    emitter(key),
                )
                if proposals[key].spec:
                    drafts[key] = {
                        "request_id": key,
                        "session_id": session_id,
                        "spec": proposals[key].spec.model_dump(exclude_none=True),
                    }
            except Exception as exc:
                errors[key] = exc
        for attempt in range(2):
            bundles, rejected = self.candidates(
                session_id, turn_id, operations, proposals, baselines, event_id
            )
            errors.update(rejected)
            if attempt or not errors:
                break
            for operation in operations:
                key = operation.request_id
                if key not in errors or not isinstance(errors[key], RepairableError):
                    continue
                proposal = proposals.get(key)
                feedback = {
                    "error": str(errors[key]),
                    "rejected_preparation": proposal.model_dump(exclude_none=True)
                    if proposal
                    else None,
                }
                self.stores.event(
                    session_id,
                    turn_id,
                    "agent_output",
                    "Supervisor",
                    {
                        "summary": "合并准备校验失败：" + str(errors[key]),
                        "rejected_preparation": feedback["rejected_preparation"],
                    },
                    request_id=key,
                )
                try:
                    proposals[key], baselines[key] = self.propose(
                        session_id,
                        operation,
                        parameters,
                        drafts,
                        required[key],
                        emitter(key),
                        feedback,
                    )
                    errors.pop(key)
                    drafts.pop(key, None)
                    if proposals[key].spec:
                        drafts[key] = {
                            "request_id": key,
                            "session_id": session_id,
                            "spec": proposals[key].spec.model_dump(exclude_none=True),
                        }
                except Exception as exc:
                    errors[key] = exc
        blocked = set(errors) | {
            key for key, value in proposals.items() if value.clarifications
        }
        while True:
            dependents = {
                key
                for key, value in proposals.items()
                if value.spec
                and key not in blocked
                and any(
                    binding.source.kind == "request" and binding.source.id in blocked
                    for binding in value.spec.inputs
                )
            }
            if not dependents:
                break
            for key in dependents:
                proposals[key] = RequestPreparation(
                    clarifications=[
                        Clarification(
                            request_id=key,
                            subject="上游需求",
                            question="本项依赖的上游需求尚未准备完成，请先补全上游需求。",
                        )
                    ]
                )
                bundles.pop(key, None)
            blocked.update(dependents)
        specs = {
            key: value.spec
            for key, value in proposals.items()
            if value.spec and key not in errors
        }
        ordered = self.requests.order_operations(operations, specs)
        return (
            ordered,
            {key: value for key, value in bundles.items() if key not in errors},
            proposals,
            errors,
        )
