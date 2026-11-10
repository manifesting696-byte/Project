"""PostgreSQL JSONB ledger with transactional revisions and ordered event records."""

import re
from datetime import datetime, timezone
from pathlib import Path

from enterprise_agent.settings import WORK
from enterprise_agent.domain.artifact import Artifact
from enterprise_agent.domain.request import Request, RequestRecord, RequestRevision
from enterprise_agent.domain.session import Session, SessionEvent, SessionSummary
from enterprise_agent.domain.task import Task
from psycopg.types.json import Jsonb

from enterprise_agent.ledger.postgres import Postgres


def now():
    return datetime.now(timezone.utc).isoformat()


TABLES = {
    "sessions": ("session_id", "user_id"),
    "session_events": ("event_id", "session_id"),
    "requests": ("request_id", "session_id"),
    "request_revisions": ("revision_id", "request_id"),
    "tasks": ("task_id", "request_id"),
    "artifacts": ("artifact_id", "session_id"),
    "session_summaries": ("summary_id", "session_id"),
}
RECORD_MODELS = {
    "sessions": Session,
    "session_events": SessionEvent,
    "requests": RequestRecord,
    "request_revisions": RequestRevision,
    "tasks": Task,
    "artifacts": Artifact,
    "session_summaries": SessionSummary,
}
IMMUTABLE = {"request_revisions", "artifacts", "session_events", "session_summaries"}


class Stores:
    def __init__(self, directory: Path = WORK, dsn=None, schema="agent"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.persistence = Postgres(dsn, schema)
        statements = [
            "CREATE TABLE IF NOT EXISTS sequences(kind TEXT PRIMARY KEY,value BIGINT NOT NULL)"
        ]
        for table, (_, parent) in TABLES.items():
            extra = (
                ",seq BIGINT NOT NULL,UNIQUE(session_id,seq)"
                if table == "session_events"
                else ""
            )
            if table == "artifacts":
                extra += ",request_id TEXT"
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {table}(row_id BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,id TEXT PRIMARY KEY,{parent} TEXT NOT NULL,body JSONB NOT NULL{extra})"
            )
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {table}_{parent}_idx ON {table}({parent},row_id)"
            )
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS legacy_tasks(id TEXT PRIMARY KEY,body JSONB NOT NULL)",
                "CREATE TABLE IF NOT EXISTS legacy_task_revisions(id TEXT PRIMARY KEY,body JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS artifact_request_idx ON artifacts(request_id)",
                "CREATE INDEX IF NOT EXISTS event_workflow_idx ON session_events(session_id,(body->'payload'->>'workflow_key'),seq DESC)",
            ]
        )
        try:
            self.persistence.setup(1, statements)
        except BaseException:
            self.persistence.close()
            raise

    def db(self):
        return self.persistence.connection()

    def session_lock(self, session_id):
        return self.persistence.advisory_lock(
            f"enterprise-session:{self.persistence.schema}:{session_id}"
        )

    def close(self):
        self.persistence.close()

    @staticmethod
    def _next_id(db, kind):
        if not re.fullmatch("[a-z_]+", kind):
            raise ValueError("无效 ID 类型")
        row = db.execute(
            "INSERT INTO sequences VALUES(%s,1) ON CONFLICT(kind) DO UPDATE SET value=sequences.value+1 RETURNING value",
            (kind,),
        ).fetchone()
        return f"{kind}_{row[0]:03d}"

    def new_id(self, kind):
        with self.db() as db:
            return self._next_id(db, kind)

    def get(self, table, key):
        if table not in TABLES:
            raise ValueError("未知存储")
        with self.db() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=%s", (key,)).fetchone()
            if row is None:
                raise ValueError(f"对象不存在：{key}")
            item = row[0]
            return self.request_view(db, item) if table == "requests" else item

    @staticmethod
    def request_view(db, item):
        revision = db.execute(
            "SELECT body FROM request_revisions WHERE id=%s",
            (item["current_revision_id"],),
        ).fetchone()
        if revision is None:
            raise ValueError("Request 缺少当前规格版本")
        revision = revision[0]
        if revision["request_id"] != item["request_id"]:
            raise ValueError("规格版本不属于该 Request")
        if "spec" in item and item["spec"] != revision["spec"]:
            raise ValueError("当前规格与版本快照不一致，请通过需求更新操作修改")
        return Request.model_validate({**item, "spec": revision["spec"]}).model_dump(
            exclude_none=True
        )

    def exists(self, table, key):
        if table not in TABLES:
            return False
        with self.db() as db:
            return (
                db.execute(f"SELECT 1 FROM {table} WHERE id=%s", (key,)).fetchone()
                is not None
            )

    def _put(self, db, table, item):
        key, parent = TABLES[table]
        if table == "requests":
            self.request_view(db, item)
            item = {name: value for name, value in item.items() if name != "spec"}
        encoded = Jsonb(item)
        if table in IMMUTABLE:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"enterprise-record:{self.persistence.schema}:{table}:{item[key]}",),
            )
            old = db.execute(
                f"SELECT body FROM {table} WHERE id=%s", (item[key],)
            ).fetchone()
            if old:
                previous = (
                    RECORD_MODELS[table]
                    .model_validate(old[0])
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
        if table == "artifacts":
            columns.insert(2, "request_id")
            values.insert(2, item.get("request_id"))
        db.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('%s' for _ in values)}) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            values,
        )

    def save_many(
        self,
        records,
        expected_revisions=None,
        event=None,
        events=(),
        expected_plan_versions=None,
    ):
        validated = [
            (
                table,
                (
                    Request
                    if table == "requests" and "spec" in item
                    else RECORD_MODELS[table]
                )
                .model_validate(item)
                .model_dump(exclude_none=True),
            )
            for table, item in records
        ]
        with self.db() as db:
            for request_id, revision_id in (expected_revisions or {}).items():
                row = db.execute(
                    "SELECT body FROM requests WHERE id=%s FOR UPDATE", (request_id,)
                ).fetchone()
                actual = row[0]["current_revision_id"] if row else None
                if actual != revision_id:
                    raise ValueError("需求已被其他执行更新，请重新读取当前版本")
            for request_id, plan_version in (expected_plan_versions or {}).items():
                row = db.execute(
                    "SELECT body FROM requests WHERE id=%s FOR UPDATE", (request_id,)
                ).fetchone()
                actual = row[0]["plan_version"] if row else None
                if actual != plan_version:
                    raise ValueError("执行计划已被其他操作更新，请重新读取当前计划")
            for table, item in validated:
                self._put(db, table, item)
            for item in [*events, *([event] if event else [])]:
                self._append_event(db, **item)
            return [
                self.request_view(db, item) if table == "requests" else item
                for table, item in validated
            ]

    def put(self, table, record):
        return self.save_many([(table, record)])[0]

    def list(self, table, parent, value):
        if table not in TABLES or (
            parent != TABLES[table][1]
            and (table, parent) != ("artifacts", "request_id")
        ):
            raise ValueError("无效存储检索")
        with self.db() as db:
            rows = db.execute(
                f"SELECT body FROM {table} WHERE {parent}=%s ORDER BY row_id",
                (str(value),),
            ).fetchall()
            return [
                self.request_view(db, row[0]) if table == "requests" else row[0]
                for row in rows
            ]

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
        session = db.execute(
            "SELECT body FROM sessions WHERE id=%s FOR UPDATE", (session_id,)
        ).fetchone()[0]
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

    def workflow_event(self, session_id, workflow_key):
        with self.db() as db:
            return self._workflow_event(db, session_id, workflow_key)

    @staticmethod
    def _workflow_event(db, session_id, workflow_key):
        row = db.execute(
            "SELECT body FROM session_events WHERE session_id=%s AND body->'payload'->>'workflow_key'=%s ORDER BY seq DESC LIMIT 1",
            (session_id, workflow_key),
        ).fetchone()
        return row[0] if row else None

    def event(
        self,
        session_id,
        turn_id,
        event_type,
        actor,
        payload,
        request_id=None,
        task_id=None,
        workflow_key=None,
    ):
        with self.db() as db:
            db.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE", (session_id,))
            if workflow_key:
                cached = self._workflow_event(db, session_id, workflow_key)
                if cached:
                    return cached
                payload = {**payload, "workflow_key": workflow_key}
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

    def recover_interrupted(self, resumable_request_ids=()):
        """Preserve checkpoint-backed work and recover legacy interrupted execution."""
        with self.db() as db:
            affected = set()
            for row in db.execute("SELECT body FROM tasks").fetchall():
                task = row[0]
                if (
                    task["status"] == "running"
                    and task["request_id"] not in resumable_request_ids
                ):
                    task.update(
                        status="failed",
                        summary="上次服务退出时执行中断，可以重新规划",
                        completed_at=now(),
                    )
                    self._put(db, "tasks", task)
                    affected.add(task["request_id"])
            for row in db.execute("SELECT body FROM requests").fetchall():
                request = row[0]
                if request["status"] == "running" or request["request_id"] in affected:
                    request.update(status="pending", updated_at=now())
                    self._put(db, "requests", request)

    def legacy_request_ids(self, task_id):
        with self.db() as db:
            row = db.execute(
                "SELECT body FROM legacy_tasks WHERE id=%s", (task_id,)
            ).fetchone()
            if row is None:
                return []
            task = row[0]
            row = db.execute(
                "SELECT body FROM legacy_task_revisions WHERE id=%s",
                (task["current_revision_id"],),
            ).fetchone()
        return [item["request_id"] for item in row[0]["requirements"]] if row else []
