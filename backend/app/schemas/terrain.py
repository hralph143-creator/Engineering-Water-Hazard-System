from pydantic import BaseModel, ConfigDict, Field


class TerrainRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dem_id: str = Field(min_length=1, max_length=64)
    position_id: str | None = Field(default=None, max_length=64)

