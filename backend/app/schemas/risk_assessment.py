from pydantic import BaseModel, ConfigDict, Field


class RiskAssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_change_analysis_id: str = Field(min_length=1, max_length=64)
    assessment_name: str | None = Field(default=None, max_length=255)
