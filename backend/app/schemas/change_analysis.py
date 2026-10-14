from pydantic import BaseModel, ConfigDict, Field


class ChangeAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_t1_id: str = Field(min_length=1)
    result_t2_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=36)
