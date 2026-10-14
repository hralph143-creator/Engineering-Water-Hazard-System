from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..db.database import engine
from ..schemas.common import ok
from ..services.data_export_service import build_export_catalog, build_simulated_xlsx, build_xlsx_export


router = APIRouter(prefix="/data-exports", tags=["data-exports"])


class DataExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_names: list[str] = Field(min_length=1, max_length=26)
    include_data: bool = True
    include_field_dictionary: bool = True


class SimulatedDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_names: list[str] = Field(min_length=1, max_length=26)
    rows_per_table: int = Field(default=20, ge=1, le=1000)
    seed: int = Field(default=20261014, ge=0, le=2_147_483_647)


@router.get("/catalog")
def export_catalog():
    return ok(build_export_catalog(engine))


@router.post("/xlsx")
def export_xlsx(payload: DataExportRequest):
    try:
        content = build_xlsx_export(
            engine,
            payload.table_names,
            include_data=payload.include_data,
            include_field_dictionary=payload.include_field_dictionary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"工程水害数据导出_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return StreamingResponse(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/simulated-xlsx")
def simulated_xlsx(payload: SimulatedDataRequest):
    try:
        content = build_simulated_xlsx(engine, payload.table_names, payload.rows_per_table, payload.seed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"工程水害模拟数据_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return StreamingResponse(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
