"""Managed content, exact selective reads, and isolated computation over existing results."""
import json
import re
import sqlite3
from pathlib import Path
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
        return json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else path.read_text(encoding="utf-8")

    def save(self, subtask, kind, title, summary, content, source_refs=None, payload=None, sql=None):
        artifact_id = self.stores.new_id("result")
        filename = {"query_result": "data.json", "analysis_result": "analysis.md", "chart": "chart.json", "report": "report.md"}[kind]
        directory = self.root / artifact_id
        directory.mkdir()
        body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        (directory / filename).write_text(body, encoding="utf-8")
        payload = dict(payload or {})
        if kind == "query_result":
            columns = list(content[0]) if content else payload.pop("columns", [])
            payload.update({"columns": [{"name": col, "dtype": next((type(row[col]).__name__ for row in content if row.get(col) is not None), "null")} for col in columns],
                            "row_count": len(content)})
        if sql:
            (directory / "query.sql").write_text(sql, encoding="utf-8")
            payload["sql_ref"] = f"storage://artifacts/{artifact_id}/query.sql"
        refs = source_refs or []
        for ref in refs:
            if ref["kind"] == "artifact":
                self.inspect(ref["id"])
        item = Artifact(artifact_id=artifact_id, type=kind, task_id=subtask["task_id"],
            revision_id=subtask["revision_id"], subtask_id=subtask["subtask_id"],
            request_ids=subtask["request_ids"], producer=subtask["assigned_agent"], title=title,
            summary=summary, source_refs=refs, content_ref=f"storage://artifacts/{artifact_id}/{filename}",
            payload=payload, created_at=now()).model_dump(exclude_none=True)
        self.stores.put("artifacts", item)
        return {"artifact_id": artifact_id, "summary": summary, "refs": [{"kind": "artifact", "id": artifact_id}]}

    def read(self, artifact_id, selector=None):
        selector = selector or {}
        item = self.inspect(artifact_id)
        if selector.get("part") == "sql":
            return {"sql": self.sql(artifact_id), "semantic_binding": item["payload"].get("semantic_binding")}
        content = self.content(artifact_id)
        if item["type"] != "query_result":
            if isinstance(content, str):
                offset = max(0, int(selector.get("offset", 0)))
                size = min(12000, max(1, int(selector.get("limit", 8000))))
                return {"content": content[offset:offset+size], "total_characters": len(content), "offset": offset}
            return {"content": content}
        rows = content
        for column, value in selector.get("filters", {}).items():
            rows = [row for row in rows if row.get(column) in value] if isinstance(value, list) else [row for row in rows if row.get(column) == value]
        if selector.get("order_by"):
            key = selector["order_by"]
            rows = sorted(rows, key=lambda row: (row.get(key) is None, row.get(key)), reverse=bool(selector.get("descending")))
        offset = max(0, int(selector.get("offset", 0)))
        limit = min(READ_ROWS, max(1, int(selector.get("limit", READ_ROWS))))
        selected = rows[offset:offset+limit]
        if selector.get("columns"):
            selected = [{key: row.get(key) for key in selector["columns"]} for row in selected]
        return {"rows": selected, "total_rows": len(rows), "offset": offset, "returned_rows": len(selected),
                "has_more": offset + len(selected) < len(rows)}

    def compute(self, artifact_ids, sql):
        """SQL runs only over in-memory copies of authorized Artifacts, never the business DB."""
        from ..query.validation import validate_readonly
        schema = {}
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            for artifact_id in artifact_ids:
                item = self.inspect(artifact_id)
                if item["type"] != "query_result" or not re.fullmatch(r"result_[A-Za-z0-9_-]+", artifact_id):
                    raise ValueError("计算仅支持查询产物")
                rows = self.content(artifact_id)
                cols = [col["name"] for col in item["payload"]["columns"]]
                schema[artifact_id] = cols
                quote = lambda value: '"' + value.replace('"', '""') + '"'
                if not cols:
                    raise ValueError("空结果没有可计算字段")
                db.execute(f"CREATE TABLE {quote(artifact_id)} ({','.join(quote(col) for col in cols)})")
                db.executemany(f"INSERT INTO {quote(artifact_id)} VALUES ({','.join('?' for _ in cols)})",
                               [tuple(row.get(col) for col in cols) for row in rows])
            statement = validate_readonly(sql, schema, "sqlite")
            db.execute("PRAGMA query_only=ON")
            import time
            deadline = time.monotonic() + 10
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            cursor = db.execute(statement)
            rows = [dict(row) for row in cursor.fetchmany(100001)]
            if len(rows) > 100000:
                raise ValueError("计算结果过大，请聚合")
            return rows
        finally:
            db.close()

    def sql(self, artifact_id):
        item = self.inspect(artifact_id)
        return (self.root / artifact_id / "query.sql").read_text(encoding="utf-8") if item["payload"].get("sql_ref") else None
