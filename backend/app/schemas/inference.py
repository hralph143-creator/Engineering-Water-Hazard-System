from pydantic import BaseModel, Field


class InferenceRequest(BaseModel):
    dataset_id: str
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class BatchInferenceRequest(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=50)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
