from enterprise_agent.model.tool_call import STRING, tool


def query_tools(self, context):
    bindings = [
        item["semantic_binding"]
        for item in context["inputs"]
        if "semantic_binding" in item
    ]
    if not bindings or any(item["status"] != "bound" for item in bindings):
        raise ValueError("QueryAgent 必须获得明确的已确认语义输入")
    binding = {
        "status": "bound",
        "metrics": [],
        "tables": [],
        "fields": [],
        "relations": [],
    }
    for item in bindings:
        for key in ("metrics", "tables", "fields", "relations"):
            for value in item[key]:
                if value not in binding[key]:
                    binding[key].append(value)

    def bound_semantics():
        names = {value.lower() for value in binding["tables"]}
        return {
            "binding": bindings,
            "metadata": {
                name: entry
                for name, entry in self.registry.tables.items()
                if name.lower() in names
            },
            "metrics": {key: self.registry.metric(key) for key in binding["metrics"]},
            "backend": self.registry.backend,
        }

    def validate(sql):
        return {"sql": self.gateway.validate(sql, binding)}

    def execute(sql, title, summary, entity_id_column=None):
        statement, rows, columns = self.gateway.execute(sql, binding)
        payload = {
            "columns": columns,
            "semantic_binding": bindings,
            "snapshot_id": self.registry.snapshot_id,
            "source_id": self.registry.source_id,
            "semantic_version": self.registry.catalog["version"],
            "catalog_version": self.registry.catalog_version,
            "provenance": {
                "method": "business_sql",
                "snapshot_id": self.registry.snapshot_id,
                "source_id": self.registry.source_id,
                "semantic_version": self.registry.catalog["version"],
                "catalog_version": self.registry.catalog_version,
            },
        }
        if entity_id_column:
            if entity_id_column not in columns:
                raise ValueError("实体 ID 列不存在")
            payload["entity_id_column"] = entity_id_column
        result = self.save(
            "table",
            title,
            summary,
            rows,
            sorted(self.allowed),
            payload,
            statement,
        )
        result["data"] = self.artifacts.read(result["artifact_id"], {"limit": 20})
        return result

    return {
        "get_bound_semantics": tool("读取已绑定的指标定义与查询范围", bound_semantics),
        "validate_sql": tool(
            "按字段、授权表、只读和登记关系校验 SQL", validate, sql=STRING
        ),
        "execute_readonly_sql": tool(
            "执行已绑定语义下的只读 SQL 并持久化 table Artifact；大结果只返回概览与切片",
            execute,
            sql=STRING,
            title=STRING,
            summary=STRING,
            entity_id_column=STRING,
        ),
    }
