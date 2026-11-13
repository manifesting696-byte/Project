"""Acceptance tests for request identity, revisions, task execution and provenance."""

import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from enterprise_agent.flow.application import Application
from enterprise_agent.ledger.ledger import RECORD_MODELS
from tests.fixtures import FixtureModel
from tests.legacy_fixture import legacy_ledger
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class MigrationTests(AgentTestCase):
    def downgrade_text_ledger(self):
        result = self.completed("什么是平均值？")
        old_key = result["blocks"][0]["artifact_id"]
        with self.engine.stores.db() as db:
            events = [
                json.loads(row[0])
                for row in db.execute("SELECT body FROM session_events ORDER BY seq")
            ]
            event = next(
                item
                for item in events
                if item["event_type"] == "agent_output"
                and item["payload"].get("blocks")
            )
            event_id = event["event_id"]
            for item in events:
                payload = item["payload"]
                if "blocks" in payload:
                    payload.pop("schema_version", None)
                    payload.pop("status", None)
                    payload["blocks"] = [
                        {
                            "block_id": block["block_id"],
                            "request_id": block["request_id"],
                            "kind": "text",
                            "content": self.engine.artifacts.content(
                                block["artifact_id"]
                            ),
                            "refs": [],
                        }
                        for block in payload["blocks"]
                    ]
                if item["event_type"] == "plan_updated":
                    payload["plan"]["output_bindings"] = {
                        "answer": {"kind": "event", "id": event_id}
                    }
                db.execute(
                    "UPDATE session_events SET body=? WHERE id=?",
                    (json.dumps(item, ensure_ascii=False), item["event_id"]),
                )
            for row in db.execute("SELECT id,body FROM request_revisions").fetchall():
                revision = json.loads(row[1])
                for output in revision["spec"]["outputs"]:
                    output["kind"] = output.pop("content_type")
                db.execute(
                    "UPDATE request_revisions SET body=? WHERE id=?",
                    (json.dumps(revision), row[0]),
                )
            db.execute("DELETE FROM artifacts WHERE id=?", (old_key,))
            db.execute("PRAGMA user_version=3")
        return result, old_key, event_id

    def test_conflicting_legacy_deliveries_roll_back_migration(self):
        result = self.completed("分别列出前两个和前三个产品")
        with self.engine.stores.db() as db:
            for index, request in enumerate(result["requests"]):
                stored = {key: value for key, value in request.items() if key != "spec"}
                stored["results"] = result["deliveries"][request["request_id"]]
                if index:
                    stored["output_refs"] = {}
                db.execute(
                    "UPDATE requests SET body=? WHERE id=?",
                    (json.dumps(stored), request["request_id"]),
                )
            before = db.execute("SELECT id, body FROM requests ORDER BY id").fetchall()
            before = [tuple(row) for row in before]
        with self.assertRaises(ValueError):
            Stores(Path(self.directory.name))
        with self.engine.stores.db() as db:
            after = [
                tuple(row)
                for row in db.execute("SELECT id, body FROM requests ORDER BY id")
            ]
        self.assertEqual(after, before)

    def test_failed_migration_rolls_back_and_keeps_backup(self):
        directory = Path(self.directory.name) / "corrupt-legacy"
        legacy_ledger(directory)
        db = sqlite3.connect(directory / "agent.sqlite")
        try:
            revision = json.loads(
                db.execute("SELECT body FROM task_revisions").fetchone()[0]
            )
            revision["requirements"][0].pop("description")
            db.execute("UPDATE task_revisions SET body=?", (json.dumps(revision),))
            db.commit()
        finally:
            db.close()
        with self.assertRaises(KeyError):
            Stores(directory)
        db = sqlite3.connect(directory / "agent.sqlite")
        try:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("subtasks", tables)
            self.assertNotIn("requests", tables)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 2
            )
        finally:
            db.close()
        self.assertTrue((directory / "agent.before-request-model.sqlite").exists())

    def test_request_specs_and_legacy_deliveries_preserve_existing_ledger(self):
        for field in ("results", "output_refs"):
            with self.subTest(field=field):
                result = self.completed()
                request = result["requests"][0]
                deliveries = self.engine.requests.outputs(request)
                artifact_id = deliveries["data"]["id"]
                original_content = self.engine.artifacts.content(artifact_id)
                previous = {
                    key: value for key, value in request.items() if key != "spec"
                }
                previous[field] = deliveries
                with self.engine.stores.db() as db:
                    db.execute(
                        "UPDATE requests SET body=? WHERE id=?",
                        (
                            json.dumps(previous),
                            request["request_id"],
                        ),
                    )
                    for row in db.execute(
                        "SELECT id, body FROM session_events WHERE session_id=?",
                        (request["session_id"],),
                    ).fetchall():
                        event = json.loads(row[1])
                        if event["event_type"] == "plan_updated":
                            event["event_type"] = "agent_output"
                            event["payload"]["summary"] = "Historical execution"
                            db.execute(
                                "UPDATE session_events SET body=? WHERE id=?",
                                (json.dumps(event), row[0]),
                            )
                original_events = self.engine.stores.list(
                    "session_events", "session_id", request["session_id"]
                )
                stores = Stores(Path(self.directory.name))
                self.assertEqual(stores.get("requests", request["request_id"]), request)
                with stores.db() as db:
                    persisted = json.loads(
                        db.execute(
                            "SELECT body FROM requests WHERE id=?",
                            (request["request_id"],),
                        ).fetchone()[0]
                    )
                self.assertNotIn("results", persisted)
                self.assertNotIn("output_refs", persisted)
                self.assertNotIn("spec", persisted)
                self.assertEqual(
                    stores.list("session_events", "session_id", request["session_id"])[
                        :-1
                    ],
                    original_events,
                )
                engine = Application(stores, self.model)
                self.assertEqual(
                    engine.artifacts.content(artifact_id), original_content
                )
                self.assertEqual(engine.requests.outputs(request), deliveries)
                self.assertEqual(
                    engine.requests.resolve(
                        request["session_id"],
                        {
                            "kind": "request",
                            "id": request["request_id"],
                            "output": "data",
                        },
                    ),
                    {"artifact_id": artifact_id},
                )
                self.assertEqual(
                    Stores(Path(self.directory.name)).get(
                        "requests", request["request_id"]
                    ),
                    request,
                )

    def test_migration_preserves_history_artifacts_and_update(self):
        directory = Path(self.directory.name) / "legacy"
        legacy_ledger(directory)
        before = (directory / "content/result_001/data.json").read_bytes()
        stores = Stores(directory)
        self.assertTrue((directory / "agent.before-request-model.sqlite").exists())
        self.assertEqual(len(stores.list("requests", "session_id", "session_001")), 2)
        for table, model in RECORD_MODELS.items():
            with stores.db() as db:
                values = [
                    json.loads(row[0])
                    for row in db.execute(f"SELECT body FROM {table}")
                ]
            for value in values:
                model.model_validate(value)
        engine = Application(stores, FixtureModel())
        result = engine.ask(1, "将刚才的图改为横向条形图", "session_001")
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(result["requests"][0]["request_id"], "req_002")
        self.assertNotIn("QueryAgent", engine.model.calls)
        self.assertEqual(
            (directory / "content/result_001/data.json").read_bytes(), before
        )
        self.assertIn("SELECT", engine.artifacts.sql("result_001"))
        self.assertEqual(
            Stores(directory).get("artifacts", "result_001")["artifact_id"],
            "result_001",
        )

    def test_unified_migration_recovers_historical_text_and_bindings(self):
        result, old_key, event_id = self.downgrade_text_ledger()
        with self.engine.stores.db() as db:
            revision = json.loads(
                db.execute("SELECT body FROM request_revisions").fetchone()[0]
            )
            revision["spec"]["inputs"] = [
                {
                    "slot": "old_text",
                    "source": {
                        "kind": "event",
                        "id": event_id,
                        "block_id": "block_001",
                    },
                }
            ]
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        original_file = self.engine.artifacts.root / old_key / "text.md"
        before = original_file.read_bytes()
        store = Stores(Path(self.directory.name))
        engine = Application(store, FixtureModel())
        request = store.get("requests", result["requests"][0]["request_id"])
        delivery = engine.requests.outputs(request)["answer"]
        self.assertEqual(request["spec"]["inputs"][0]["source"], delivery)
        self.assertEqual(delivery["kind"], "artifact")
        self.assertNotEqual(delivery["id"], old_key)
        self.assertEqual(
            engine.artifacts.content(delivery["id"]), "平均值是总和除以数量。"
        )
        self.assertEqual(original_file.read_bytes(), before)
        event = store.get("session_events", event_id)
        self.assertEqual(event["payload"]["schema_version"], 2)
        self.assertEqual(event["payload"]["blocks"][0]["artifact_id"], delivery["id"])
        self.assertEqual(
            event["payload"]["blocks"][0]["revision_id"], request["current_revision_id"]
        )
        with store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        reopened = Stores(Path(self.directory.name))
        with reopened.db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], count
            )
        self.assertTrue(
            (
                Path(self.directory.name) / "agent.before-unified-artifacts.sqlite"
            ).exists()
        )

    def test_unified_migration_failure_removes_new_content_and_can_retry(self):
        result, old_key, event_id = self.downgrade_text_ledger()
        root = Path(self.directory.name) / "content"
        before = set(root.iterdir())
        with self.engine.stores.db() as db:
            revision = json.loads(
                db.execute("SELECT body FROM request_revisions").fetchone()[0]
            )
            revision["spec"].pop("description")
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        with self.assertRaises(ValidationError):
            Stores(Path(self.directory.name))
        self.assertEqual(set(root.iterdir()), before)
        with self.engine.stores.db() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0
            )
            revision["spec"]["description"] = "什么是平均值？"
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        store = Stores(Path(self.directory.name))
        event = store.get("session_events", event_id)
        self.assertTrue(
            store.exists("artifacts", event["payload"]["blocks"][0]["artifact_id"])
        )
