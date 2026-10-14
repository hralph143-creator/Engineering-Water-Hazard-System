from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import ChangeAnalysis
from ..schemas.change_analysis import ChangeAnalysisCreate
from ..schemas.common import ok
from ..services.change_analysis_service import (
    ChangeAnalysisError,
    create_change_analysis,
    delete_change_analysis,
    serialize_change_analysis,
)
from ..services.change_report_service import generate_change_report_html, generate_change_report_pdf
from ..services.risk_service import change_is_referenced

router = APIRouter(prefix="/change-analyses", tags=["change-analyses"])


def _find(analysis_id: str, db: Session) -> ChangeAnalysis:
    row = db.get(ChangeAnalysis, analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="变化分析记录不存在")
    return row


def _completed(analysis_id: str, db: Session) -> ChangeAnalysis:
    row = _find(analysis_id, db)
    if row.status != "completed":
        raise HTTPException(status_code=409, detail="变化分析尚未成功完成")
    return row


def _file(row: ChangeAnalysis, raw_path: str | None, label: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return path


@router.post("")
def create(payload: ChangeAnalysisCreate, db: Session = Depends(get_db)):
    try:
        row = create_change_analysis(
            db,
            result_t1_id=payload.result_t1_id,
            result_t2_id=payload.result_t2_id,
            name=payload.name,
            project_id=payload.project_id,
        )
    except ChangeAnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(serialize_change_analysis(row), "时空变化分析已完成")


@router.get("")
def list_all(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    statement = select(ChangeAnalysis)
    if status:
        statement = statement.where(ChangeAnalysis.status == status)
    rows = db.scalars(statement.order_by(ChangeAnalysis.created_at.desc()).limit(limit)).all()
    return ok([serialize_change_analysis(row) for row in rows])


@router.get("/{analysis_id}")
def detail(analysis_id: str, db: Session = Depends(get_db)):
    return ok(serialize_change_analysis(_find(analysis_id, db)))


@router.delete("/{analysis_id}")
def remove(analysis_id: str, db: Session = Depends(get_db)):
    if change_is_referenced(db, analysis_id):
        raise HTTPException(status_code=409, detail="该时空变化分析已被风险研判记录引用，请先删除相关风险研判记录。")
    try:
        delete_change_analysis(db, _find(analysis_id, db))
    except ChangeAnalysisError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ok(message="变化分析记录及成果文件已删除")


@router.get("/{analysis_id}/image")
def image(analysis_id: str, db: Session = Depends(get_db)):
    row = _completed(analysis_id, db)
    path = _file(row, row.change_png_path, "变化分析图")
    return FileResponse(path, media_type="image/png", filename=f"change_{analysis_id[:8]}.png")


@router.get("/{analysis_id}/change.tif")
def geotiff(analysis_id: str, db: Session = Depends(get_db)):
    row = _completed(analysis_id, db)
    path = _file(row, row.change_geotiff_path, "变化分析 GeoTIFF")
    return FileResponse(path, media_type="image/tiff", filename=f"change_{analysis_id[:8]}.tif")


@router.get("/{analysis_id}/geotiff")
def geotiff_alias(analysis_id: str, db: Session = Depends(get_db)):
    """正式语义化下载地址；保留 change.tif 地址兼容已有前端。"""
    return geotiff(analysis_id, db)


@router.get("/{analysis_id}/export.csv")
def export_csv(analysis_id: str, db: Session = Depends(get_db)):
    row = _completed(analysis_id, db)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([
        "分析ID", "分析名称", "T1任务", "T2任务", "T1数据", "T2数据",
        "T1积水占比", "T2积水占比", "新增积水像素", "新增积水占比",
        "持续积水像素", "持续积水占比", "消退积水像素", "消退积水占比",
        "持续非积水像素", "持续非积水占比", "有效像素", "净变化",
        "分析时间", "算法版本",
    ])
    writer.writerow([
        row.id, row.name, row.task_t1_id, row.task_t2_id,
        row.dataset_t1_name, row.dataset_t2_name, row.t1_water_ratio, row.t2_water_ratio,
        row.new_water_pixels, row.new_water_ratio, row.persistent_water_pixels,
        row.persistent_water_ratio, row.receded_water_pixels, row.receded_water_ratio,
        row.stable_non_water_pixels, row.stable_non_water_ratio, row.valid_pixels,
        row.net_change_ratio, row.completed_at, row.algorithm_version,
    ])
    return StreamingResponse(
        iter([stream.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="change_{analysis_id[:8]}.csv"'},
    )


@router.get("/{analysis_id}/report.html", response_class=HTMLResponse)
def report_html(analysis_id: str, db: Session = Depends(get_db)):
    return HTMLResponse(generate_change_report_html(_completed(analysis_id, db)))


@router.get("/{analysis_id}/report.pdf")
def report_pdf(analysis_id: str, db: Session = Depends(get_db)):
    pdf = generate_change_report_pdf(_completed(analysis_id, db))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="change_{analysis_id[:8]}.pdf"'},
    )
