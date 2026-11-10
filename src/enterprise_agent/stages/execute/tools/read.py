from enterprise_agent.model.tool_call import OBJECT, STRING, tool


def common_tools(self):
    return {
        "inspect_artifact": tool(
            "读取输入产物的概况与完整内容地址", self.inspect, artifact_id=STRING
        ),
        "read_artifact": tool(
            "精确读取输入产物正文或切片；selector 支持 columns/filters/order_by/descending/offset/limit，part=sql 可回读原查询 SQL 和口径",
            self.read,
            artifact_id=STRING,
            selector=OBJECT,
        ),
    }
