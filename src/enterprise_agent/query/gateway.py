"""Only QueryAgent receives this gateway. Connections are always read only."""
import os
import time
from datetime import date
from decimal import Decimal
from ..profile.data import connection
from .validation import validate_readonly


class QueryGateway:
    def __init__(self, registry):
        self.registry = registry

    def validate(self, sql, binding):
        if binding.get("status") != "bound":
            raise ValueError("查询需要已确认的语义绑定")
        allowed = {name.lower() for name in binding.get("tables", [])}
        schema = {name: list(entry["columns"]) for name, entry in self.registry.tables.items() if name.lower() in allowed}
        if not schema:
            raise ValueError("语义绑定没有授权的查询表")
        return validate_readonly(sql, schema, self.registry.backend, binding.get("relations", []))

    def execute(self, sql, binding):
        statement = self.validate(sql, binding)
        limit, timeout = 100000, 15000
        if os.getenv("DATABASE_URL"):
            import psycopg
            from psycopg.rows import dict_row
            with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
                    options="-c default_transaction_read_only=on -c search_path=analysis") as db:
                db.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout),))
                plan = db.execute("EXPLAIN (FORMAT JSON) " + statement).fetchone()["QUERY PLAN"][0]["Plan"]
                pending = [plan]
                while pending:
                    node = pending.pop()
                    if node.get("Total Cost", 0) > 100000 or node.get("Plan Rows", 0) > 1000000:
                        raise ValueError("查询超过资源预算，请缩小范围或聚合")
                    pending.extend(node.get("Plans", []))
                cursor = db.execute(statement)
                rows = cursor.fetchmany(limit + 1)
                columns = [col.name for col in cursor.description]
        else:
            with connection() as db:
                deadline = time.monotonic() + timeout / 1000
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                cursor = db.execute(statement)
                columns = [col[0] for col in cursor.description]
                rows = [dict(row) for row in cursor.fetchmany(limit + 1)]
        if len(rows) > limit:
            raise ValueError("结果超过行数预算，请改用聚合查询")
        rows = [{key: float(value) if isinstance(value, Decimal) else value.isoformat() if isinstance(value, date) else value
                 for key, value in row.items()} for row in rows]
        return statement, rows, columns
