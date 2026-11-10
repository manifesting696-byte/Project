from enterprise_agent.model.tool_call import STRING, tool


def semantic_tools(registry):
    return {
        "search_semantic_registry": tool(
            "检索真实表字段、已发布指标与未批准指标状态", registry.search, query=STRING
        ),
        "get_metric_definition": tool(
            "读取已发布指标口径；未发布明确拒绝绑定", registry.metric, metric_id=STRING
        ),
        "get_relation_paths": tool(
            "读取登记的物理数据关系，不发明关联",
            lambda: {"relations": registry.relations},
        ),
    }
