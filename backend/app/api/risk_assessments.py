import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentRiskLink, EquipmentTerrainSnapshot, RainfallStation, RiskAssessment, RiskRainfallContext, TerrainDataset, TerrainFusionAnalysis, UavDataset, UavRiskOverlay, UavVerificationRecord, WorkArea, WorkAreaImpact, WorkAreaTerrainProfile
from ..schemas.common import ok
from ..schemas.risk_assessment import RiskAssessmentCreate
from ..services.risk_report_service import risk_report_html, risk_report_pdf
from ..services.risk_service import (
    RiskAssessmentError, create_risk_assessment, delete_risk_assessment, serialize_risk,
)
from ..services.spatial_service import serialize_impact
from ..services.equipment_service import serialize_link
from ..services.terrain_service import serialize_fusion, serialize_profile, serialize_snapshot
from ..services.rainfall_service import serialize_risk_context
from ..services.uav_service import serialize_overlay, serialize_verification

router = APIRouter(prefix="/risk-assessments", tags=["risk-assessments"])


def _find(assessment_id: str, db: Session) -> RiskAssessment:
    row = db.get(RiskAssessment, assessment_id)
    if not row: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    return row


def _completed(assessment_id: str, db: Session) -> RiskAssessment:
    row = _find(assessment_id, db)
    if row.status != "completed": raise HTTPException(status_code=409, detail="风险研判尚未成功完成")
    return row


def _file(path: str | None, label: str) -> Path:
    target = Path(path) if path else None
    if not target or not target.is_file(): raise HTTPException(status_code=404, detail=f"{label}不存在")
    return target


@router.post("")
def create(payload: RiskAssessmentCreate, db: Session = Depends(get_db)):
    try:
        row = create_risk_assessment(db, payload.source_change_analysis_id, payload.assessment_name)
    except RiskAssessmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(serialize_risk(row), "基础水害辅助研判已完成")


@router.get("")
def list_all(status: str | None = None, project_id: str | None = None, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    statement = select(RiskAssessment)
    if status: statement = statement.where(RiskAssessment.status == status)
    if project_id: statement = statement.where(RiskAssessment.project_id == project_id)
    rows = db.scalars(statement.order_by(RiskAssessment.created_at.desc()).limit(limit)).all()
    return ok([serialize_risk(row) for row in rows])


@router.get("/{assessment_id}")
def detail(assessment_id: str, db: Session = Depends(get_db)):
    return ok(serialize_risk(_find(assessment_id, db)))


@router.delete("/{assessment_id}")
def remove(assessment_id: str, db: Session = Depends(get_db)):
    try: delete_risk_assessment(db, _find(assessment_id, db))
    except RiskAssessmentError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ok(message="风险研判记录及成果文件已删除")


@router.get("/{assessment_id}/image")
def image(assessment_id: str, db: Session = Depends(get_db)):
    row = _completed(assessment_id, db)
    return FileResponse(_file(row.risk_png_path, "水害关注区域图"), media_type="image/png", filename=f"risk_{assessment_id[:8]}.png")


@router.get("/{assessment_id}/geotiff")
def geotiff(assessment_id: str, db: Session = Depends(get_db)):
    row = _completed(assessment_id, db)
    return FileResponse(_file(row.risk_geotiff_path, "水害关注区域 GeoTIFF"), media_type="image/tiff", filename=f"risk_{assessment_id[:8]}.tif")


@router.get("/{assessment_id}/export.csv")
def export_csv(assessment_id: str, db: Session = Depends(get_db)):
    row = _completed(assessment_id, db); data = serialize_risk(row)
    stream = io.StringIO(); writer = csv.writer(stream)
    writer.writerow(["风险研判ID", "来源变化分析ID", "T1", "T2", "当前积水占比", "新增积水占比", "持续积水占比", "消退积水占比", "净变化", "基础水害关注等级", "风险原因", "辅助建议", "规则版本", "研判时间"])
    writer.writerow([row.id, row.source_change_analysis_id, row.source_t1_name, row.source_t2_name,
                     row.current_water_ratio, row.new_water_ratio, row.persistent_water_ratio,
                     row.receded_water_ratio, row.net_change_ratio, row.attention_level,
                     "；".join(data["risk_reason_text"]), "；".join(data["inspection_advice"]),
                     row.rule_version, row.completed_at])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="risk_{assessment_id[:8]}.csv"'})


@router.get("/{assessment_id}/report.html", response_class=HTMLResponse)
def report_html(assessment_id: str, db: Session = Depends(get_db)):
    row = _completed(assessment_id, db); impacts, project_name = _report_impacts(row, db)
    return HTMLResponse(risk_report_html(row, impacts, project_name, _report_equipment(row, db), _report_terrain(row, db), _report_rainfall(row, db), _report_uav(row, db)))


@router.get("/{assessment_id}/report.pdf")
def report_pdf(assessment_id: str, db: Session = Depends(get_db)):
    row = _completed(assessment_id, db); impacts, project_name = _report_impacts(row, db)
    return Response(risk_report_pdf(row, impacts, project_name, _report_equipment(row, db), _report_terrain(row, db), _report_rainfall(row, db), _report_uav(row, db)), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="risk_{assessment_id[:8]}.pdf"'})


def _report_impacts(row: RiskAssessment, db: Session) -> tuple[list[dict], str | None]:
    items = db.scalars(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == row.id).order_by(WorkAreaImpact.high_attention_ratio.desc())).all()
    areas = {x.id:x for x in db.scalars(select(WorkArea).where(WorkArea.id.in_([i.work_area_id for i in items]))).all()} if items else {}
    project = db.get(ConstructionProject, row.project_id) if row.project_id else None
    return [serialize_impact(item, areas.get(item.work_area_id)) for item in items], project.project_name if project else None


def _report_equipment(row: RiskAssessment, db: Session) -> list[dict]:
    links = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == row.id).order_by(EquipmentRiskLink.created_at)).all()
    output = []
    for link in links:
        equipment = db.get(ConstructionEquipment, link.equipment_id); position = db.get(EquipmentPosition, link.position_id)
        ids = __import__("json").loads(link.work_area_ids or "[]")
        found = {area.id:area for area in db.scalars(select(WorkArea).where(WorkArea.id.in_(ids))).all()} if ids else {}
        output.append(serialize_link(link, equipment, position, [found[x] for x in ids if x in found]))
    return output


def _report_terrain(row: RiskAssessment, db: Session) -> dict | None:
    fusion = db.scalar(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id == row.id).order_by(TerrainFusionAnalysis.created_at.desc()).limit(1))
    if not fusion: return None
    dem = db.get(TerrainDataset, fusion.dem_id)
    profiles = db.scalars(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.dem_id == fusion.dem_id,
                         WorkAreaTerrainProfile.project_id == row.project_id)).all() if row.project_id else []
    profile_data = []
    for profile in profiles:
        data = serialize_profile(profile, dem); area = db.get(WorkArea, profile.work_area_id); data["area_name"] = area.area_name if area else None; profile_data.append(data)
    links = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == row.id)).all()
    equipment_data = []
    for link in links:
        snapshot = db.scalar(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.position_id == link.position_id,
                             EquipmentTerrainSnapshot.dem_id == fusion.dem_id).limit(1))
        if snapshot:
            data = serialize_snapshot(snapshot, dem); equipment = db.get(ConstructionEquipment, snapshot.equipment_id); data["equipment_name"] = equipment.equipment_name if equipment else None; equipment_data.append(data)
    return {"dem_name":dem.name if dem else None,"source_name":dem.source_name if dem else None,
            "fusion":serialize_fusion(fusion,dem),"profiles":profile_data,"equipment":equipment_data}


def _report_rainfall(row: RiskAssessment, db: Session) -> dict | None:
    context=db.scalar(select(RiskRainfallContext).where(RiskRainfallContext.risk_assessment_id==row.id))
    if not context: return None
    data=serialize_risk_context(context)
    data["source_names"]=[x for x in db.scalars(select(RainfallStation.source_name).where(RainfallStation.project_id==context.project_id,RainfallStation.source_name!="")).all()]
    return data


def _report_uav(row: RiskAssessment, db: Session) -> dict | None:
    overlays = db.scalars(select(UavRiskOverlay).where(UavRiskOverlay.risk_assessment_id == row.id).order_by(UavRiskOverlay.created_at)).all()
    verifications = db.scalars(select(UavVerificationRecord).where(UavVerificationRecord.risk_assessment_id == row.id).order_by(UavVerificationRecord.created_at)).all()
    if not overlays and not verifications: return None
    overlay_data = [serialize_overlay(x, db.get(UavDataset, x.uav_dataset_id)) for x in overlays]
    verification_data = []
    for item in verifications:
        data = serialize_verification(item, db.get(UavDataset, item.uav_dataset_id), db.get(WorkArea, item.work_area_id) if item.work_area_id else None)
        data["image_crop_path"] = item.image_crop_path
        verification_data.append(data)
    return {"overlays": overlay_data, "verifications": verification_data}
