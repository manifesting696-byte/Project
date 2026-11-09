"""SQLite ledger with atomic request revisions and append-only history."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import WORK
from ..core.models import (
    Artifact,
    Request,
    RequestRevision,
    Session,
    SessionEvent,
    SessionSummary,
    Task,
)
from .migration import migrate_legacy, migrate_request_results


def now():
    return datetime.now(timezone.utc).isoformat()


TABLES = {
    "sessions": ("session_id", "user_id"),
    "session_events": ("event_id", "session_id"),
    "requests": ("request_id", "session_id"),
    "request_revisions": ("revision_id", "request_id"),
    "tasks": ("task_id", "request_id"),
    "artifacts": ("artifact_id", "request_id"),
    "session_summaries": ("summary_id", "session_id"),
}
RECORD_MODELS = {
    "sessions": Session,
    "session_events": SessionEvent,
    "requests": Request,
    "request_revisions": RequestRevision,
    "tasks": Task,
    "artifacts": Artifact,
    "session_summaries": SessionSummary,
}
IMMUTABLE = {"request_revisions", "artifacts", "session_events", "session_summaries"}


class Stores:
    def __init__(self, directory: Path = WORK):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "agent.sqlite"
        with self.db() as db:
            legacy = "session_id" in {
                row[1] for row in db.execute("PRAGMA table_info(tasks)")
            }
            migrate_legacy(db, self.path)
            for statement in (
                "CREATE TABLE IF NOT EXISTS sequences(kind TEXT PRIMARY KEY,value INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS session_events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,UNIQUE(session_id,seq))",
                "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS request_revisions(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS session_summaries(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS request_session ON requests(session_id)",
                "CREATE INDEX IF NOT EXISTS execution_request ON tasks(request_id)",
                "CREATE INDEX IF NOT EXISTS result_request ON artifacts(request_id)",
            ):
                db.execute(statement)
            migrate_request_results(db)
            db.execute("PRAGMA user_version=3")
            if legacy:
                for table, model in RECORD_MODELS.items():
                    for row in db.execute(f"SELECT body FROM {table}"):
                        model.model_validate(json.loads(row[0]))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _next_id(db, kind):
        if not re.fullmatch("[a-z_]+", kind):
            raise ValueError("无效 ID 类型")
        row = db.execute(
            "INSERT INTO sequences VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET value=value+1 RETURNING value",
            (kind,),
        ).fetchone()
        return f"{kind}_{row[0]:03d}"

    def new_id(self, kind):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._next_id(db, kind)

    def get(self, table, key):
        if table not in TABLES:
            raise ValueError("未知存储")
        with self.db() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ValueError(f"对象不存在：{key}")
        return json.loads(row[0])

    def exists(self, table, key):
        if table not in TABLES:
            return False
        with self.db() as db:
            return (
                db.execute(f"SELECT 1 FROM {table} WHERE id=?", (key,)).fetchone()
                is not None
            )

    def _put(self, db, table, item):
        key, parent = TABLES[table]
        encoded = json.dumps(item, ensure_ascii=False)
        if table in IMMUTABLE:
            old = db.execute(
                f"SELECT body FROM {table} WHERE id=?", (item[key],)
            ).fetchone()
            if old:
                previous = (
                    RECORD_MODELS[table]
                    .model_validate(json.loads(old[0]))
                    .model_dump(exclude_none=True)
                )
                if previous != item:
                    raise ValueError("历史快照、事件和产物不可覆盖")
                return
        columns, values = (
            ["id", parent, "body"],
            [item[key], str(item[parent]), encoded],
        )
        if table == "session_events":
            columns.insert(2, "seq")
            values.insert(2, item["seq"])
        db.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in values)}) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            values,
        )

    def save_many(self, records, expected_revisions=None, event=None):
        validated = [
            (
                table,
                RECORD_MODELS[table].model_validate(item).model_dump(exclude_none=True),
            )
            for table, item in records
        ]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for request_id, revision_id in (expected_revisions or {}).items():
                row = db.execute(
                    "SELECT body FROM requests WHERE id=?", (request_id,)
                ).fetchone()
                actual = json.loads(row[0])["current_revision_id"] if row else None
                if actual != revision_id:
                    raise ValueError("需求已被其他执行更新，请重新读取当前版本")
            for table, item in validated:
                self._put(db, table, item)
            if event:
                self._append_event(db, **event)
        return [item for _, item in validated]

    def put(self, table, record):
        return self.save_many([(table, record)])[0]

    def list(self, table, parent, value):
        if table not in TABLES or parent != TABLES[table][1]:
            raise ValueError("无效存储检索")
        with self.db() as db:
            rows = db.execute(
                f"SELECT body FROM {table} WHERE {parent}=? ORDER BY rowid",
                (str(value),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def session(self, user_id, session_id=None, title="新会话"):
        if session_id:
            item = self.get("sessions", session_id)
            if item["user_id"] != str(user_id):
                raise PermissionError("无权访问此会话")
            return item
        return self.put(
            "sessions",
            {
                "session_id": self.new_id("session"),
                "user_id": str(user_id),
                "title": title[:80],
                "last_seq": 0,
                "created_at": now(),
                "updated_at": now(),
            },
        )

    def _append_event(
        self,
        db,
        session_id,
        turn_id,
        event_type,
        actor,
        payload,
        request_id=None,
        task_id=None,
    ):
        session = json.loads(
            db.execute(
                "SELECT body FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
        )
        event_id = self._next_id(db, "event")
        session["last_seq"] += 1
        session["updated_at"] = now()
        if event_type == "assistant_response":
            session["last_response_event_id"] = event_id
        if event_type == "request_revised":
            session["last_active_request_id"] = request_id
        record = SessionEvent(
            event_id=event_id,
            session_id=session_id,
            seq=session["last_seq"],
            turn_id=turn_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            request_id=request_id,
            task_id=task_id,
            created_at=now(),
        ).model_dump(exclude_none=True)
        self._put(db, "session_events", record)
        self._put(db, "sessions", session)
        return record

    def event(
        self,
        session_id,
        turn_id,
        event_type,
        actor,
        payload,
        request_id=None,
        task_id=None,
    ):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._append_event(
                db, session_id, turn_id, event_type, actor, payload, request_id, task_id
            )

    def owned_request(self, user_id, request_id):
        request = self.get("requests", request_id)
        self.session(user_id, request["session_id"])
        return request

    def session_tasks(self, session_id):
        return [
            task
            for request in self.list("requests", "session_id", session_id)
            for task in self.list("tasks", "request_id", request["request_id"])
        ]

    def recover_interrupted(self):
        """Called once by the single-worker application at startup."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            affected = set()
            for row in db.execute("SELECT body FROM tasks").fetchall():
                task = json.loads(row[0])
                if task["status"] == "running":
                    task.update(
                        status="failed",
                        summary="上次服务退出时执行中断，可以重新规划",
                        completed_at=now(),
                    )
                    self._put(db, "tasks", task)
                    affected.add(task["request_id"])
            for row in db.execute("SELECT body FROM requests").fetchall():
                request = json.loads(row[0])
                if request["status"] == "running" or request["request_id"] in affected:
                    request.update(status="pending", updated_at=now())
                    self._put(db, "requests", request)

    def legacy_request_ids(self, task_id):
        with self.db() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_tasks'"
            ).fetchone():
                return []
            row = db.execute(
                "SELECT body FROM legacy_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                return []
            task = json.loads(row[0])
            row = db.execute(
                "SELECT body FROM legacy_task_revisions WHERE id=?",
                (task["current_revision_id"],),
            ).fetchone()
        return (
            [item["request_id"] for item in json.loads(row[0])["requirements"]]
            if row
            else []
        )
