from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RainfallStationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    station_code: str = Field(min_length=1, max_length=128)
    station_name: str = Field(min_length=1, max_length=255)
    station_type: str = "manual_station"
    x: float | None = None
    y: float | None = None
    crs: str | None = Field(default=None, max_length=128)
    location_description: str = Field(default="", max_length=255)
    source_name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def coordinate_group(self):
        supplied = (self.x is not None, self.y is not None, bool(self.crs and self.crs.strip()))
        if any(supplied) and not all(supplied):
            raise ValueError("雨量站坐标必须同时填写X、Y和CRS")
        return self


class RainfallStationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_name: str | None = Field(default=None, min_length=1, max_length=255)
    station_type: str | None = None
    x: float | None = None
    y: float | None = None
    crs: str | None = Field(default=None, max_length=128)
    clear_coordinates: bool = False
    location_description: str | None = Field(default=None, max_length=255)
    source_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class RainfallObservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: str
    project_id: str
    observed_at: datetime
    interval_minutes: int = Field(gt=0, le=10080)
    rainfall_mm: float = Field(ge=0)
    source_type: str = "manual"
    quality_status: str = "valid"
    source_note: str = Field(default="", max_length=2000)


class CaptureTimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_time: datetime | None
