from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProjectType = Literal["open_pit_mine", "earthwork", "infrastructure", "road_construction", "other"]
AreaType = Literal["pit", "construction_road", "low_lying_area", "work_face", "material_area", "drainage_area", "other"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str = Field(min_length=1, max_length=255)
    project_code: str = Field(min_length=1, max_length=128)
    project_type: ProjectType
    description: str = Field(default="", max_length=4000)
    location_text: str = Field(default="", max_length=255)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str | None = Field(default=None, min_length=1, max_length=255)
    project_code: str | None = Field(default=None, min_length=1, max_length=128)
    project_type: ProjectType | None = None
    description: str | None = Field(default=None, max_length=4000)
    location_text: str | None = Field(default=None, max_length=255)
    status: Literal["active", "archived"] | None = None


class WorkAreaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=64)
    area_name: str = Field(min_length=1, max_length=255)
    area_code: str = Field(min_length=1, max_length=128)
    area_type: AreaType
    description: str = Field(default="", max_length=4000)
    geometry_geojson: dict[str, Any]
    crs: str = Field(min_length=1, max_length=128)


class WorkAreaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_name: str | None = Field(default=None, min_length=1, max_length=255)
    area_code: str | None = Field(default=None, min_length=1, max_length=128)
    area_type: AreaType | None = None
    description: str | None = Field(default=None, max_length=4000)
    geometry_geojson: dict[str, Any] | None = None
    crs: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["active", "archived"] | None = None


class ImpactRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=64)
