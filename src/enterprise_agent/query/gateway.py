"""Only QueryAgent receives this gateway. Connections are always read only."""

from copy import deepcopy

from enterprise_agent.query.database import ReadonlyDatabase
from enterprise_agent.query.validate import validate_readonly


class QueryGateway:
    def __init__(self, registry, events=None, database=None):
        self.registry = registry
        self.events = events
        self.database = database or ReadonlyDatabase()

    def validate(self, sql, binding):
        if binding.get("status") != "bound":
            raise ValueError("查询需要已确认的语义绑定")
        allowed = {name.lower() for name in binding.get("tables", [])}
        if not allowed <= {name.lower() for name in self.registry.tables}:
            raise ValueError("语义绑定的数据表已移出问析目录，请重新确认查询范围")
        if any(
            relation not in self.registry.relations
            for relation in binding.get("relations", [])
        ):
            raise ValueError("语义绑定的表关系已变化，请重新确认查询范围")
        schema = {
            name: list(entry["columns"])
            for name, entry in self.registry.tables.items()
            if name.lower() in allowed
        }
        if not schema:
            raise ValueError("语义绑定没有授权的查询表")
        return validate_readonly(
            sql, schema, self.registry.backend, binding.get("relations", [])
        )

    def execute(self, sql, binding):
        statement = self.validate(sql, binding)
        if self.events is not None:
            self.events.emit(
                "sql/before_execute", {"sql": statement, "binding": deepcopy(binding)}
            )
        rows, columns = self.database.execute(statement)
        if self.events is not None:
            self.events.notify(
                "sql/after_execute",
                {"sql": statement, "row_count": len(rows), "columns": list(columns)},
            )
        return statement, rows, columns
