"""Upgrade historical content and response references without changing existing files."""

import json

from enterprise_agent.domain.artifact import Artifact
from enterprise_agent.domain.request import RequestRevision
from enterprise_agent.domain.session import SessionEvent
from enterprise_agent.domain.task import Task


def migrate_unified_artifacts(db, directory):
    if db.execute("PRAGMA user_version").fetchone()[0] >= 4:
        return
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    created = []
    try:
        _migrate(db, directory, created)
    except Exception:
        for path in reversed(created):
            (path / "text.md").unlink(missing_ok=True)
            path.rmdir()
        raise


def _migrate(db, directory, created):
    columns = {row[1] for row in db.execute("PRAGMA table_info(artifacts)")}
    if "session_id" not in columns:
        db.execute("ALTER TABLE artifacts ADD COLUMN session_id TEXT")

    def records(table):
        return [
            json.loads(row[0])
            for row in db.execute(f"SELECT body FROM {table} ORDER BY rowid")
        ]

    def update(table, key, item):
        db.execute(
            f"UPDATE {table} SET body=? WHERE id=?",
            (json.dumps(item, ensure_ascii=False), item[key]),
        )

    requests = {item["request_id"]: item for item in records("requests")}
    tasks = {item["task_id"]: item for item in records("tasks")}
    revisions = {item["revision_id"]: item for item in records("request_revisions")}
    events = records("session_events")
    events.sort(key=lambda event: (event["session_id"], event["seq"]))
    by_event = {event["event_id"]: event for event in events}
    artifacts = {}
    for item in records("artifacts"):
        owner = requests[item["request_id"]]
        task = tasks.get(item.get("task_id"), {})
        item["content_type"] = {"query_result": "table", "analysis_result": "text"}.get(
            item.get("type"), item.get("content_type", item.get("type"))
        )
        item.pop("type", None)
        item["session_id"] = owner["session_id"]
        item["created_by_turn_id"] = task.get(
            "created_by_turn_id", owner["created_by_turn_id"]
        )
        refs = item.pop("source_refs", [])
        item["source_artifact_ids"] = list(
            dict.fromkeys(ref["id"] for ref in refs if ref["kind"] == "artifact")
        )
        if any(ref["kind"] != "artifact" for ref in refs):
            item["payload"]["historical_source_refs"] = refs
        artifacts[item["artifact_id"]] = item

    def new_text(event, content, owner, revision_id, sources):
        root = directory / "content"
        root.mkdir(exist_ok=True)
        while True:
            sequence = db.execute(
                "INSERT INTO sequences VALUES('result',1) ON CONFLICT(kind) DO UPDATE SET value=value+1 RETURNING value"
            ).fetchone()[0]
            key = f"result_{sequence:03d}"
            path = root / key
            if key not in artifacts and not path.exists():
                break
        path.mkdir()
        created.append(path)
        (path / "text.md").write_text(content, encoding="utf-8")
        item = Artifact(
            artifact_id=key,
            content_type="text",
            session_id=event["session_id"],
            created_by_turn_id=event["turn_id"],
            request_id=owner,
            revision_id=revision_id,
            producer="Supervisor",
            title="历史回复",
            summary=content[:160],
            source_artifact_ids=sources,
            content_ref=f"storage://artifacts/{key}/text.md",
            payload={
                "provenance": {
                    "method": "historical_response",
                    "event_id": event["event_id"],
                }
            },
            created_at=event["created_at"],
        ).model_dump(exclude_none=True)
        artifacts[key] = item
        db.execute(
            "INSERT INTO artifacts(id,request_id,body,session_id) VALUES(?,?,?,?)",
            (
                key,
                owner or "",
                json.dumps(item, ensure_ascii=False),
                event["session_id"],
            ),
        )
        return key

    # Recover display ownership as of each historical turn, rather than today's revision.
    active = {}
    block_refs, event_refs, event_blocks, text_cache = {}, {}, {}, {}
    for event in events:
        owner = event.get("request_id")
        if event["event_type"] == "intent_resolved":
            for revision in revisions.values():
                if revision["source_intent_event_id"] == event["event_id"]:
                    active[revision["request_id"]] = revision["revision_id"]
        if event["event_type"] == "request_revised" and owner:
            active[owner] = event["payload"]["revision_id"]
        payload = event["payload"]
        if "blocks" not in payload or payload.get("schema_version") == 2:
            continue
        blocks = []
        for index, old in enumerate(payload["blocks"], 1):
            block_owner = old.get("request_id", owner)
            revision_id = payload.get("revision_id") or active.get(block_owner)
            if revision_id is None and block_owner in requests:
                candidates = [
                    rev
                    for rev in revisions.values()
                    if rev["request_id"] == block_owner
                    and rev["created_at"] <= event["created_at"]
                ]
                revision_id = (
                    max(candidates, key=lambda rev: rev["created_at"])["revision_id"]
                    if candidates
                    else None
                )
            sources = list(
                dict.fromkeys(
                    ref["id"]
                    for ref in old.get("refs", [])
                    if ref["kind"] == "artifact"
                )
            )
            keys = []
            if old.get("content"):
                identity = (
                    event["session_id"],
                    event["turn_id"],
                    block_owner,
                    revision_id,
                    old["content"],
                    tuple(sources),
                )
                if identity not in text_cache:
                    text_cache[identity] = new_text(
                        event, old["content"], block_owner, revision_id, sources
                    )
                keys.append(text_cache[identity])
                keys.extend(
                    key for key in sources if artifacts[key]["content_type"] != "text"
                )
            else:
                keys.extend(sources)
            old_id = old.get("block_id", f"block_{index:03d}")
            for offset, key in enumerate(keys):
                block = {
                    "block_id": old_id if offset == 0 else f"{old_id}_source_{offset}",
                    "artifact_id": key,
                }
                if block_owner:
                    block["request_id"] = block_owner
                if revision_id:
                    block["revision_id"] = revision_id
                blocks.append(block)
            if keys:
                block_refs[(event["event_id"], old_id)] = keys[0]
        payload["blocks"] = blocks
        payload["schema_version"] = 2
        if event["event_type"] == "assistant_response":
            payload.setdefault("status", "completed")
        keys = list(dict.fromkeys(block["artifact_id"] for block in blocks))
        if len(keys) == 1:
            event_refs[event["event_id"]] = keys[0]
        elif keys:
            event_blocks[event["event_id"]] = (keys, active.get(owner))

    def normalize(value):
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        if value.get("kind") == "event":
            event_id = value["id"]
            if (
                not value.get("block_id")
                and event_id not in event_refs
                and event_id in event_blocks
            ):
                keys, revision_id = event_blocks[event_id]
                event = by_event[event_id]
                text = "\n\n".join(
                    (
                        directory
                        / "content"
                        / key
                        / artifacts[key]["content_ref"].rsplit("/", 1)[-1]
                    ).read_text(encoding="utf-8")
                    for key in keys
                    if artifacts[key]["content_type"] == "text"
                )
                event_refs[event_id] = new_text(
                    event,
                    text or "历史回复的结果集合",
                    event.get("request_id"),
                    revision_id,
                    keys,
                )
            if (
                not value.get("block_id")
                and event_id not in event_refs
                and event_id in by_event
            ):
                event = by_event[event_id]
                content = json.dumps(
                    {
                        "event_type": event["event_type"],
                        "actor": event["actor"],
                        "payload": event["payload"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                event_refs[event_id] = new_text(
                    event,
                    content,
                    event.get("request_id"),
                    event["payload"].get("revision_id"),
                    [],
                )
            key = (
                block_refs.get((value["id"], value.get("block_id")))
                if value.get("block_id")
                else event_refs.get(value["id"])
            )
            if key:
                return {"kind": "artifact", "id": key}
            raise ValueError("历史事件引用或回答块不存在")
        return {key: normalize(item) for key, item in value.items()}

    for item in revisions.values():
        for output in item["spec"]["outputs"]:
            output["content_type"] = output.pop("kind", output.get("content_type"))
        item["spec"] = normalize(item["spec"])
        RequestRevision.model_validate(item)
        update("request_revisions", "revision_id", item)
    for item in tasks.values():
        item["input_bindings"] = normalize(item["input_bindings"])
        item["produced_outputs"] = normalize(item["produced_outputs"])
        Task.model_validate(item)
        update("tasks", "task_id", item)
    for event in events:
        if event["event_type"] == "plan_updated":
            event["payload"]["plan"] = normalize(event["payload"]["plan"])
        for block in event["payload"].get("blocks", []):
            if (
                block["artifact_id"] not in artifacts
                or artifacts[block["artifact_id"]]["session_id"] != event["session_id"]
            ):
                raise ValueError("历史回复产物缺失或跨越会话")
        SessionEvent.model_validate(event)
        update("session_events", "event_id", event)
    for item in list(artifacts.values()):
        historical = item["payload"].get("historical_source_refs", [])
        for ref in historical:
            source = normalize(ref)
            if (
                source.get("kind") == "artifact"
                and source["id"] not in item["source_artifact_ids"]
            ):
                item["source_artifact_ids"].append(source["id"])
        for key in item["source_artifact_ids"]:
            if (
                key not in artifacts
                or artifacts[key]["session_id"] != item["session_id"]
            ):
                raise ValueError("历史来源产物缺失或跨越会话")
        Artifact.model_validate(item)
        update("artifacts", "artifact_id", item)
        db.execute(
            "UPDATE artifacts SET session_id=? WHERE id=?",
            (item["session_id"], item["artifact_id"]),
        )
    db.execute("CREATE INDEX IF NOT EXISTS result_session ON artifacts(session_id)")
    db.execute("PRAGMA user_version=4")
