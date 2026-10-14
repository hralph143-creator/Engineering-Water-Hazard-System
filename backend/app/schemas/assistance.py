from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


ASSISTANCE_TYPES = {"AREA_INSPECTION", "EQUIPMENT_ENV_CHECK", "PASSAGE_CONDITION_CHECK", "RESUME_WORK_CHECK", "EMERGENCY_COORDINATION", "OTHER"}
PRIORITIES = {"NORMAL", "IMPORTANT", "URGENT"}
STATUSES = {"PENDING", "IN_PROGRESS", "VERIFIED", "CLOSED", "CANCELLED"}
VERIFICATION_RESULTS = {"ISSUE_CONFIRMED", "NO_OBVIOUS_ISSUE", "NEEDS_FURTHER_CHECK", "CONDITION_IMPROVED", "UNABLE_TO_VERIFY"}


class GenerateAssistanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_resume_checks: bool = True


class AssistanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, max_length=64)
    risk_assessment_id: str | None = Field(default=None, max_length=64)
    work_area_id: str | None = Field(default=None, max_length=64)
    equipment_id: str | None = Field(default=None, max_length=64)
    assistance_type: str
    title: str = Field(min_length=1, max_length=255)
    priority_level: str = "NORMAL"
    source_summary: str = Field(default="", max_length=4000)
    recommendation_text: str = Field(default="", max_length=4000)
    inspection_checklist: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def choices(self):
        if self.assistance_type not in ASSISTANCE_TYPES: raise ValueError("无效的辅助事项类型")
        if self.priority_level not in PRIORITIES: raise ValueError("无效的事项优先级")
        return self


class AssistanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    priority_level: str | None = None
    recommendation_text: str | None = Field(default=None, max_length=4000)
    inspection_checklist: list[str] | None = Field(default=None, max_length=30)
    status: str | None = None

    @model_validator(mode="after")
    def choices(self):
        if self.priority_level is not None and self.priority_level not in PRIORITIES: raise ValueError("无效的事项优先级")
        if self.status is not None and self.status not in STATUSES: raise ValueError("无效的事项状态")
        return self


class AssistanceVerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_result: str
    finding_text: str = Field(default="", max_length=4000)
    action_note: str = Field(default="", max_length=4000)
    checklist: list[dict] = Field(default_factory=list, max_length=50)
    attachment_note: str = Field(default="", max_length=1000)
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def choices(self):
        if self.verification_result not in VERIFICATION_RESULTS: raise ValueError("无效的人工核查结果")
        return self


class AssistanceVerificationUpdate(AssistanceVerificationCreate):
    pass


class CloseAssistanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closed_by_note: str = Field(min_length=1, max_length=4000)
