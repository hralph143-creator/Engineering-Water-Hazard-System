from __future__ import annotations

import csv
import io
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.database import get_db
from ..db.models import (
    ConstructionProject, DatasetRecord, EquipmentPosition, RainfallEvent, RiskAssessment,
    UavDataset, UavRiskOverlay, UavVerificationRecord, UavWorkAreaCoverage, WorkArea,
)
from ..schemas.common import ok
from ..schemas.uav import UavCoverageRequest, UavOverlayRequest, UavVerificationCreate, UavVerificationUpdate
from ..services.uav_service import (
    VERIFICATION_VERSION, UavError, analyze_risk_overlay, analyze_work_area, ensure_preview,
    inspect_uav, serialize_coverage, serialize_overlay, serialize_uav, serialize_verification, sha256_file,
)

router = APIRouter(tags=["uav"])


def _uav(dataset_id: str, db: Session) -> UavDataset:
    row = db.get(UavDataset, dataset_id)
    if not row: raise HTTPException(status_code=404, detail="无人机影像不存在")
    return row


def _file(value: str | None, label: str) -> Path:
    path = Path(value) if value else None
    if not path or not path.is_file(): raise HTTPException(status_code=404, detail=f"{label}不存在")
    return path


@router.post("/uav-datasets")
def upload_uav(file: UploadFile = File(...), project_id: str = Form(...), capture_time: datetime | None = Form(None),
               flight_name: str = Form(""), flight_batch: str = Form(""), source_name: str = Form(""),
               source_note: str = Form(""), device_note: str = Form(""), resolution_note: str = Form(""),
               db: Session = Depends(get_db)):
    project = db.get(ConstructionProject, project_id)
    if not project: raise HTTPException(status_code=404, detail="施工项目不存在")
    filename = Path(file.filename or "unnamed").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".tif", ".tiff"}: raise HTTPException(status_code=400, detail="正式空间复核仅支持 .tif 或 .tiff GeoTIFF正射影像")
    dataset_id = str(uuid.uuid4()); directory = get_config().paths.uploads / "uav"; directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{dataset_id}{suffix}"
    try:
        with target.open("wb") as output: shutil.copyfileobj(file.file, output)
        digest = sha256_file(target)
        if db.scalar(select(UavDataset.id).where(UavDataset.file_hash == digest)):
            raise HTTPException(status_code=409, detail="该无人机正射影像已存在。")
        metadata = inspect_uav(target)
        row = UavDataset(id=dataset_id, project_id=project_id, name=filename, file_path=str(target), file_hash=digest,
                         capture_time=capture_time, flight_name=flight_name.strip(), flight_batch=flight_batch.strip(),
                         source_name=source_name.strip(), source_note=source_note.strip(), device_note=device_note.strip(),
                         resolution_note=resolution_note.strip(), **metadata)
        db.add(row); db.flush(); ensure_preview(row); db.commit(); db.refresh(row)
        return ok(serialize_uav(row, project), "无人机正射影像导入成功")
    except HTTPException:
        db.rollback(); target.unlink(missing_ok=True); raise
    except UavError as exc:
        db.rollback(); target.unlink(missing_ok=True); raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback(); target.unlink(missing_ok=True); raise HTTPException(status_code=409, detail="该无人机正射影像已存在。") from exc


@router.get("/uav-datasets")
def list_uav(project_id: str | None = None, include_archived: bool = False, db: Session = Depends(get_db)):
    statement = select(UavDataset)
    if project_id: statement = statement.where(UavDataset.project_id == project_id)
    if not include_archived: statement = statement.where(UavDataset.status != "archived")
    rows = db.scalars(statement.order_by(UavDataset.capture_time.desc(), UavDataset.created_at.desc())).all()
    projects = {x.id:x for x in db.scalars(select(ConstructionProject).where(ConstructionProject.id.in_([r.project_id for r in rows]))).all()} if rows else {}
    return ok([serialize_uav(row, projects.get(row.project_id)) for row in rows])


@router.get("/uav-datasets/{dataset_id}")
def uav_detail(dataset_id: str, db: Session = Depends(get_db)):
    row = _uav(dataset_id, db); data = serialize_uav(row, db.get(ConstructionProject, row.project_id))
    data.update(work_area_coverage_count=db.scalar(select(func.count()).select_from(UavWorkAreaCoverage).where(UavWorkAreaCoverage.uav_dataset_id == row.id)) or 0,
                risk_overlay_count=db.scalar(select(func.count()).select_from(UavRiskOverlay).where(UavRiskOverlay.uav_dataset_id == row.id)) or 0,
                verification_count=db.scalar(select(func.count()).select_from(UavVerificationRecord).where(UavVerificationRecord.uav_dataset_id == row.id)) or 0)
    return ok(data)


@router.get("/uav-datasets/{dataset_id}/preview")
def uav_preview(dataset_id: str, db: Session = Depends(get_db)):
    row = _uav(dataset_id, db)
    try: path = ensure_preview(row); db.commit()
    except UavError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=f"uav_{dataset_id[:8]}_preview.png")


@router.delete("/uav-datasets/{dataset_id}")
def delete_uav(dataset_id: str, db: Session = Depends(get_db)):
    row = _uav(dataset_id, db)
    referenced = any(db.scalar(select(model.id).where(model.uav_dataset_id == row.id).limit(1))
                     for model in (UavWorkAreaCoverage, UavRiskOverlay, UavVerificationRecord))
    if referenced:
        row.status = "archived"; db.commit()
        return ok(message="无人机影像已归档；空间覆盖和人工复核历史已保留。")
    paths = [row.file_path, row.preview_path]; db.delete(row); db.commit()
    for value in paths:
        if value: Path(value).unlink(missing_ok=True)
    return ok(message="无人机影像已删除")


@router.post("/uav-datasets/{dataset_id}/work-area-coverage")
def run_work_area_coverage(dataset_id: str, payload: UavCoverageRequest, db: Session = Depends(get_db)):
    row = _uav(dataset_id, db); area = db.get(WorkArea, payload.work_area_id)
    if not area: raise HTTPException(status_code=404, detail="施工区域不存在")
    try: result = analyze_work_area(db, row, area)
    except UavError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(serialize_coverage(result, row, area), "施工区域无人机覆盖分析已完成")


@router.get("/uav-datasets/{dataset_id}/work-area-coverage")
def list_work_area_coverage(dataset_id: str, db: Session = Depends(get_db)):
    row = _uav(dataset_id, db)
    items = db.scalars(select(UavWorkAreaCoverage).where(UavWorkAreaCoverage.uav_dataset_id == row.id).order_by(UavWorkAreaCoverage.created_at.desc())).all()
    return ok([serialize_coverage(x, row, db.get(WorkArea, x.work_area_id)) for x in items])


@router.get("/work-areas/{area_id}/uav-coverages")
def area_uav_coverages(area_id: str, db: Session = Depends(get_db)):
    area = db.get(WorkArea, area_id)
    if not area: raise HTTPException(status_code=404, detail="施工区域不存在")
    items = db.scalars(select(UavWorkAreaCoverage).where(UavWorkAreaCoverage.work_area_id == area.id).order_by(UavWorkAreaCoverage.created_at.desc())).all()
    return ok([serialize_coverage(x, db.get(UavDataset, x.uav_dataset_id), area) for x in items])


@router.get("/uav-coverages/{coverage_id}/crop")
def coverage_crop(coverage_id: str, db: Session = Depends(get_db)):
    row = db.get(UavWorkAreaCoverage, coverage_id)
    if not row: raise HTTPException(status_code=404, detail="施工区域无人机覆盖记录不存在")
    return FileResponse(_file(row.crop_path, "施工区域局部影像"), media_type="image/png")


@router.post("/risk-assessments/{assessment_id}/uav-overlay")
def run_uav_overlay(assessment_id: str, payload: UavOverlayRequest, db: Session = Depends(get_db)):
    risk = db.get(RiskAssessment, assessment_id)
    if not risk: raise HTTPException(status_code=404, detail="风险研判记录不存在")
    uav = _uav(payload.uav_dataset_id, db)
    try: row = analyze_risk_overlay(db, risk, uav)
    except UavError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(serialize_overlay(row, uav), "无人机与风险区域协同分析已完成")


@router.get("/risk-assessments/{assessment_id}/uav-overlays")
def risk_uav_overlays(assessment_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(UavRiskOverlay).where(UavRiskOverlay.risk_assessment_id == assessment_id).order_by(UavRiskOverlay.created_at.desc())).all()
    return ok([serialize_overlay(row, db.get(UavDataset, row.uav_dataset_id)) for row in rows])


@router.get("/uav-overlays/{overlay_id}/{kind}")
def overlay_file(overlay_id: str, kind: str, db: Session = Depends(get_db)):
    row = db.get(UavRiskOverlay, overlay_id)
    if not row: raise HTTPException(status_code=404, detail="无人机风险覆盖记录不存在")
    mapping = {"high-attention-crop": (row.high_attention_crop_path, "高关注区域局部图"),
               "inspection-crop": (row.inspection_crop_path, "重点巡检区域局部图"),
               "summary": (row.summary_json_path, "协同分析摘要")}
    if kind not in mapping: raise HTTPException(status_code=404, detail="成果类型不存在")
    path, label = mapping[kind]
    return FileResponse(_file(path, label), media_type="application/json" if kind == "summary" else "image/png")


def _validate_verification_links(payload, db: Session):
    project = db.get(ConstructionProject, payload.project_id); uav = db.get(UavDataset, payload.uav_dataset_id)
    risk = db.get(RiskAssessment, payload.risk_assessment_id); area = db.get(WorkArea, payload.work_area_id) if payload.work_area_id else None
    if not project or not uav or not risk: raise HTTPException(status_code=404, detail="项目、无人机影像或风险研判不存在")
    if uav.project_id != project.id or risk.project_id != project.id or (area and area.project_id != project.id):
        raise HTTPException(status_code=400, detail="人工复核关联对象不属于同一施工项目")
    return uav, risk, area


@router.post("/uav-verifications")
def create_verification(payload: UavVerificationCreate, db: Session = Depends(get_db)):
    uav, _, area = _validate_verification_links(payload, db)
    crop = None
    if area:
        coverage = db.scalar(select(UavWorkAreaCoverage).where(UavWorkAreaCoverage.uav_dataset_id == uav.id,
                             UavWorkAreaCoverage.work_area_id == area.id)); crop = coverage.crop_path if coverage else None
    if not crop:
        overlay = db.scalar(select(UavRiskOverlay).where(UavRiskOverlay.uav_dataset_id == uav.id,
                            UavRiskOverlay.risk_assessment_id == payload.risk_assessment_id)); crop = overlay.high_attention_crop_path if overlay else None
    values = payload.model_dump()
    if values["verification_status"] != "PENDING" and not values.get("verified_at"):
        values["verified_at"] = datetime.now(timezone.utc)
    row = UavVerificationRecord(id=str(uuid.uuid4()), image_crop_path=crop, structure_version=VERIFICATION_VERSION, **values)
    db.add(row); db.commit(); db.refresh(row)
    return ok(serialize_verification(row, uav, area), "无人机人工复核记录已创建")


@router.get("/uav-verifications")
def list_verifications(project_id: str | None = None, risk_assessment_id: str | None = None,
                       work_area_id: str | None = None, db: Session = Depends(get_db)):
    statement = select(UavVerificationRecord)
    if project_id: statement = statement.where(UavVerificationRecord.project_id == project_id)
    if risk_assessment_id: statement = statement.where(UavVerificationRecord.risk_assessment_id == risk_assessment_id)
    if work_area_id: statement = statement.where(UavVerificationRecord.work_area_id == work_area_id)
    rows = db.scalars(statement.order_by(UavVerificationRecord.created_at.desc())).all()
    return ok([serialize_verification(x, db.get(UavDataset, x.uav_dataset_id), db.get(WorkArea, x.work_area_id) if x.work_area_id else None) for x in rows])


@router.get("/uav-verifications/{verification_id}")
def verification_detail(verification_id: str, db: Session = Depends(get_db)):
    row = db.get(UavVerificationRecord, verification_id)
    if not row: raise HTTPException(status_code=404, detail="无人机人工复核记录不存在")
    return ok(serialize_verification(row, db.get(UavDataset, row.uav_dataset_id), db.get(WorkArea, row.work_area_id) if row.work_area_id else None))


@router.patch("/uav-verifications/{verification_id}")
def update_verification(verification_id: str, payload: UavVerificationUpdate, db: Session = Depends(get_db)):
    row = db.get(UavVerificationRecord, verification_id)
    if not row: raise HTTPException(status_code=404, detail="无人机人工复核记录不存在")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items(): setattr(row, key, value)
    if row.verification_status != "PENDING" and not row.verified_at: row.verified_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc); db.commit(); db.refresh(row)
    return ok(serialize_verification(row, db.get(UavDataset, row.uav_dataset_id), db.get(WorkArea, row.work_area_id) if row.work_area_id else None), "人工复核记录已更新")


@router.get("/uav-verifications/{verification_id}/image")
def verification_image(verification_id: str, db: Session = Depends(get_db)):
    row = db.get(UavVerificationRecord, verification_id)
    if not row: raise HTTPException(status_code=404, detail="无人机人工复核记录不存在")
    return FileResponse(_file(row.image_crop_path, "人工复核局部影像"), media_type="image/png")


def _csv_response(name: str, headers: list[str], rows: list[list]):
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/uav-datasets.csv")
def uav_csv(project_id: str | None = None, db: Session = Depends(get_db)):
    statement = select(UavDataset)
    if project_id: statement = statement.where(UavDataset.project_id == project_id)
    rows = db.scalars(statement.order_by(UavDataset.created_at)).all()
    return _csv_response("uav_datasets.csv", ["id","project_id","name","capture_time","flight_name","source_name","width","height","bands","crs","pixel_size_x","pixel_size_y","status"],
                         [[x.id,x.project_id,x.name,x.capture_time,x.flight_name,x.source_name,x.width,x.height,x.band_count,x.crs,x.pixel_size_x,x.pixel_size_y,x.status] for x in rows])


@router.get("/uav-work-area-coverages.csv")
def coverage_csv(project_id: str | None = None, db: Session = Depends(get_db)):
    statement = select(UavWorkAreaCoverage)
    if project_id: statement = statement.where(UavWorkAreaCoverage.project_id == project_id)
    rows = db.scalars(statement.order_by(UavWorkAreaCoverage.created_at)).all()
    return _csv_response("uav_work_area_coverages.csv", ["uav_dataset_id","work_area_id","coverage_status","covered_pixels","area_pixels","coverage_ratio","analysis_version"],
                         [[x.uav_dataset_id,x.work_area_id,x.coverage_status,x.covered_pixels,x.valid_area_pixels,x.coverage_ratio,x.analysis_version] for x in rows])


@router.get("/uav-risk-overlays.csv")
def overlay_csv(risk_assessment_id: str | None = None, db: Session = Depends(get_db)):
    statement = select(UavRiskOverlay)
    if risk_assessment_id: statement = statement.where(UavRiskOverlay.risk_assessment_id == risk_assessment_id)
    rows = db.scalars(statement.order_by(UavRiskOverlay.created_at)).all()
    return _csv_response("uav_risk_overlays.csv", ["uav_dataset_id","risk_assessment_id","high_attention_coverage_ratio","inspection_coverage_ratio","general_coverage_ratio","time_difference_hours","time_reference_status","analysis_version"],
                         [[x.uav_dataset_id,x.risk_assessment_id,x.high_attention_coverage_ratio,x.inspection_coverage_ratio,x.general_coverage_ratio,x.time_difference_hours,x.time_reference_status,x.analysis_version] for x in rows])


@router.get("/uav-verifications.csv")
def verification_csv(project_id: str | None = None, db: Session = Depends(get_db)):
    statement = select(UavVerificationRecord)
    if project_id: statement = statement.where(UavVerificationRecord.project_id == project_id)
    rows = db.scalars(statement.order_by(UavVerificationRecord.created_at)).all()
    return _csv_response("uav_verifications.csv", ["id","project_id","uav_dataset_id","risk_assessment_id","work_area_id","verification_status","observation_type","finding_text","recommendation_text","verified_at","structure_version"],
                         [[x.id,x.project_id,x.uav_dataset_id,x.risk_assessment_id,x.work_area_id,x.verification_status,x.observation_type,x.finding_text,x.recommendation_text,x.verified_at,x.structure_version] for x in rows])


@router.get("/construction-projects/{project_id}/uav-summary")
def project_uav_summary(project_id: str, db: Session = Depends(get_db)):
    if not db.get(ConstructionProject, project_id): raise HTTPException(status_code=404, detail="施工项目不存在")
    datasets = db.scalars(select(UavDataset).where(UavDataset.project_id == project_id, UavDataset.status != "archived")).all()
    covered = db.scalar(select(func.count(func.distinct(UavWorkAreaCoverage.work_area_id))).where(UavWorkAreaCoverage.project_id == project_id, UavWorkAreaCoverage.coverage_ratio > 0)) or 0
    reviewed = db.scalar(select(func.count()).select_from(UavVerificationRecord).where(UavVerificationRecord.project_id == project_id, UavVerificationRecord.verification_status == "REVIEWED")) or 0
    captures = [x.capture_time for x in datasets if x.capture_time]
    return ok({"project_id":project_id,"dataset_count":len(datasets),"latest_capture_time":max(captures) if captures else None,
               "covered_work_area_count":covered,"reviewed_count":reviewed})


@router.get("/construction-projects/{project_id}/multisource-timeline")
def multisource_timeline(project_id: str, db: Session = Depends(get_db)):
    if not db.get(ConstructionProject, project_id): raise HTTPException(status_code=404, detail="施工项目不存在")
    events = []
    for x in db.scalars(select(DatasetRecord).where(DatasetRecord.project_id == project_id, DatasetRecord.capture_time.is_not(None))).all():
        events.append({"time":x.capture_time,"type":"SAR_CAPTURE","label":f"SAR影像采集：{x.name}","source_id":x.id})
    for x in db.scalars(select(UavDataset).where(UavDataset.project_id == project_id)).all():
        events.append({"time":x.capture_time,"type":"UAV_CAPTURE","label":f"无人机影像采集：{x.name}","source_id":x.id,"time_recorded":bool(x.capture_time)})
    for x in db.scalars(select(RainfallEvent).where(RainfallEvent.project_id == project_id, RainfallEvent.event_status == "active")).all():
        events.append({"time":x.start_time,"type":"RAINFALL_EVENT","label":f"降雨事件开始：{x.total_rainfall_mm:.2f} mm","source_id":x.id})
    for x in db.scalars(select(RiskAssessment).where(RiskAssessment.project_id == project_id)).all():
        events.append({"time":x.completed_at,"type":"RISK_ASSESSMENT","label":f"风险研判：{x.assessment_name}","source_id":x.id})
    for x in db.scalars(select(EquipmentPosition).where(EquipmentPosition.project_id == project_id)).all():
        events.append({"time":x.recorded_at,"type":"EQUIPMENT_POSITION","label":"设备位置快照","source_id":x.id})
    for x in db.scalars(select(UavVerificationRecord).where(UavVerificationRecord.project_id == project_id)).all():
        events.append({"time":x.verified_at,"type":"UAV_VERIFICATION","label":f"无人机人工复核：{x.verification_status}","source_id":x.id,"time_recorded":bool(x.verified_at)})
    def sort_key(item):
        value = item["time"]
        if value is None: return datetime.min
        return value.replace(tzinfo=None) if value.tzinfo else value
    events.sort(key=sort_key)
    return ok(events)
