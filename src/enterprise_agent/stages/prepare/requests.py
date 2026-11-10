"""Persistent request identity, immutable specifications and reference validation."""

from copy import deepcopy

from enterprise_agent.domain.errors import RepairableError
from enterprise_agent.domain.request import Request, RequestRevision
from enterprise_agent.ledger.ledger import now
from enterprise_agent.ledger.references import validate_ref
from enterprise_agent.stages.intent.normalize import normalize, order_operations
from enterprise_agent.stages.prepare.bindings import plan_bindings
from enterprise_agent.stages.prepare.validate import validate_specs
from enterprise_agent.stages.verify.evidence import (
    artifact_scope,
    outputs,
    resolve,
    resolve_delivery,
)


class Requests:
    normalize = normalize
    order_operations = staticmethod(order_operations)
    validate_specs = validate_specs
    resolve = resolve
    resolve_delivery = resolve_delivery
    outputs = outputs
    artifact_scope = artifact_scope
    plan_bindings = plan_bindings
    validate_ref = validate_ref

    def __init__(self, stores):
        self.stores = stores

    def prepare_revision(
        self, session_id, turn_id, operation, spec, event_id, pending=None
    ):
        request_id = operation.request_id
        old = (
            self.stores.get("requests", request_id)
            if operation.action == "update_task"
            else None
        )
        previous = (
            self.stores.get("request_revisions", old["current_revision_id"])
            if old
            else None
        )
        spec_data = spec.model_dump(exclude_none=True)
        for binding in spec_data["inputs"]:
            self.validate_ref(session_id, binding["source"], pending)
            if (
                binding["source"]["kind"] == "request"
                and binding["source"]["id"] == request_id
            ):
                raise RepairableError(
                    "复用目标需求的旧结果时，请绑定具体历史 Artifact",
                    code="prepare.invalid_proposal",
                )
        if previous and previous["spec"] == spec_data:
            return {
                "request": old,
                "revision": previous,
                "records": [],
                "expected_revisions": {request_id: previous["revision_id"]},
                "events": [],
            }
        revision = RequestRevision(
            revision_id=self.stores.new_id("rev"),
            request_id=request_id,
            parent_revision_id=previous["revision_id"] if previous else None,
            source_intent_event_id=event_id,
            spec=spec,
            created_at=now(),
        ).model_dump(exclude_none=True)
        request = Request(
            request_id=request_id,
            session_id=session_id,
            title=spec.description[:80],
            created_by_turn_id=old["created_by_turn_id"] if old else turn_id,
            current_revision_id=revision["revision_id"],
            plan_version=old["plan_version"] if old else 0,
            status="pending",
            spec=spec,
            created_at=old["created_at"] if old else now(),
            updated_at=now(),
        ).model_dump(exclude_none=True)
        changes = []
        if old:
            changes = self.invalidate(old, previous["spec"], spec_data)
        expected = {request_id: previous["revision_id"] if previous else None}
        expected.update(
            {
                item["request_id"]: item["current_revision_id"]
                for table, item in changes
                if table == "requests"
            }
        )
        return {
            "request": request,
            "revision": revision,
            "records": [
                *changes,
                ("request_revisions", revision),
                ("requests", request),
            ],
            "expected_revisions": expected,
            "events": [
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "event_type": "request_revised",
                    "actor": "Supervisor",
                    "request_id": request_id,
                    "payload": {"revision_id": revision["revision_id"]},
                }
            ],
        }

    def revise(self, session_id, turn_id, operation, spec, event_id):
        prepared = self.prepare_revision(session_id, turn_id, operation, spec, event_id)
        self.stores.save_many(
            prepared["records"],
            expected_revisions=prepared["expected_revisions"],
            events=prepared["events"],
        )
        return prepared["request"], prepared["revision"]

    def invalidate(self, request, previous, current):
        nodes = self.stores.session_tasks(request["session_id"])
        owners = {
            item["request_id"]: item
            for item in self.stores.list(
                "requests", "session_id", request["session_id"]
            )
        }
        old_outputs = {item["name"]: item for item in previous["outputs"]}
        new_outputs = {item["name"]: item for item in current["outputs"]}
        changed_outputs = {
            name for name, item in old_outputs.items() if new_outputs.get(name) != item
        }
        changed_data = (
            previous["description"] != current["description"]
            or previous["parameters"] != current["parameters"]
            or (
                previous["inputs"] != current["inputs"]
                and not self.presentation_only_inputs(
                    request, previous, current, changed_outputs
                )
            )
        )
        bindings = self.plan_bindings(request)
        sources = {
            bindings[name]["id"]
            for name in changed_outputs
            if name in bindings and bindings[name]["kind"] == "task"
        }
        affected = {
            node["task_id"]
            for node in nodes
            if node["request_id"] == request["request_id"]
            and node["status"] not in ("superseded", "cancelled")
            and (
                changed_data
                or node["task_id"] in sources
                or (
                    changed_outputs
                    and not sources
                    and node["assigned_agent"] == "PresentationAgent"
                )
            )
        }
        changed_deliveries = {
            request["request_id"]: set(old_outputs) if changed_data else changed_outputs
        }
        fixed_inputs = {
            key: self.artifact_scope(
                [
                    binding["source"]
                    for binding in owner["spec"]["inputs"]
                    if binding["source"]["kind"] in ("artifact", "event")
                ],
                follow_requests=False,
            )
            for key, owner in owners.items()
        }
        while True:
            previous_affected = set(affected)
            previous_owners = set(changed_deliveries)
            for key, owner in owners.items():
                if key in changed_deliveries:
                    continue
                if any(
                    binding["source"]["kind"] == "request"
                    and changed_deliveries.get(binding["source"]["id"])
                    and (
                        binding["source"].get("output") is None
                        or binding["source"]["output"]
                        in changed_deliveries[binding["source"]["id"]]
                    )
                    for binding in owner["spec"]["inputs"]
                ):
                    changed_deliveries[key] = {
                        item["name"] for item in owner["spec"]["outputs"]
                    }
                    affected.update(
                        node["task_id"]
                        for node in nodes
                        if node["request_id"] == key
                        and node["status"] not in ("superseded", "cancelled")
                    )
            artifacts = {
                ref["id"]
                for node in nodes
                if node["task_id"] in affected
                for ref in node["produced_outputs"].values()
                if ref["kind"] == "artifact"
            }
            affected.update(
                node["task_id"]
                for node in nodes
                if node["status"] not in ("superseded", "cancelled")
                and (
                    set(node["depends_on"]) & affected
                    or any(
                        binding["source"]["kind"] == "artifact"
                        and binding["source"]["id"] in artifacts
                        and binding["source"]["id"]
                        not in fixed_inputs[node["request_id"]]
                        for binding in node["input_bindings"]
                    )
                )
            )
            for node in nodes:
                key = node["request_id"]
                if node["task_id"] in affected and key not in changed_deliveries:
                    changed_deliveries[key] = {
                        item["name"] for item in owners[key]["spec"]["outputs"]
                    }
            if (
                affected == previous_affected
                and set(changed_deliveries) == previous_owners
            ):
                break
        changes = []
        for node in nodes:
            if node["task_id"] in affected:
                updated = deepcopy(node)
                updated["status"] = "superseded"
                changes.append(("tasks", updated))
        for key in changed_deliveries.keys() - {request["request_id"]}:
            dependent = owners[key]
            dependent["status"], dependent["updated_at"] = "pending", now()
            changes.append(("requests", dependent))
        return changes

    def presentation_only_inputs(self, request, previous, current, changed_outputs):
        old = {item["slot"]: item for item in previous["inputs"]}
        new = {item["slot"]: item for item in current["inputs"]}
        outputs = {item["name"]: item for item in current["outputs"]}
        if not changed_outputs or any(
            outputs.get(name, {}).get("content_type") not in ("chart", "report")
            for name in changed_outputs
        ):
            return False
        if any(new.get(slot) != value for slot, value in old.items()):
            return False
        for slot in new.keys() - old.keys():
            ref = new[slot]["source"]
            if ref["kind"] != "artifact":
                return False
            artifact = self.stores.get("artifacts", ref["id"])
            if artifact["request_id"] != request["request_id"] or artifact[
                "content_type"
            ] not in ("chart", "report"):
                return False
        return True
