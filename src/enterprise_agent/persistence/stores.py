"""Four authoritative stores in one SQLite database; content is managed separately."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from ..core.config import WORK
from ..core.models import Session, SessionEvent, Task, TaskRevision, Subtask, Artifact, SessionSummary


def now():
    return datetime.now(timezone.utc).isoformat()


class Stores:
    def __init__(self, directory: Path = WORK):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "agent.sqlite"
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sequences(kind TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,body TEXT NOT NULL,UNIQUE(session_id,seq));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_revisions(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS subtasks(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_summaries(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS task_session ON tasks(session_id);
            CREATE INDEX IF NOT EXISTS subtask_task ON subtasks(task_id);
            CREATE INDEX IF NOT EXISTS artifact_task ON artifacts(task_id);
            """)

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

    def new_id(self, kind):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("INSERT INTO sequences VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET value=value+1 RETURNING value", (kind,)).fetchone()
            return f"{kind}_{row[0]:03d}"

    def get(self, table, key):
        if table not in TABLES:
            raise ValueError("未知存储")
        with self.db() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError(f"对象不存在：{key}")
        return json.loads(row[0])

    def put(self, table, record):
        record = RECORD_MODELS[table].model_validate(record).model_dump(exclude_none=True)
        key, parent = TABLES[table]
        columns = ["id", parent, "body"]
        parent_value = record["origin_session_id"] if table == "tasks" else record[parent]
        values = [record[key], str(parent_value), json.dumps(record, ensure_ascii=False)]
        if table == "session_events":
            columns.insert(2, "seq")
            values.insert(2, record["seq"])
        with self.db() as db:
            db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in values)}) "
                       "ON CONFLICT(id) DO UPDATE SET body=excluded.body", values)
        return record

    def list(self, table, parent, value):
        if table not in TABLES or parent != TABLES[table][1]:
            raise ValueError("无效存储检索")
        with self.db() as db:
            rows = db.execute(f"SELECT body FROM {table} WHERE {parent}=? ORDER BY rowid", (str(value),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def session(self, user_id, session_id=None, title="新会话"):
        if session_id:
            item = self.get("sessions", session_id)
            if item["user_id"] != str(user_id):
                raise PermissionError("无权访问此会话")
            return item
        return self.put("sessions", {"session_id": self.new_id("session"), "user_id": str(user_id),
            "title": title[:80], "last_active_task_id": None, "last_response_event_id": None,
            "last_seq": 0, "created_at": now(), "updated_at": now()})

    def event(self, session_id, turn_id, event_type, actor, payload, task_id=None, subtask_id=None):
        event_id = self.new_id("event")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            session = json.loads(db.execute("SELECT body FROM sessions WHERE id=?", (session_id,)).fetchone()[0])
            session["last_seq"] += 1
            session["updated_at"] = now()
            if event_type == "assistant_response":
                session["last_response_event_id"] = event_id
            record = {"event_id": event_id, "session_id": session_id, "seq": session["last_seq"],
                "turn_id": turn_id, "event_type": event_type, "actor": actor, "payload": payload,
                "task_id": task_id, "subtask_id": subtask_id, "created_at": now()}
            record = SessionEvent.model_validate(record).model_dump(exclude_none=True)
            db.execute("INSERT INTO session_events VALUES(?,?,?,?)", (event_id, session_id, record["seq"], json.dumps(record, ensure_ascii=False)))
            db.execute("UPDATE sessions SET body=? WHERE id=?", (json.dumps(session, ensure_ascii=False), session_id))
        return record

    def activate(self, session_id, task_id):
        item = self.get("sessions", session_id)
        item["last_active_task_id"] = task_id
        return self.put("sessions", item)

    def owned_task(self, user_id, task_id):
        task = self.get("tasks", task_id)
        self.session(user_id, task["origin_session_id"])
        return task


TABLES = {"sessions": ("session_id", "user_id"), "session_events": ("event_id", "session_id"),
          "tasks": ("task_id", "session_id"), "task_revisions": ("revision_id", "task_id"),
          "subtasks": ("subtask_id", "task_id"), "artifacts": ("artifact_id", "task_id"),
          "session_summaries": ("summary_id", "session_id")}

RECORD_MODELS = {"sessions": Session, "session_events": SessionEvent, "tasks": Task,
                 "task_revisions": TaskRevision, "subtasks": Subtask, "artifacts": Artifact,
                 "session_summaries": SessionSummary}
