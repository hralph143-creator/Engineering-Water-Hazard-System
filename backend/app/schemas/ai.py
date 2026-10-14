from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AIChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


class AITaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    mode: Literal["analysis", "chat"] = "analysis"
    question: str = Field(default="请生成本次识别结果的专业分析。", min_length=1, max_length=2000)
    messages: list[AIChatMessage] = Field(default_factory=list, max_length=12)
    existing_analysis: str | None = Field(default=None, max_length=12000)


class AICompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_ids: list[str] = Field(min_length=2, max_length=2)
    question: str = Field(default="请解读这两次识别结果的变化。", min_length=1, max_length=2000)

    @field_validator("result_ids")
    @classmethod
    def different_results(cls, value: list[str]) -> list[str]:
        if value[0] == value[1]:
            raise ValueError("请选择两个不同的识别结果")
        if any(not item.strip() or len(item) > 64 for item in value):
            raise ValueError("识别结果 ID 无效")
        return value


class AIChangeAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1, max_length=64)
    question: str = Field(
        default="请解读本次时空变化分析中可确认的变化、限制和复核重点。",
        min_length=1,
        max_length=2000,
    )


class AIRiskAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(min_length=1, max_length=64)
    question: str = Field(
        default="请解释本次基础水害关注等级、研判依据与现场复核重点。",
        min_length=1, max_length=2000,
    )


class AIAssistanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistance_record_id: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)
    question: str = Field(default="请解释当前作业辅助事项及其多源证据。", min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_scope(self):
        if not self.project_id and not self.assistance_record_id:
            raise ValueError("必须指定事项或施工项目")
        return self
