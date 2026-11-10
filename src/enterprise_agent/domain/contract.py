"""Base pydantic model and the closed value sets shared by every contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Category = Literal[
    "general_qa", "business_knowledge", "data_analysis", "result_interaction"
]
ContentType = Literal["text", "table", "chart", "report"]

AgentName = str

CATEGORY_DESCRIPTIONS = {
    "general_qa": "系统能力介绍、基本统计概念",
    "business_knowledge": "企业数据表、字段、表关系、指标含义和业务术语",
    "data_analysis": "明细查询、统计、排名、趋势、对比、关联、诊断分析",
    "result_interaction": "图表生成、报告生成、指标解释、图表解释、结果解读",
}
