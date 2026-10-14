from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EquipmentType = Literal["excavator", "loader", "dump_truck", "mining_truck", "bulldozer", "roller", "crane", "other"]
BusinessStatus = Literal["idle", "working", "paused", "maintenance", "offline"]


class EquipmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=64)
    equipment_code: str = Field(min_length=1, max_length=128)
    equipment_name: str = Field(min_length=1, max_length=255)
    equipment_type: EquipmentType
    manufacturer: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=255)
    business_status: BusinessStatus = "idle"
    description: str = Field(default="", max_length=4000)


class EquipmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_code: str | None = Field(default=None, min_length=1, max_length=128)
    equipment_name: str | None = Field(default=None, min_length=1, max_length=255)
    equipment_type: EquipmentType | None = None
    manufacturer: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    business_status: BusinessStatus | None = None
    lifecycle_status: Literal["active", "archived"] | None = None
    description: str | None = Field(default=None, max_length=4000)


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float
    crs: str = Field(min_length=1, max_length=128)
    recorded_at: datetime
    accuracy_note: str = Field(default="", max_length=512)
    source_note: str = Field(default="", max_length=4000)


class EquipmentRiskRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_assessment_id: str = Field(min_length=1, max_length=64)

