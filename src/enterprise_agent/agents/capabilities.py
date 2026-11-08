"""Descriptions of the available Agent roles, shared with planning and tool discovery."""

CAPABILITIES = {
    "Supervisor": "意图识别、动态规划/重规划、需求验收、普通回答；不能执行业务 SQL",
    "SemanticAgent": "绑定已发布指标、表字段、粒度、时间与登记关系；返回轻量绑定，不创建伪数据产物",
    "QueryAgent": "依据语义绑定查询只读业务库，输出 query_result",
    "AnalysisAgent": "读取并精确计算已有 Artifact，输出 analysis_result；新底层数据需建议 Supervisor 规划",
    "PresentationAgent": "读取已有结果，输出 chart 或 Markdown report；保存真实映射、颜色与来源",
}
