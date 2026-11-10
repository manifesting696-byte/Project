"""A small v2 ledger for migration tests, including original artifact files."""

import json
import sqlite3
from contextlib import closing


def legacy_ledger(directory):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = "2026-11-01T00:00:00+00:00"
    with closing(sqlite3.connect(directory / "agent.sqlite")) as db, db:
        db.executescript("""
            CREATE TABLE sequences(kind TEXT PRIMARY KEY,value INTEGER NOT NULL);
            CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE session_events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL);
            CREATE TABLE tasks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE task_revisions(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE subtasks(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE artifacts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE session_summaries(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL);
        """)
        for kind, value in {
            "req": 2,
            "task": 1,
            "subtask": 2,
            "rev": 1,
            "result": 2,
            "session": 1,
            "turn": 1,
            "event": 2,
        }.items():
            db.execute("INSERT INTO sequences VALUES(?,?)", (kind, value))

        def put(table, key, parent, value, seq=None):
            columns = [value[key], value[parent]]
            if seq is not None:
                columns.append(seq)
            columns.append(json.dumps(value, ensure_ascii=False))
            db.execute(
                f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", columns
            )

        put(
            "sessions",
            "session_id",
            "user_id",
            {
                "session_id": "session_001",
                "user_id": "1",
                "title": "历史产品图",
                "last_active_task_id": "task_001",
                "last_response_event_id": "event_002",
                "last_seq": 2,
                "created_at": stamp,
                "updated_at": stamp,
            },
        )
        task = {
            "task_id": "task_001",
            "origin_session_id": "session_001",
            "session_id": "session_001",
            "created_by_turn_id": "turn_001",
            "goal": "历史产品图",
            "current_revision_id": "rev_001",
            "plan_version": 1,
            "status": "completed",
            "created_at": stamp,
            "updated_at": stamp,
        }
        task.pop("session_id")
        db.execute(
            "INSERT INTO tasks VALUES(?,?,?)",
            ("task_001", "session_001", json.dumps(task)),
        )
        requirements = [
            {
                "request_id": "req_001",
                "type": "data_analysis",
                "description": "读取产品",
                "params": {},
                "inputs": [],
            },
            {
                "request_id": "req_002",
                "type": "visualization",
                "description": "产品图",
                "params": {},
                "inputs": [],
            },
        ]
        put(
            "task_revisions",
            "revision_id",
            "task_id",
            {
                "revision_id": "rev_001",
                "task_id": "task_001",
                "parent_revision_id": None,
                "source_intent_event_id": "event_001",
                "constraints": {},
                "requirements": requirements,
                "created_at": stamp,
            },
        )
        base = {
            "task_id": "task_001",
            "revision_id": "rev_001",
            "created_by_turn_id": "turn_001",
            "status": "completed",
            "created_at": stamp,
            "params": {},
            "replan_after": False,
            "suggestions": [],
        }
        put(
            "subtasks",
            "subtask_id",
            "task_id",
            {
                **base,
                "subtask_id": "subtask_001",
                "request_ids": ["req_001"],
                "assigned_agent": "QueryAgent",
                "objective": "产品数据",
                "depends_on": [],
                "input_bindings": [],
                "expected_outputs": ["dataset"],
                "produced_outputs": {
                    "dataset": {"kind": "artifact", "id": "result_001"}
                },
            },
        )
        put(
            "subtasks",
            "subtask_id",
            "task_id",
            {
                **base,
                "subtask_id": "subtask_002",
                "request_ids": ["req_002"],
                "assigned_agent": "PresentationAgent",
                "objective": "产品图",
                "depends_on": ["subtask_001"],
                "input_bindings": [
                    {
                        "slot": "data",
                        "source": {
                            "kind": "subtask",
                            "id": "subtask_001",
                            "output": "dataset",
                        },
                    }
                ],
                "expected_outputs": ["chart"],
                "produced_outputs": {"chart": {"kind": "artifact", "id": "result_002"}},
            },
        )
        rows = [
            {"product_id": 1, "product_name": "Historical product", "record_count": 1}
        ]
        chart = {
            "title": {"text": "产品记录"},
            "dataset": {"source": rows},
            "xAxis": {"type": "category"},
            "yAxis": {"type": "value"},
            "series": [],
            "selection": {
                "source_id": "result_001",
                "selector": {},
                "total_rows": 1,
                "displayed_rows": 1,
            },
        }
        for index, kind, body, filename, refs in (
            (1, "query_result", rows, "data.json", []),
            (
                2,
                "chart",
                chart,
                "chart.json",
                [{"kind": "artifact", "id": "result_001"}],
            ),
        ):
            key = f"result_{index:03d}"
            path = directory / "content" / key
            path.mkdir(parents=True)
            (path / filename).write_text(json.dumps(body), encoding="utf-8")
            payload = {
                "row_count": 1,
                "columns": [{"name": name, "dtype": "str"} for name in rows[0]],
            }
            if index == 1:
                payload["sql_ref"] = f"storage://artifacts/{key}/query.sql"
                (path / "query.sql").write_text(
                    "SELECT product_id, product_name FROM Products LIMIT 1",
                    encoding="utf-8",
                )
            put(
                "artifacts",
                "artifact_id",
                "task_id",
                {
                    "artifact_id": key,
                    "type": kind,
                    "task_id": "task_001",
                    "revision_id": "rev_001",
                    "subtask_id": f"subtask_{index:03d}",
                    "request_ids": [f"req_{index:03d}"],
                    "producer": "QueryAgent" if index == 1 else "PresentationAgent",
                    "title": "历史产品",
                    "summary": "历史真实结果",
                    "source_refs": refs,
                    "content_ref": f"storage://artifacts/{key}/{filename}",
                    "payload": payload,
                    "created_at": stamp,
                },
            )
        for seq, kind, payload in (
            (1, "user_message", {"content": "历史产品图"}),
            (
                2,
                "assistant_response",
                {
                    "blocks": [
                        {
                            "block_id": "block_001",
                            "kind": "chart",
                            "refs": [{"kind": "artifact", "id": "result_002"}],
                        }
                    ]
                },
            ),
        ):
            put(
                "session_events",
                "event_id",
                "session_id",
                {
                    "event_id": f"event_{seq:03d}",
                    "session_id": "session_001",
                    "seq": seq,
                    "turn_id": "turn_001",
                    "event_type": kind,
                    "actor": "User" if seq == 1 else "Supervisor",
                    "payload": payload,
                    "task_id": "task_001",
                    "created_at": stamp,
                },
                seq,
            )
