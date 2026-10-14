from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db.models import AssistanceVerification, ChangeAnalysis, ChangeRainfallContext, ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentRiskLink, EquipmentTerrainSnapshot, InferenceResult, InferenceTask, OperationAssistanceRecord, RainfallStation, RiskAssessment, RiskRainfallContext, TerrainDataset, TerrainFusionAnalysis, UavDataset, UavRiskOverlay, UavVerificationRecord, WorkArea, WorkAreaImpact, WorkAreaTerrainProfile
from .operation_assistance_service import serialize_record
from .risk_service import serialize_risk
from .spatial_service import serialize_impact
from .equipment_service import serialize_link
from .terrain_service import serialize_fusion, serialize_profile, serialize_snapshot
from .rainfall_service import serialize_change_context, serialize_risk_context
from .uav_service import serialize_overlay, serialize_verification
from .compare_service import change_statistics, spatial_consistent


class AIContextError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _finite(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def _numeric_stats(raw: dict[str, Any] | None) -> dict[str, int | float | None]:
    return {str(key): _finite(value) for key, value in (raw or {}).items() if _finite(value) is not None}


def _equipment_links_context(db: Session, assessment_id: str) -> list[dict]:
    rows = db.scalars(select(EquipmentRiskLink).where(
        EquipmentRiskLink.risk_assessment_id == assessment_id
    ).order_by(EquipmentRiskLink.created_at)).all()
    output = []
    for row in rows:
        equipment = db.get(ConstructionEquipment, row.equipment_id)
        position = db.get(EquipmentPosition, row.position_id)
        area_ids = json.loads(row.work_area_ids or "[]")
        areas = [area for area_id in area_ids if (area := db.get(WorkArea, area_id))]
        data = serialize_link(row, equipment, position, areas)
        data.update(recorded_at=_iso(row.recorded_at), risk_analysis_at=_iso(row.risk_analysis_at), created_at=_iso(row.created_at))
        if data.get("position") and position:
            data["position"] = dict(data["position"], recorded_at=_iso(position.recorded_at), created_at=_iso(position.created_at))
        output.append(data)
    return output


def _terrain_context(db: Session, risk: RiskAssessment) -> dict | None:
    fusion = db.scalar(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id == risk.id).order_by(TerrainFusionAnalysis.created_at.desc()).limit(1))
    if not fusion: return None
    dem = db.get(TerrainDataset, fusion.dem_id); fusion_data = serialize_fusion(fusion, dem); fusion_data["created_at"] = _iso(fusion.created_at)
    profiles = []
    for row in db.scalars(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.dem_id == fusion.dem_id,
                          WorkAreaTerrainProfile.project_id == risk.project_id)).all():
        data = serialize_profile(row, dem); data["created_at"] = _iso(row.created_at); area = db.get(WorkArea,row.work_area_id); data["area_name"] = area.area_name if area else None; profiles.append(data)
    equipment = []
    links = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == risk.id)).all()
    for link in links:
        snapshot = db.scalar(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.position_id == link.position_id,
                             EquipmentTerrainSnapshot.dem_id == fusion.dem_id).limit(1))
        if snapshot:
            data=serialize_snapshot(snapshot,dem); data["created_at"]=_iso(snapshot.created_at); item=db.get(ConstructionEquipment,snapshot.equipment_id); data["equipment_name"]=item.equipment_name if item else None; equipment.append(data)
    return {"dem":{"id":dem.id,"name":dem.name,"source_name":dem.source_name or None,"capture_date":dem.capture_date or None,
                   "min_elevation":dem.min_elevation,"max_elevation":dem.max_elevation,"mean_elevation":dem.mean_elevation} if dem else None,
            "fusion":fusion_data,"work_area_profiles":profiles,"equipment_terrain_snapshots":equipment,
            "boundary":"相对低高程只表示当前DEM内部的相对地形位置，不表示必然积水；坡度和高程不用于预测水深、边坡稳定、道路承载或设备通行安全。"}


def _rainfall_context(db: Session, target, risk: bool=False) -> dict:
    model=RiskRainfallContext if risk else ChangeRainfallContext
    field=model.risk_assessment_id if risk else model.change_analysis_id
    row=db.scalar(select(model).where(field==target.id))
    if not row:
        return {"available":False,"message":"当前系统没有对应时间范围的降雨观测数据。","boundary":"无记录不等于没有降雨。"}
    data=serialize_risk_context(row) if risk else serialize_change_context(row)
    sources=db.scalars(select(RainfallStation).where(RainfallStation.project_id==row.project_id)).all()
    data.update(available=True,stations=[{"station_code":x.station_code,"station_name":x.station_name,"source_name":x.source_name or None} for x in sources],boundary="降雨与积水变化之间仅作同期和时间关联解释，不代表已验证因果关系；不用于未来降雨、径流、水深或洪水预测。")
    return data


def _uav_context(db: Session, risk: RiskAssessment) -> dict:
    overlays = db.scalars(select(UavRiskOverlay).where(UavRiskOverlay.risk_assessment_id == risk.id).order_by(UavRiskOverlay.created_at)).all()
    verifications = db.scalars(select(UavVerificationRecord).where(UavVerificationRecord.risk_assessment_id == risk.id).order_by(UavVerificationRecord.created_at)).all()
    overlay_data = [serialize_overlay(row, db.get(UavDataset, row.uav_dataset_id)) for row in overlays]
    verification_data = [serialize_verification(row, db.get(UavDataset, row.uav_dataset_id), db.get(WorkArea, row.work_area_id) if row.work_area_id else None) for row in verifications]
    return {"available": bool(overlay_data or verification_data), "risk_overlays": overlay_data,
            "manual_verifications": verification_data, "spatial_version": "uav-spatial-v1",
            "verification_version": "uav-verification-v1",
            "boundary": "无人机数据仅用于局部精细查看与人工复核；不存在无人机自动积水识别模型，不改变water-risk-v1等级，不判断道路或设备可安全通行。"}


def _stored_stats(raw: str | None) -> dict[str, int | float | None]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return _numeric_stats(value if isinstance(value, dict) else {})


def get_completed_task(db: Session, task_id: str) -> InferenceTask:
    statement = (
        select(InferenceTask)
        .options(joinedload(InferenceTask.dataset), joinedload(InferenceTask.result))
        .where(InferenceTask.id == task_id)
    )
    task = db.scalars(statement).unique().first()
    if not task:
        raise AIContextError("任务不存在", 404)
    if task.status != "completed" or not task.result:
        raise AIContextError("仅已完成且包含识别结果的任务可进行 AI 分析", 409)
    return task


def build_task_context(db: Session, task_id: str) -> dict[str, Any]:
    task = get_completed_task(db, task_id)
    result, dataset = task.result, task.dataset
    missing = []
    optional = {
        "probability_min": _finite(result.probability_min),
        "probability_max": _finite(result.probability_max),
        "probability_mean": _finite(result.probability_mean),
        "probability_median": _finite(result.probability_median),
        "high_confidence_flood_pixel_count": result.high_confidence_flood_pixel_count,
        "high_confidence_flood_ratio": _finite(result.high_confidence_flood_ratio),
    }
    missing.extend(key for key, value in optional.items() if value is None)
    return {
        "context_type": "completed_inference_task",
        "task": {
            "task_id": task.id,
            "status": task.status,
            "created_at": _iso(task.created_at),
            "started_at": _iso(task.started_at),
            "finished_at": _iso(task.finished_at),
            "model_name": task.model_name,
            "threshold": _finite(task.threshold),
        },
        "dataset": {
            "name": dataset.name if dataset else None,
            "source_type": dataset.source_type if dataset else None,
            "width": dataset.width if dataset else None,
            "height": dataset.height if dataset else None,
            "bands": dataset.bands if dataset else None,
            "dtype": dataset.dtype if dataset else None,
            "crs": dataset.crs if dataset else None,
            "vv_stats": _stored_stats(dataset.vv_stats) if dataset else {},
            "vh_stats": _stored_stats(dataset.vh_stats) if dataset else {},
        },
        "result": {
            "flood_pixel_count": result.flood_pixel_count,
            "valid_pixel_count": result.valid_pixel_count,
            "flood_ratio": _finite(result.flood_ratio),
            "mean_flood_confidence": _finite(result.mean_confidence),
            "risk_hint": result.risk_level,
            "inference_seconds": _finite(result.inference_seconds),
            **optional,
        },
        "missing_optional_fields": missing,
        "unavailable_fields": [
            "rainfall", "water_depth", "water_level", "flow_velocity", "population",
            "economic_loss", "administrative_region", "future_forecast", "pixel_area",
        ],
        "data_policy": "仅结构化统计；未包含栅格像元、影像文件或本地文件路径。",
    }


def _result_side(result: InferenceResult) -> dict[str, Any]:
    task = result.task
    return {
        "result_id": result.id,
        "dataset_name": task.dataset.name if task.dataset else None,
        "finished_at": _iso(task.finished_at),
        "model_name": task.model_name,
        "threshold": _finite(task.threshold),
        "flood_ratio": _finite(result.flood_ratio),
        "mean_confidence": _finite(result.mean_confidence),
        "risk_hint": result.risk_level,
        "valid_pixel_count": result.valid_pixel_count,
        "flood_pixel_count": result.flood_pixel_count,
        "probability_mean": _finite(result.probability_mean),
    }


def build_compare_context(db: Session, result_ids: list[str]) -> dict[str, Any]:
    statement = (
        select(InferenceResult)
        .options(joinedload(InferenceResult.task).joinedload(InferenceTask.dataset))
        .where(InferenceResult.id.in_(result_ids))
    )
    found = {row.id: row for row in db.scalars(statement).unique().all()}
    if any(result_id not in found for result_id in result_ids):
        raise AIContextError("识别结果不存在", 404)
    a, b = found[result_ids[0]], found[result_ids[1]]
    if a.task.status != "completed" or b.task.status != "completed":
        raise AIContextError("仅已完成任务的识别结果可进行 AI 对比解读", 409)
    side_a, side_b = _result_side(a), _result_side(b)
    consistent, change, limitation = False, None, None
    try:
        consistent = spatial_consistent(a, b)
        if consistent:
            change = change_statistics(a, b)
        else:
            limitation = "两景空间范围或栅格网格不一致，仅可进行统计对比。"
    except (OSError, ValueError, RuntimeError):
        limitation = "变化分析所需栅格文件不可用，仅可进行数据库统计对比。"
    return {
        "context_type": "historical_result_comparison",
        "a": side_a,
        "b": side_b,
        "differences_b_minus_a": {
            "flood_ratio": side_b["flood_ratio"] - side_a["flood_ratio"],
            "mean_confidence": side_b["mean_confidence"] - side_a["mean_confidence"],
        },
        "spatial_consistent": consistent,
        "pixel_change_statistics": change,
        "limitation": limitation,
        "unavailable_fields": [
            "rainfall", "water_depth", "water_level", "population", "economic_loss", "future_forecast",
        ],
        "data_policy": "仅结构化统计；未包含栅格像元、影像文件或本地文件路径。",
    }


def build_change_analysis_context(db: Session, analysis_id: str) -> dict[str, Any]:
    row = db.get(ChangeAnalysis, analysis_id)
    if not row:
        raise AIContextError("变化分析记录不存在", 404)
    if row.status != "completed":
        raise AIContextError("仅已完成的变化分析可进行 AI 解读", 409)
    return {
        "context_type": "persisted_spatiotemporal_change_analysis",
        "analysis": {
            "id": row.id,
            "name": row.name,
            "algorithm_version": row.algorithm_version,
            "spatial_consistent": row.spatial_consistent,
            "completed_at": _iso(row.completed_at),
        },
        "t1": {
            "dataset_name": row.dataset_t1_name,
            "finished_at": _iso(row.finished_t1_at),
            "model_name": row.model_t1_name,
            "threshold": _finite(row.threshold_t1),
            "water_ratio": _finite(row.t1_water_ratio),
        },
        "t2": {
            "dataset_name": row.dataset_t2_name,
            "finished_at": _iso(row.finished_t2_at),
            "model_name": row.model_t2_name,
            "threshold": _finite(row.threshold_t2),
            "water_ratio": _finite(row.t2_water_ratio),
        },
        "change": {
            "valid_pixels": row.valid_pixels,
            "new_water_pixels": row.new_water_pixels,
            "new_water_ratio": _finite(row.new_water_ratio),
            "persistent_water_pixels": row.persistent_water_pixels,
            "persistent_water_ratio": _finite(row.persistent_water_ratio),
            "receded_water_pixels": row.receded_water_pixels,
            "receded_water_ratio": _finite(row.receded_water_ratio),
            "stable_non_water_pixels": row.stable_non_water_pixels,
            "stable_non_water_ratio": _finite(row.stable_non_water_ratio),
            "net_change_ratio": _finite(row.net_change_ratio),
        },
        "rainfall_background": _rainfall_context(db,row),
        "unavailable_fields": [
            "water_depth", "water_level", "flow_velocity", "pixel_area",
            "causal_explanation", "future_forecast", "on_site_safety_state",
        ],
        "data_policy": "仅使用已持久化的结构化变化统计；未发送影像、栅格像元或本地路径。",
    }


def build_risk_assessment_context(db: Session, assessment_id: str) -> dict[str, Any]:
    row = db.get(RiskAssessment, assessment_id)
    if not row: raise AIContextError("风险研判记录不存在", 404)
    if row.status != "completed": raise AIContextError("仅已完成的风险研判可进行 AI 解读", 409)
    data = serialize_risk(row)
    impact_rows = db.scalars(select(WorkAreaImpact).where(
        WorkAreaImpact.risk_assessment_id == row.id
    ).order_by(WorkAreaImpact.high_attention_ratio.desc())).all()
    areas = {x.id:x for x in db.scalars(select(WorkArea).where(
        WorkArea.id.in_([item.work_area_id for item in impact_rows])
    )).all()} if impact_rows else {}
    project = db.get(ConstructionProject, row.project_id) if row.project_id else None
    return {
        "context_type": "persisted_rule_based_water_risk_assessment",
        "formal_rule_result": {
            "attention_level": data["attention_level"],
            "reason_codes": data["risk_reason_codes"],
            "reason_text": data["risk_reason_text"],
            "inspection_advice": data["inspection_advice"],
            "rule_version": data["rule_version"],
        },
        "source": {"t1": data["source_t1_name"], "t2": data["source_t2_name"],
                   "change_analysis_id": data["source_change_analysis_id"]},
        "statistics": {key: data[key] for key in (
            "current_water_ratio", "new_water_ratio", "persistent_water_ratio",
            "receded_water_ratio", "net_change_ratio",
        )},
        "zone_counts": data["zone_counts"],
        "construction_project": {"id":project.id,"name":project.project_name} if project else None,
        "work_area_impacts": [dict(serialize_impact(item, areas.get(item.work_area_id)), created_at=_iso(item.created_at)) for item in impact_rows],
        "equipment_risk_links": _equipment_links_context(db, row.id),
        "terrain_auxiliary_analysis": _terrain_context(db, row),
        "rainfall_background": _rainfall_context(db,row,True),
        "uav_manual_verification": _uav_context(db,row),
        "operation_assistance_records": [serialize_record(db,item) for item in db.scalars(select(OperationAssistanceRecord).where(OperationAssistanceRecord.risk_assessment_id==row.id).order_by(OperationAssistanceRecord.created_at)).all()],
        "boundary_note": data["boundary_note"],
        "unavailable_fields": ["water_depth", "road_bearing_capacity", "slope_stability", "equipment_gps", "future_rainfall", "uav_ai_detection"],
        "data_policy": "AI 必须区分SAR模型结果、DEM地形结果、同期降雨观测和无人机人工观察。仅解释已持久化的结构化事实，不得自行识别无人机像素、虚构无人机AI模型、修改人工记录、重新分类或改变正式风险等级，也不得判断道路或设备可安全通行。",
    }


def build_assistance_context(db: Session, record_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
    if record_id:
        row=db.get(OperationAssistanceRecord,record_id)
        if not row: raise AIContextError("作业辅助事项不存在",404)
        rows=[row]
    else:
        if not db.get(ConstructionProject,project_id): raise AIContextError("施工项目不存在",404)
        rows=db.scalars(select(OperationAssistanceRecord).where(OperationAssistanceRecord.project_id==project_id).order_by(OperationAssistanceRecord.created_at.desc()).limit(100)).all()
    return {"context_type":"read_only_operation_assistance","rule_version":"operation-assistance-v1","records":[serialize_record(db,row) for row in rows],"data_policy":"只读解释。AI不得创建正式事项、修改优先级、关闭事项、修改人工核查结果，不得输出安全通行许可或决定停复工。"}
