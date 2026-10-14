import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import (
    ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentRiskLink, OperationAssistanceRecord,
    RiskAssessment, WorkArea,
)
from ..schemas.common import ok
from ..schemas.equipment import EquipmentCreate, EquipmentRiskRun, EquipmentUpdate, PositionCreate
from ..services.equipment_service import (
    EquipmentError, areas_for_position, create_risk_link, latest_link, latest_position,
    run_project_equipment_links, serialize_equipment, serialize_link, serialize_position, validate_position,
)
from ..services.operation_assistance_service import serialize_record

router = APIRouter(tags=["construction-equipment"])


def _equipment(equipment_id: str, db: Session) -> ConstructionEquipment:
    row = db.get(ConstructionEquipment, equipment_id)
    if not row: raise HTTPException(status_code=404, detail="工程机械不存在")
    return row


def _project(project_id: str, db: Session) -> ConstructionProject:
    row = db.get(ConstructionProject, project_id)
    if not row: raise HTTPException(status_code=404, detail="施工项目不存在")
    return row


def _commit(db: Session, message: str):
    try: db.commit()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=message) from exc


def _equipment_data(db: Session, row: ConstructionEquipment) -> dict:
    position = latest_position(db, row.id); link = latest_link(db, row.id)
    data = serialize_equipment(row, db.get(ConstructionProject, row.project_id), position)
    data["latest_risk_link"] = _link_data(db, link) if link else None
    return data


@router.post("/equipment")
def create_equipment(payload: EquipmentCreate, db: Session = Depends(get_db)):
    project = _project(payload.project_id, db)
    if project.status != "active": raise HTTPException(status_code=409, detail="已归档项目不能新增工程机械")
    row = ConstructionEquipment(id=str(uuid.uuid4()), **payload.model_dump())
    db.add(row); _commit(db, "同一项目内设备编号不能重复")
    return ok(_equipment_data(db, row), "工程机械已创建")


@router.get("/equipment")
def list_equipment(project_id: str | None = None, lifecycle_status: str | None = None, db: Session = Depends(get_db)):
    statement = select(ConstructionEquipment)
    if project_id: statement = statement.where(ConstructionEquipment.project_id == project_id)
    if lifecycle_status: statement = statement.where(ConstructionEquipment.lifecycle_status == lifecycle_status)
    rows = db.scalars(statement.order_by(ConstructionEquipment.updated_at.desc())).all()
    return ok([_equipment_data(db, row) for row in rows])


@router.post("/equipment/positions/import")
async def import_positions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not (file.filename or "").lower().endswith(".csv"): raise HTTPException(status_code=400, detail="仅支持 CSV 位置文件")
    try: content = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as exc: raise HTTPException(status_code=400, detail="CSV 必须使用 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(content)); required = {"equipment_code", "x", "y", "crs", "recorded_at"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=400, detail="CSV 缺少必填列：equipment_code、x、y、crs、recorded_at")
    items = []; success = 0
    for line, raw in enumerate(reader, 2):
        code = (raw.get("equipment_code") or "").strip()
        try:
            matches = db.scalars(select(ConstructionEquipment).where(ConstructionEquipment.equipment_code == code)).all()
            if not matches: raise EquipmentError("设备编号不存在")
            if len(matches) > 1: raise EquipmentError("设备编号跨项目重复，请分项目使用唯一编号后再导入")
            equipment = matches[0]; project = _project(equipment.project_id, db)
            if project.status != "active" or equipment.lifecycle_status != "active": raise EquipmentError("设备或所属项目已归档")
            x, y = float(raw["x"]), float(raw["y"]); crs = validate_position(x, y, raw["crs"])
            recorded_at = datetime.fromisoformat(raw["recorded_at"].replace("Z", "+00:00"))
            row = EquipmentPosition(id=str(uuid.uuid4()), equipment_id=equipment.id, project_id=equipment.project_id,
                                    x=x, y=y, crs=crs, position_source="csv_import", recorded_at=recorded_at,
                                    source_note=(raw.get("source_note") or "").strip())
            db.add(row); db.commit(); success += 1
            items.append({"line":line,"equipment_code":code,"status":"success","position_id":row.id})
        except (ValueError, EquipmentError, HTTPException) as exc:
            db.rollback(); items.append({"line":line,"equipment_code":code,"status":"failed","message":getattr(exc,"detail",str(exc))})
    return ok({"total":len(items),"success_count":success,"failed_count":len(items)-success,"items":items}, "CSV位置导入完成")


@router.get("/equipment/{equipment_id}")
def equipment_detail(equipment_id: str, db: Session = Depends(get_db)):
    row = _equipment(equipment_id, db); data = _equipment_data(db, row)
    positions = db.scalars(select(EquipmentPosition).where(EquipmentPosition.equipment_id == row.id).order_by(EquipmentPosition.recorded_at.desc())).all()
    links = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.equipment_id == row.id).order_by(EquipmentRiskLink.created_at.desc())).all()
    data["positions"] = [serialize_position(item) for item in positions]
    data["risk_links"] = [_link_data(db, item) for item in links]
    if positions:
        data["current_work_areas"] = [{"id":area.id,"area_name":area.area_name,"area_type":area.area_type} for area in areas_for_position(db, positions[0])]
    else: data["current_work_areas"] = []
    assistance = db.scalars(select(OperationAssistanceRecord).where(OperationAssistanceRecord.equipment_id == row.id).order_by(OperationAssistanceRecord.created_at.desc())).all()
    data["assistance_records"] = [serialize_record(db, item) for item in assistance]
    data["latest_assistance_verification"] = next((item["verifications"][0] for item in data["assistance_records"] if item.get("verifications")), None)
    return ok(data)


@router.patch("/equipment/{equipment_id}")
def update_equipment(equipment_id: str, payload: EquipmentUpdate, db: Session = Depends(get_db)):
    row = _equipment(equipment_id, db)
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    _commit(db, "同一项目内设备编号不能重复")
    return ok(_equipment_data(db, row), "工程机械已更新")


@router.delete("/equipment/{equipment_id}")
def delete_equipment(equipment_id: str, db: Session = Depends(get_db)):
    row = _equipment(equipment_id, db)
    if db.scalar(select(OperationAssistanceRecord.id).where(OperationAssistanceRecord.equipment_id == row.id).limit(1)):
        raise HTTPException(status_code=409, detail="该设备已有作业辅助事项，为保证历史追溯不能删除；可将其归档。")
    if db.scalar(select(EquipmentPosition.id).where(EquipmentPosition.equipment_id == row.id).limit(1)) or db.scalar(select(EquipmentRiskLink.id).where(EquipmentRiskLink.equipment_id == row.id).limit(1)):
        raise HTTPException(status_code=409, detail="该设备已有位置或水害关联历史，不能删除；可将其归档。")
    db.delete(row); db.commit(); return ok(message="工程机械已删除")


@router.post("/equipment/{equipment_id}/positions")
def add_position(equipment_id: str, payload: PositionCreate, db: Session = Depends(get_db)):
    equipment = _equipment(equipment_id, db); project = _project(equipment.project_id, db)
    if project.status != "active" or equipment.lifecycle_status != "active": raise HTTPException(status_code=409, detail="设备或所属项目已归档，不能新增位置快照")
    try: crs = validate_position(payload.x, payload.y, payload.crs)
    except EquipmentError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = EquipmentPosition(id=str(uuid.uuid4()), equipment_id=equipment.id, project_id=equipment.project_id,
                            position_source="manual", crs=crs, **payload.model_dump(exclude={"crs"}))
    db.add(row); db.commit(); return ok(serialize_position(row), "设备位置快照已记录")


@router.get("/equipment/{equipment_id}/positions")
def list_positions(equipment_id: str, db: Session = Depends(get_db)):
    _equipment(equipment_id, db)
    rows = db.scalars(select(EquipmentPosition).where(EquipmentPosition.equipment_id == equipment_id).order_by(EquipmentPosition.recorded_at.desc())).all()
    return ok([serialize_position(row) for row in rows])


def _link_data(db: Session, row: EquipmentRiskLink) -> dict:
    equipment = db.get(ConstructionEquipment, row.equipment_id); position = db.get(EquipmentPosition, row.position_id)
    ids = __import__("json").loads(row.work_area_ids or "[]")
    areas = db.scalars(select(WorkArea).where(WorkArea.id.in_(ids))).all() if ids else []
    ordered = sorted(areas, key=lambda area: ids.index(area.id))
    return serialize_link(row, equipment, position, ordered)


@router.post("/equipment/{equipment_id}/risk-link")
def run_equipment_link(equipment_id: str, payload: EquipmentRiskRun, db: Session = Depends(get_db)):
    equipment = _equipment(equipment_id, db); risk = db.get(RiskAssessment, payload.risk_assessment_id)
    if not risk: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    try: row = create_risk_link(db, equipment, risk)
    except EquipmentError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(_link_data(db, row), "设备水害关联已生成")


@router.get("/equipment/{equipment_id}/risk-links")
def equipment_links(equipment_id: str, db: Session = Depends(get_db)):
    _equipment(equipment_id, db)
    rows = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.equipment_id == equipment_id).order_by(EquipmentRiskLink.created_at.desc())).all()
    return ok([_link_data(db, row) for row in rows])


@router.get("/equipment/{equipment_id}/risk-links/{link_id}")
def equipment_link_detail(equipment_id: str, link_id: str, db: Session = Depends(get_db)):
    row = db.get(EquipmentRiskLink, link_id)
    if not row or row.equipment_id != equipment_id: raise HTTPException(status_code=404, detail="设备水害关联记录不存在")
    return ok(_link_data(db, row))


@router.post("/risk-assessments/{assessment_id}/equipment-links")
def run_risk_equipment_links(assessment_id: str, db: Session = Depends(get_db)):
    risk = db.get(RiskAssessment, assessment_id)
    if not risk: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    try: rows = run_project_equipment_links(db, risk)
    except EquipmentError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok([_link_data(db, row) for row in rows], "工程机械水害关联分析已完成")


@router.get("/risk-assessments/{assessment_id}/equipment-links")
def risk_equipment_links(assessment_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == assessment_id).order_by(EquipmentRiskLink.created_at.desc())).all()
    return ok([_link_data(db, row) for row in rows])


@router.get("/risk-assessments/{assessment_id}/equipment-links.csv")
def export_equipment_links_csv(assessment_id: str, db: Session = Depends(get_db)):
    risk = db.get(RiskAssessment, assessment_id)
    if not risk: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    project = db.get(ConstructionProject, risk.project_id) if risk.project_id else None
    rows = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == assessment_id).order_by(EquipmentRiskLink.created_at)).all()
    stream = io.StringIO(); writer = csv.writer(stream)
    writer.writerow(["risk_assessment_id","equipment_id","equipment_code","equipment_name","equipment_type","project","position_time","position_source","position_crs","x","y","position_age_hours","position_status","work_area","work_area_type","risk_zone_class","risk_zone_label","work_area_impact","advice","rule_version"])
    for row in rows:
        data = _link_data(db, row); position = data["position"] or {}; primary = db.get(WorkArea, row.primary_work_area_id) if row.primary_work_area_id else None
        writer.writerow([assessment_id,row.equipment_id,data["equipment_code"],data["equipment_name"],data["equipment_type_label"],project.project_name if project else "",row.recorded_at,position.get("position_source_label"),position.get("crs"),position.get("x"),position.get("y"),row.position_age_hours,row.position_status,data["primary_work_area_name"],primary.area_type if primary else "",row.risk_class,row.risk_label,row.work_area_impact_level or "",row.advice_text,row.rule_version])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]), media_type="text/csv",
                             headers={"Content-Disposition":f'attachment; filename="equipment_links_{assessment_id[:8]}.csv"'})
