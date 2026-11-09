"""Managed content, exact selective reads, and isolated computation over existing results."""

import json
import re
import sqlite3
from copy import deepcopy

from ..core.config import READ_ROWS
from ..core.models import Artifact
from .stores import now


class Artifacts:
    def __init__(self, stores):
        self.stores = stores
        self.root = stores.directory / "content"
        self.root.mkdir(exist_ok=True)

    def inspect(self, artifact_id):
        return self.stores.get("artifacts", artifact_id)

    def content(self, artifact_id):
        metadata = self.inspect(artifact_id)
        path = self.root / artifact_id / metadata["content_ref"].rsplit("/", 1)[-1]
        return (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".json"
            else path.read_text(encoding="utf-8")
        )

    def save(
        self,
        task,
        content_type,
        title,
        summary,
        content,
        source_artifact_ids=None,
        payload=None,
        sql=None,
    ):
        request = (
            self.stores.get("requests", task["request_id"])
            if task.get("request_id")
            else {}
        )
        session_id = task.get("session_id") or request["session_id"]
        self.stores.get("sessions", session_id)
        if request and request["session_id"] != session_id:
            raise PermissionError("生产需求不属于当前会话")
        if task.get("revision_id"):
            revision = self.stores.get("request_revisions", task["revision_id"])
            if revision["request_id"] != task.get("request_id"):
                raise ValueError("生产版本与需求不匹配")
        if content_type in ("text", "report") and not isinstance(content, str):
            raise ValueError("文字和报告必须使用正文字符串")
        if content_type == "chart" and not isinstance(content, dict):
            raise ValueError("图表必须使用配置对象")
        sources = list(dict.fromkeys(source_artifact_ids or []))
        for key in sources:
            if self.inspect(key)["session_id"] != session_id:
                raise PermissionError("来源产物不属于当前会话")
        artifact_id = self.stores.new_id("result")
        while (self.root / artifact_id).exists() or self.stores.exists(
            "artifacts", artifact_id
        ):
            artifact_id = self.stores.new_id("result")
        filename = {
            "table": "data.json",
            "text": "text.md",
            "chart": "chart.json",
            "report": "report.md",
        }[content_type]
        directory = self.root / artifact_id
        body = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, default=str)
        )
        payload = deepcopy(payload or {})
        payload.setdefault(
            "provenance",
            {"method": "analysis" if content_type == "text" else "presentation"},
        )
        if content_type == "table":
            if not isinstance(content, list) or any(
                not isinstance(row, dict) for row in content
            ):
                raise ValueError("表格必须使用行对象列表")
            columns = list(content[0]) if content else payload.pop("columns", [])
            if any(
                not isinstance(column, str) or not column for column in columns
            ) or len(columns) != len(set(columns)):
                raise ValueError("表格列名必须为唯一的非空字符串")
            if any(set(row) != set(columns) for row in content):
                raise ValueError("表格各行字段必须一致")
            payload.update(
                {
                    "columns": [
                        {
                            "name": col,
                            "dtype": next(
                                (
                                    type(row[col]).__name__
                                    for row in content
                                    if row.get(col) is not None
                                ),
                                "null",
                            ),
                        }
                        for col in columns
                    ],
                    "row_count": len(content),
                }
            )
        if sql:
            sql_file = (
                "compute.sql"
                if payload["provenance"]["method"] == "artifact_compute"
                else "query.sql"
            )
            payload["sql_ref"] = f"storage://artifacts/{artifact_id}/{sql_file}"
            payload["provenance"]["sql_ref"] = payload["sql_ref"]
        item = Artifact(
            artifact_id=artifact_id,
            content_type=content_type,
            session_id=session_id,
            created_by_turn_id=task["created_by_turn_id"],
            task_id=task.get("task_id"),
            revision_id=task.get("revision_id"),
            request_id=task.get("request_id"),
            producer=task.get("assigned_agent", task.get("producer", "Supervisor")),
            title=title,
            summary=summary,
            source_artifact_ids=sources,
            content_ref=f"storage://artifacts/{artifact_id}/{filename}",
            payload=payload,
            created_at=now(),
        ).model_dump(exclude_none=True)
        directory.mkdir()
        try:
            (directory / filename).write_text(body, encoding="utf-8")
            if sql:
                (directory / sql_file).write_text(sql, encoding="utf-8")
            self.stores.put("artifacts", item)
        except Exception:
            (directory / filename).unlink(missing_ok=True)
            if sql:
                (directory / sql_file).unlink(missing_ok=True)
            directory.rmdir()
            raise
        return {
            "artifact_id": artifact_id,
            "summary": summary,
            "refs": [{"kind": "artifact", "id": artifact_id}],
        }

    def read(self, artifact_id, selector=None):
        selector = selector or {}
        item = self.inspect(artifact_id)
        if selector.get("part") == "sql":
            return {
                "sql": self.sql(artifact_id),
                "semantic_binding": item["payload"].get("semantic_binding"),
            }
        content = self.content(artifact_id)
        if item["content_type"] != "table":
            if isinstance(content, str):
                offset = max(0, int(selector.get("offset", 0)))
                size = min(12000, max(1, int(selector.get("limit", 8000))))
                return {
                    "content": content[offset : offset + size],
                    "total_characters": len(content),
                    "offset": offset,
                }
            return {"content": content}
        rows = content
        for column, value in selector.get("filters", {}).items():
            rows = (
                [row for row in rows if row.get(column) in value]
                if isinstance(value, list)
                else [row for row in rows if row.get(column) == value]
            )
        if selector.get("order_by"):
            key = selector["order_by"]
            rows = sorted(
                rows,
                key=lambda row: (row.get(key) is None, row.get(key)),
                reverse=bool(selector.get("descending")),
            )
        offset = max(0, int(selector.get("offset", 0)))
        limit = min(READ_ROWS, max(1, int(selector.get("limit", READ_ROWS))))
        selected = rows[offset : offset + limit]
        if selector.get("columns"):
            selected = [
                {key: row.get(key) for key in selector["columns"]} for row in selected
            ]
        return {
            "rows": selected,
            "total_rows": len(rows),
            "offset": offset,
            "returned_rows": len(selected),
            "has_more": offset + len(selected) < len(rows),
        }

    def compute(self, artifact_ids, sql, with_columns=False):
        """SQL runs only over in-memory copies of authorized Artifacts, never the business DB."""
        from ..query.validation import validate_readonly

        schema = {}
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            for artifact_id in artifact_ids:
                item = self.inspect(artifact_id)
                if item["content_type"] != "table" or not re.fullmatch(
                    r"result_[A-Za-z0-9_-]+", artifact_id
                ):
                    raise ValueError("计算仅支持查询产物")
                rows = self.content(artifact_id)
                cols = [col["name"] for col in item["payload"]["columns"]]
                schema[artifact_id] = cols
                quote = lambda value: '"' + value.replace('"', '""') + '"'
                if not cols:
                    raise ValueError("空结果没有可计算字段")
                db.execute(
                    f"CREATE TABLE {quote(artifact_id)} ({','.join(quote(col) for col in cols)})"
                )
                db.executemany(
                    f"INSERT INTO {quote(artifact_id)} VALUES ({','.join('?' for _ in cols)})",
                    [tuple(row.get(col) for col in cols) for row in rows],
                )
            statement = validate_readonly(sql, schema, "sqlite")
            db.execute("PRAGMA query_only=ON")
            import time

            deadline = time.monotonic() + 10
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            cursor = db.execute(statement)
            rows = [dict(row) for row in cursor.fetchmany(100001)]
            if len(rows) > 100000:
                raise ValueError("计算结果过大，请聚合")
            if with_columns:
                return rows, [column[0] for column in cursor.description]
            return rows
        finally:
            db.close()

    def sql(self, artifact_id):
        item = self.inspect(artifact_id)
        ref = item["payload"].get("sql_ref")
        if ref:
            return (self.root / artifact_id / ref.rsplit("/", 1)[-1]).read_text(
                encoding="utf-8"
            )
        return item["payload"].get("computation_sql")
