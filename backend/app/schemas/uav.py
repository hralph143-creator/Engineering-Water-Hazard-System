from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UavCoverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_area_id: str = Field(min_length=1, max_length=64)


class UavOverlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uav_dataset_id: str = Field(min_length=1, max_length=64)


class UavVerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=64)
    uav_dataset_id: str = Field(min_length=1, max_length=64)
    risk_assessment_id: str = Field(min_length=1, max_length=64)
    work_area_id: str | None = Field(default=None, max_length=64)
    verification_status: str = "PENDING"
    observation_type: str = "OTHER"
    finding_text: str = Field(default="", max_length=4000)
    recommendation_text: str = Field(default="", max_length=4000)
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_choices(self):
        if self.verification_status not in {"PENDING", "REVIEWED", "INSUFFICIENT_IMAGE"}:
            raise ValueError("无效的人工复核状态")
        if self.observation_type not in {"VISIBLE_WATER", "POSSIBLE_WATER", "MUDDY_OR_WET_SURFACE", "NO_OBVIOUS_WATER", "IMAGE_UNCLEAR", "OTHER"}:
            raise ValueError("无效的人工观察类型")
        return self


class UavVerificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_status: str | None = None
    observation_type: str | None = None
    finding_text: str | None = Field(default=None, max_length=4000)
    recommendation_text: str | None = Field(default=None, max_length=4000)
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_choices(self):
        if self.verification_status is not None and self.verification_status not in {"PENDING", "REVIEWED", "INSUFFICIENT_IMAGE"}:
            raise ValueError("无效的人工复核状态")
        if self.observation_type is not None and self.observation_type not in {"VISIBLE_WATER", "POSSIBLE_WATER", "MUDDY_OR_WET_SURFACE", "NO_OBVIOUS_WATER", "IMAGE_UNCLEAR", "OTHER"}:
            raise ValueError("无效的人工观察类型")
        return self
