import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import ConstructionEquipment, ConstructionProject, OperationAssistanceRecord, RiskAssessment, WorkArea, WorkAreaImpact
from ..schemas.common import ok
from ..schemas.spatial import ImpactRunRequest, ProjectCreate, ProjectUpdate, WorkAreaCreate, WorkAreaUpdate
from ..services.spatial_service import (
    SpatialAnalysisError, run_project_impacts, serialize_area, serialize_impact, serialize_project, validate_crs, validate_geometry,
)
from ..services.equipment_service import areas_for_position, latest_link, latest_position, serialize_equipment
from ..services.operation_assistance_service import serialize_record

router = APIRouter(tags=["construction-spatial"])


def _project(project_id: str, db: Session) -> ConstructionProject:
    row = db.get(ConstructionProject, project_id)
    if not row: raise HTTPException(status_code=404, detail="施工项目不存在")
    return row


def _area(area_id: str, db: Session) -> WorkArea:
    row = db.get(WorkArea, area_id)
    if not row: raise HTTPException(status_code=404, detail="施工区域不存在")
    return row


def _commit(db: Session, message: str):
    try: db.commit()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=message) from exc


@router.post("/construction-projects")
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    row = ConstructionProject(id=str(uuid.uuid4()), **payload.model_dump())
    db.add(row); _commit(db, "项目编号已存在")
    return ok(serialize_project(row), "施工项目已创建")


@router.get("/construction-projects")
def list_projects(status: str | None = None, db: Session = Depends(get_db)):
    statement = select(ConstructionProject)
    if status: statement = statement.where(ConstructionProject.status == status)
    rows = db.scalars(statement.order_by(ConstructionProject.updated_at.desc())).all()
    counts = dict(db.execute(select(WorkArea.project_id, func.count()).group_by(WorkArea.project_id)).all())
    return ok([serialize_project(row, counts.get(row.id, 0)) for row in rows])


@router.get("/construction-projects/{project_id}")
def project_detail(project_id: str, db: Session = Depends(get_db)):
    row = _project(project_id, db)
    count = db.scalar(select(func.count()).select_from(WorkArea).where(WorkArea.project_id == row.id)) or 0
    return ok(serialize_project(row, count))


@router.patch("/construction-projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    row = _project(project_id, db)
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    _commit(db, "项目编号已存在")
    return ok(serialize_project(row), "施工项目已更新")


@router.delete("/construction-projects/{project_id}")
def archive_project(project_id: str, db: Session = Depends(get_db)):
    row = _project(project_id, db); row.status = "archived"; db.commit()
    return ok(message="施工项目已归档，历史区域和影响记录已保留")


def _latest_impact(db: Session, area_id: str):
    return db.scalar(select(WorkAreaImpact).where(WorkAreaImpact.work_area_id == area_id).order_by(WorkAreaImpact.created_at.desc()).limit(1))


@router.post("/work-areas")
def create_area(payload: WorkAreaCreate, db: Session = Depends(get_db)):
    project = _project(payload.project_id, db)
    if project.status != "active": raise HTTPException(status_code=409, detail="已归档项目不能新增施工区域")
    try:
        geometry = validate_geometry(payload.geometry_geojson)
        crs = validate_crs(payload.crs)
    except SpatialAnalysisError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    values = payload.model_dump(exclude={"geometry_geojson", "crs"})
    row = WorkArea(id=str(uuid.uuid4()), geometry_type=geometry["type"],
                   geometry_geojson=json.dumps(geometry, ensure_ascii=False, separators=(",", ":")), crs=crs, **values)
    db.add(row); _commit(db, "同一项目内施工区域编号不能重复")
    return ok(serialize_area(row), "施工区域已创建")


@router.get("/work-areas")
def list_areas(project_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    statement = select(WorkArea)
    if project_id: statement = statement.where(WorkArea.project_id == project_id)
    if status: statement = statement.where(WorkArea.status == status)
    rows = db.scalars(statement.order_by(WorkArea.updated_at.desc())).all()
    return ok([serialize_area(row, _latest_impact(db, row.id)) for row in rows])


@router.get("/work-areas/{area_id}")
def area_detail(area_id: str, db: Session = Depends(get_db)):
    row = _area(area_id, db)
    impacts = db.scalars(select(WorkAreaImpact).where(WorkAreaImpact.work_area_id == row.id).order_by(WorkAreaImpact.created_at.desc())).all()
    data = serialize_area(row, impacts[0] if impacts else None)
    data["impact_history"] = [serialize_impact(item, row) for item in impacts]
    equipment_rows = db.scalars(select(ConstructionEquipment).where(
        ConstructionEquipment.project_id == row.project_id,
        ConstructionEquipment.lifecycle_status == "active",
    )).all()
    current_equipment = []
    project = db.get(ConstructionProject, row.project_id)
    for equipment in equipment_rows:
        position = latest_position(db, equipment.id)
        if position and any(item.id == row.id for item in areas_for_position(db, position)):
            current_equipment.append(serialize_equipment(equipment, project, position, latest_link(db, equipment.id)))
    data["current_equipment"] = current_equipment
    assistance = db.scalars(select(OperationAssistanceRecord).where(OperationAssistanceRecord.work_area_id == row.id).order_by(OperationAssistanceRecord.created_at.desc())).all()
    data["assistance_records"] = [serialize_record(db, item) for item in assistance]
    latest_check = next((item["verifications"][0] for item in data["assistance_records"] if item.get("verifications")), None)
    data["latest_assistance_verification"] = latest_check
    return ok(data)


@router.patch("/work-areas/{area_id}")
def update_area(area_id: str, payload: WorkAreaUpdate, db: Session = Depends(get_db)):
    row = _area(area_id, db); values = payload.model_dump(exclude_unset=True)
    if "geometry_geojson" in values:
        try: geometry = validate_geometry(values.pop("geometry_geojson"))
        except SpatialAnalysisError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
        row.geometry_type, row.geometry_geojson = geometry["type"], json.dumps(geometry, ensure_ascii=False, separators=(",", ":"))
    if "crs" in values:
        try: values["crs"] = validate_crs(values["crs"])
        except SpatialAnalysisError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key, value in values.items(): setattr(row, key, value)
    _commit(db, "同一项目内施工区域编号不能重复")
    return ok(serialize_area(row, _latest_impact(db, row.id)), "施工区域已更新")


@router.delete("/work-areas/{area_id}")
def delete_area(area_id: str, db: Session = Depends(get_db)):
    row = _area(area_id, db)
    if db.scalar(select(OperationAssistanceRecord.id).where(OperationAssistanceRecord.work_area_id == row.id).limit(1)):
        raise HTTPException(status_code=409, detail="该施工区域已有作业辅助事项，为保证历史追溯不能删除；可将其归档。")
    if db.scalar(select(WorkAreaImpact.id).where(WorkAreaImpact.work_area_id == row.id).limit(1)):
        raise HTTPException(status_code=409, detail="该施工区域已有历史水害影响记录，不能删除；可将其归档。")
    db.delete(row); db.commit(); return ok(message="施工区域已删除")


@router.post("/risk-assessments/{assessment_id}/work-area-impacts")
def run_impacts(assessment_id: str, payload: ImpactRunRequest, db: Session = Depends(get_db)):
    risk = db.get(RiskAssessment, assessment_id)
    if not risk: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    project = _project(payload.project_id, db)
    try: rows = run_project_impacts(db, risk, project)
    except SpatialAnalysisError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    areas = {row.id: row for row in db.scalars(select(WorkArea).where(WorkArea.project_id == project.id)).all()}
    return ok([serialize_impact(row, areas.get(row.work_area_id)) for row in rows], "施工区域影响分析已完成")


@router.get("/risk-assessments/{assessment_id}/work-area-impacts")
def risk_impacts(assessment_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == assessment_id).order_by(WorkAreaImpact.high_attention_ratio.desc())).all()
    areas = {row.id: row for row in db.scalars(select(WorkArea).where(WorkArea.id.in_([x.work_area_id for x in rows]))).all()} if rows else {}
    return ok([serialize_impact(row, areas.get(row.work_area_id)) for row in rows])


@router.get("/work-areas/{area_id}/impacts")
def area_impacts(area_id: str, db: Session = Depends(get_db)):
    area = _area(area_id, db)
    rows = db.scalars(select(WorkAreaImpact).where(WorkAreaImpact.work_area_id == area.id).order_by(WorkAreaImpact.created_at.desc())).all()
    return ok([serialize_impact(row, area) for row in rows])
