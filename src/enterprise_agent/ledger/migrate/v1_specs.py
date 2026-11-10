"""Transactional upgrade of the v2 ledger, preserving events and artifact files."""

import json
import sqlite3
from pathlib import Path

from datetime import datetime, timezone

from enterprise_agent.domain.request import RequestRecord
from enterprise_agent.domain.session import SessionEvent


def migrate_request_specs(db):
    """Recover legacy delivery bindings before removing request result caches."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    for row in db.execute("SELECT id, body FROM requests").fetchall():
        request = json.loads(row[1])
        if "results" not in request and "output_refs" not in request:
            continue
        cached = request.pop("results", request.get("output_refs", {}))
        if "output_refs" in request and request["output_refs"] != cached:
            raise ValueError("Conflicting legacy request deliveries")
        request.pop("output_refs", None)
        request.pop("spec", None)
        RequestRecord.model_validate(request)
        events = [
            json.loads(event[0])
            for event in db.execute(
                "SELECT body FROM session_events WHERE session_id=? ORDER BY seq DESC",
                (request["session_id"],),
            )
        ]
        current = next(
            (
                event["payload"]["plan"]["output_bindings"]
                for event in events
                if event["event_type"] == "plan_updated"
                and event.get("request_id") == request["request_id"]
                and event["payload"].get("revision_id")
                == request["current_revision_id"]
            ),
            {},
        )
        if any(name not in current for name in cached):
            session = json.loads(
                db.execute(
                    "SELECT body FROM sessions WHERE id=?", (request["session_id"],)
                ).fetchone()[0]
            )
            sequence = db.execute(
                "INSERT INTO sequences VALUES('event',1) ON CONFLICT(kind) DO UPDATE SET value=value+1 RETURNING value"
            ).fetchone()[0]
            session["last_seq"] += 1
            stamp = datetime.now(timezone.utc).isoformat()
            event = SessionEvent(
                event_id=f"event_{sequence:03d}",
                session_id=session["session_id"],
                seq=session["last_seq"],
                turn_id=request["created_by_turn_id"],
                event_type="plan_updated",
                request_id=request["request_id"],
                actor="Migration",
                created_at=stamp,
                payload={
                    "revision_id": request["current_revision_id"],
                    "plan_version": request["plan_version"],
                    "plan": {
                        "tasks": [],
                        "cancel_task_ids": [],
                        "output_bindings": {**cached, **current},
                        "reason": "Recovered historical delivery bindings",
                    },
                },
            ).model_dump(exclude_none=True)
            db.execute(
                "INSERT INTO session_events VALUES(?,?,?,?)",
                (
                    event["event_id"],
                    event["session_id"],
                    event["seq"],
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            db.execute(
                "UPDATE sessions SET body=? WHERE id=?",
                (
                    json.dumps(session, ensure_ascii=False),
                    session["session_id"],
                ),
            )
        db.execute(
            "UPDATE requests SET body=? WHERE id=?",
            (
                json.dumps(request, ensure_ascii=False),
                row[0],
            ),
        )


def migrate_legacy(db, path: Path):
    columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    if "session_id" not in columns:
        return
    backup = path.with_name("agent.before-request-model.sqlite")
    if not backup.exists():
        destination = sqlite3.connect(backup)
        try:
            db.backup(destination)
        finally:
            destination.close()
    db.execute("BEGIN IMMEDIATE")
    # The second startup process may have waited for another migration.
    if "session_id" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}:
        return
    for name in ("tasks", "task_revisions", "subtasks", "artifacts"):
        db.execute(f"ALTER TABLE {name} RENAME TO legacy_{name}")
    db.execute(
        "CREATE TABLE requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE request_revisions(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE artifacts(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)"
    )

    def records(table):
        return [
            json.loads(row[0])
            for row in db.execute(f"SELECT body FROM {table} ORDER BY rowid")
        ]

    def next_id(kind):
        row = db.execute(
            "INSERT INTO sequences VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET value=value+1 RETURNING value",
            (kind,),
        ).fetchone()
        return f"{kind}_{row[0]:03d}"

    def insert(table, key, parent, item):
        db.execute(
            f"INSERT INTO {table} VALUES(?,?,?)",
            (item[key], item[parent], json.dumps(item, ensure_ascii=False)),
        )

    old_tasks = records("legacy_tasks")
    old_revisions = records("legacy_task_revisions")
    old_nodes = records("legacy_subtasks")
    old_artifacts = records("legacy_artifacts")
    revision_order = {
        item["revision_id"]: index for index, item in enumerate(old_revisions)
    }
    owners = {}
    for revision in old_revisions:
        for requirement in revision["requirements"]:
            owners[requirement["request_id"]] = revision["task_id"]
    primary = {
        task["task_id"]: next(
            (
                r["request_id"]
                for revision in reversed(old_revisions)
                if revision["task_id"] == task["task_id"]
                for r in revision["requirements"]
            ),
            None,
        )
        for task in old_tasks
    }
    node_owner = {}
    for node in old_nodes:
        owner = next(
            (key for key in node["request_ids"] if key in owners),
            primary[node["task_id"]],
        )
        if owner:
            node_owner[node["subtask_id"]] = owner
    node_ids = {key: next_id("task") for key in node_owner}
    revision_ids = {}
    for revision in old_revisions:
        for requirement in revision["requirements"]:
            revision_ids[(revision["revision_id"], requirement["request_id"])] = (
                next_id("rev")
            )

    def translate(value):
        if isinstance(value, list):
            return [translate(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: translate(item) for key, item in value.items()}
        if result.get("kind") == "subtask":
            result["kind"] = "task"
            result["id"] = node_ids.get(result["id"], result["id"])
        return result

    def outputs_for(requirement, revision):
        found = {}
        for node in old_nodes:
            if (
                revision_order.get(node["revision_id"], -1)
                > revision_order[revision["revision_id"]]
            ):
                continue
            if (
                requirement["request_id"] not in node["request_ids"]
                or node["status"] != "completed"
            ):
                continue
            for name, ref in node["produced_outputs"].items():
                artifact = next(
                    (a for a in old_artifacts if a["artifact_id"] == ref["id"]), None
                )
                if artifact:
                    found[name] = (
                        {
                            "query_result": "table",
                            "chart": "chart",
                            "report": "report",
                            "analysis_result": "text",
                        }[artifact["type"]],
                        translate(ref),
                    )
        if not found:
            kind = {
                "visualization": "chart",
                "report": "report",
                "data_analysis": "table",
            }.get(requirement["type"], "text")
            found["result"] = (kind, None)
        return found

    last_revision = {}
    last_requirements = {}
    for revision in old_revisions:
        for requirement in revision["requirements"]:
            key = requirement["request_id"]
            previous = last_revision.get(key)
            outputs = outputs_for(requirement, revision)
            item = {
                "revision_id": revision_ids[(revision["revision_id"], key)],
                "request_id": key,
                "parent_revision_id": previous,
                "source_intent_event_id": revision["source_intent_event_id"],
                "spec": {
                    "description": requirement["description"],
                    "parameters": {
                        **revision["constraints"],
                        **requirement.get("params", {}),
                    },
                    "inputs": translate(requirement.get("inputs", [])),
                    "outputs": [
                        {
                            "name": name,
                            "kind": kind,
                            "description": requirement["description"],
                            "options": {},
                        }
                        for name, (kind, _) in outputs.items()
                    ],
                },
                "created_at": revision["created_at"],
            }
            insert("request_revisions", "revision_id", "request_id", item)
            last_revision[key] = item["revision_id"]
            last_requirements[key] = requirement

    for key, task_id in owners.items():
        task = next(item for item in old_tasks if item["task_id"] == task_id)
        current = next(
            (
                r
                for r in old_revisions
                if r["revision_id"] == task["current_revision_id"]
            ),
            None,
        )
        active = current and any(
            r["request_id"] == key for r in current["requirements"]
        )
        revision = next(
            (
                r
                for r in reversed(old_revisions)
                if r["task_id"] == task_id
                and any(q["request_id"] == key for q in r["requirements"])
            ),
            None,
        )
        outputs = outputs_for(last_requirements[key], revision)
        item = {
            "request_id": key,
            "session_id": task["origin_session_id"],
            "title": last_requirements[key]["description"][:80],
            "created_by_turn_id": task["created_by_turn_id"],
            "current_revision_id": last_revision[key],
            "plan_version": task["plan_version"],
            "status": (
                "completed" if task["status"] == "completed" else "needs_clarification"
            )
            if active
            else "archived",
            "results": {
                name: ref for name, (_, ref) in outputs.items() if ref is not None
            },
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }
        insert("requests", "request_id", "session_id", item)

    for node in old_nodes:
        old_id = node["subtask_id"]
        if old_id not in node_owner:
            continue
        owner = node_owner[old_id]
        item = translate(
            {
                key: value
                for key, value in node.items()
                if key not in ("subtask_id", "request_ids", "task_id", "revision_id")
            }
        )
        item.update(
            task_id=node_ids[old_id],
            request_id=owner,
            revision_id=revision_ids.get(
                (node["revision_id"], owner), last_revision[owner]
            ),
            plan_version=next(
                t["plan_version"] for t in old_tasks if t["task_id"] == node["task_id"]
            ),
            depends_on=[node_ids[key] for key in node["depends_on"] if key in node_ids],
        )
        for binding in item["input_bindings"]:
            source = binding["source"]
            if source["kind"] == "task":
                source["id"] = node_ids.get(source["id"], source["id"])
        if item["status"] == "running":
            item["status"] = "failed"
            item["summary"] = "服务重启前的执行未完成，可重新规划"
        insert("tasks", "task_id", "request_id", item)

    for artifact in old_artifacts:
        owner = node_owner.get(artifact["subtask_id"])
        if owner is None:
            owner = next(
                (key for key in artifact["request_ids"] if key in owners),
                primary[artifact["task_id"]],
            )
        if owner is None:
            raise ValueError("历史产物缺少可恢复的需求归属")
        item = translate(
            {
                key: value
                for key, value in artifact.items()
                if key not in ("request_ids", "subtask_id", "task_id", "revision_id")
            }
        )
        payload = item["payload"]
        method = (
            "business_sql"
            if payload.get("sql_ref")
            else "artifact_compute"
            if payload.get("computation_sql")
            else "analysis"
            if item["type"] == "analysis_result"
            else "presentation"
        )
        payload.setdefault(
            "provenance",
            {
                "method": method,
                **{
                    name: payload[name]
                    for name in ("sql_ref", "snapshot_id", "semantic_version")
                    if name in payload
                },
            },
        )
        item.update(
            request_id=owner,
            revision_id=revision_ids.get(
                (artifact["revision_id"], owner), last_revision[owner]
            ),
            task_id=node_ids.get(artifact["subtask_id"], artifact["subtask_id"]),
        )
        insert("artifacts", "artifact_id", "request_id", item)

    for session in records("sessions"):
        old = session.pop("last_active_task_id", None)
        session["last_active_request_id"] = primary.get(old)
        db.execute(
            "UPDATE sessions SET body=? WHERE id=?",
            (json.dumps(session, ensure_ascii=False), session["session_id"]),
        )
    for event in records("session_events"):
        old_task = event.pop("task_id", None)
        old_node = event.pop("subtask_id", None)
        event["request_id"] = node_owner.get(old_node, primary.get(old_task))
        event["task_id"] = node_ids.get(old_node)
        event = translate(event)
        if event["event_type"] == "assistant_response":
            for block in event["payload"]["blocks"]:
                owner = event["request_id"]
                for ref in block.get("refs", []):
                    if ref["kind"] == "artifact":
                        artifact = next(
                            (
                                item
                                for item in old_artifacts
                                if item["artifact_id"] == ref["id"]
                            ),
                            None,
                        )
                        if artifact:
                            owner = node_owner.get(artifact["subtask_id"], owner)
                            break
                block.setdefault("request_id", owner)
        db.execute(
            "UPDATE session_events SET body=? WHERE id=?",
            (json.dumps(event, ensure_ascii=False), event["event_id"]),
        )
    for summary in records("session_summaries"):
        item = translate(summary)
        db.execute(
            "UPDATE session_summaries SET body=? WHERE id=?",
            (json.dumps(item, ensure_ascii=False), item["summary_id"]),
        )
    db.execute("PRAGMA user_version=3")
