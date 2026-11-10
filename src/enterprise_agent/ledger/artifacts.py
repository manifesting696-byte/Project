"""Managed content, exact selective reads, and isolated computation over existing results."""

import hashlib
import json
import re
from copy import deepcopy

from enterprise_agent.domain.artifact import Artifact
from enterprise_agent.ledger.ledger import now
from enterprise_agent.settings import READ_ROWS


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
        payload = deepcopy(payload or {})
        if task.get("workflow_key"):
            payload.setdefault("provenance", {})["workflow_key"] = task["workflow_key"]
        fingerprint = None
        if task.get("task_id") or task.get("workflow_key"):
            encoded = json.dumps(
                {
                    "content_type": content_type,
                    "content": content,
                    "column_order": list(content[0])
                    if content_type == "table" and content
                    else payload.get("columns"),
                    "sources": sources,
                    "payload": payload,
                    "sql": sql,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            for item in self.stores.list("artifacts", "session_id", session_id):
                same_origin = (
                    item.get("task_id") == task["task_id"]
                    if task.get("task_id")
                    else item["payload"].get("provenance", {}).get("workflow_key")
                    == task["workflow_key"]
                )
                if (
                    same_origin
                    and item["payload"].get("write_fingerprint") == fingerprint
                ):
                    if self.content(item["artifact_id"]) == json.loads(
                        json.dumps(content, ensure_ascii=False, default=str)
                    ):
                        return {
                            "artifact_id": item["artifact_id"],
                            "summary": item["summary"],
                            "refs": [{"kind": "artifact", "id": item["artifact_id"]}],
                        }
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
        if fingerprint:
            payload["write_fingerprint"] = fingerprint
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
        """Compute in transaction-local PostgreSQL tables containing authorized artifacts."""
        from decimal import Decimal

        from psycopg import sql as statements
        from psycopg.rows import dict_row

        from enterprise_agent.query.validate import validate_readonly

        schema = {}
        with self.stores.persistence.connection() as db:
            db.row_factory = dict_row
            db.execute("SET LOCAL statement_timeout='10s'")
            db.execute("SET LOCAL search_path=pg_temp,pg_catalog")
            for artifact_id in artifact_ids:
                item = self.inspect(artifact_id)
                if item["content_type"] != "table" or not re.fullmatch(
                    r"result_[A-Za-z0-9_-]+", artifact_id
                ):
                    raise ValueError("计算仅支持表格产物")
                rows = self.content(artifact_id)
                cols = [col["name"] for col in item["payload"]["columns"]]
                if not cols:
                    raise ValueError("空结果没有可计算字段")
                schema[artifact_id] = cols
                definitions = []
                for column in item["payload"]["columns"]:
                    dtype = column["dtype"]
                    kind = {
                        "int": "BIGINT",
                        "float": "DOUBLE PRECISION",
                        "bool": "BOOLEAN",
                        "null": "NUMERIC",
                    }.get(dtype, "TEXT")
                    definitions.append(
                        statements.SQL("{} {}").format(
                            statements.Identifier(column["name"]), statements.SQL(kind)
                        )
                    )
                db.execute(
                    statements.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
                        statements.Identifier(artifact_id),
                        statements.SQL(",").join(definitions),
                    )
                )
                if rows:
                    with db.cursor().copy(
                        statements.SQL("COPY {} ({}) FROM STDIN").format(
                            statements.Identifier(artifact_id),
                            statements.SQL(",").join(map(statements.Identifier, cols)),
                        )
                    ) as copy:
                        for row in rows:
                            copy.write_row(tuple(row.get(column) for column in cols))
            statement = validate_readonly(sql, schema, "postgres")
            cursor = db.execute(statement)
            values = cursor.fetchmany(100001)
            if len(values) > 100000:
                raise ValueError("计算结果过大，请聚合")
            values = [
                {
                    key: float(value) if isinstance(value, Decimal) else value
                    for key, value in row.items()
                }
                for row in values
            ]
            return (
                (values, [column[0] for column in cursor.description])
                if with_columns
                else values
            )

    def sql(self, artifact_id):
        item = self.inspect(artifact_id)
        ref = item["payload"].get("sql_ref")
        if ref:
            return (self.root / artifact_id / ref.rsplit("/", 1)[-1]).read_text(
                encoding="utf-8"
            )
        return item["payload"].get("computation_sql")
