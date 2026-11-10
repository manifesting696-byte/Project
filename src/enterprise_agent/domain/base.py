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
