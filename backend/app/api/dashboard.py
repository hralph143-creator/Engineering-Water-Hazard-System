from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..db.database import get_db
from ..config import get_config
from ..db.models import ConstructionEquipment, ConstructionProject, DatasetRecord, EquipmentRiskLink, InferenceResult, InferenceTask, OperationAssistanceRecord, RainfallObservation, RiskAssessment, RiskRainfallContext, TerrainDataset, TerrainFusionAnalysis, UavDataset, UavVerificationRecord, WorkArea, WorkAreaImpact, WorkAreaTerrainProfile
from ..schemas.common import ok
from ..services.model_service import get_model_service
from .helpers import task_to_dict
from ..services.equipment_service import latest_link, latest_position, position_age_hours
from ..services.rainfall_service import project_overview

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    datasets = db.scalar(select(func.count()).select_from(DatasetRecord)) or 0
    tasks = db.scalar(select(func.count()).select_from(InferenceTask)) or 0
    recent = db.scalars(select(InferenceTask).options(joinedload(InferenceTask.dataset), joinedload(InferenceTask.result)).order_by(InferenceTask.started_at.desc()).limit(8)).unique().all()
    # 最新结果 = 最近完成的 InferenceResult（按结果创建时间降序），不受 pending/running/failed 影响
    latest = db.scalar(
        select(InferenceResult)
        .join(InferenceTask, InferenceResult.task_id == InferenceTask.id)
        .where(InferenceTask.status == "completed")
        .order_by(InferenceResult.created_at.desc())
        .limit(1)
    )
    grouped = dict(db.execute(select(InferenceTask.status, func.count()).group_by(InferenceTask.status)).all())
    trend_rows = db.scalars(select(InferenceResult).order_by(InferenceResult.created_at.desc()).limit(20)).all()
    trend = [{"time": r.created_at, "flood_ratio": r.flood_ratio, "risk_level": r.risk_level} for r in reversed(trend_rows)]
    latest_risk = db.scalar(select(RiskAssessment).where(
        RiskAssessment.status == "completed"
    ).order_by(RiskAssessment.completed_at.desc()).limit(1))
    project_count = db.scalar(select(func.count()).select_from(ConstructionProject).where(ConstructionProject.status == "active")) or 0
    area_count = db.scalar(select(func.count()).select_from(WorkArea).where(WorkArea.status == "active")) or 0
    high_impact_count = db.scalar(select(func.count()).select_from(WorkAreaImpact).where(WorkAreaImpact.impact_level == "高水害影响")) or 0
    attention_impact_count = db.scalar(select(func.count()).select_from(WorkAreaImpact).where(WorkAreaImpact.impact_level == "中水害影响")) or 0
    latest_project = db.get(ConstructionProject, latest_risk.project_id) if latest_risk and latest_risk.project_id else None
    evidence_summary = None
    if latest_risk:
        parts = []
        impact = db.scalar(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == latest_risk.id).order_by(WorkAreaImpact.high_attention_ratio.desc()).limit(1))
        if impact:
            area = db.get(WorkArea, impact.work_area_id)
            parts.append(f"{area.area_name if area else '施工区域'}存在{impact.impact_level}，高水害关注区域占比{impact.high_attention_ratio:.1%}")
        if latest_risk.persistent_water_ratio: parts.append(f"持续积水占比{latest_risk.persistent_water_ratio:.1%}")
        rain_context = db.scalar(select(RiskRainfallContext).where(RiskRainfallContext.risk_assessment_id == latest_risk.id))
        if rain_context and rain_context.has_rainfall_data: parts.append("同期存在降雨观测记录")
        if db.scalar(select(UavVerificationRecord.id).where(UavVerificationRecord.risk_assessment_id == latest_risk.id, UavVerificationRecord.verification_status == "REVIEWED").limit(1)): parts.append("无人机已完成局部人工复核")
        equipment_link = db.scalar(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == latest_risk.id).order_by(EquipmentRiskLink.created_at.desc()).limit(1))
        if equipment_link:
            equipment = db.get(ConstructionEquipment, equipment_link.equipment_id)
            if equipment: parts.append(f"关联{equipment.equipment_name}最近位置")
        evidence_summary = "；".join(parts) + "。" if parts else None
    equipment_rows = db.scalars(select(ConstructionEquipment).where(ConstructionEquipment.lifecycle_status == "active")).all()
    positioned_count = stale_count = high_equipment = inspection_equipment = 0
    stale_hours = get_config().equipment_position.position_stale_hours
    for equipment in equipment_rows:
        position = latest_position(db, equipment.id)
        link = latest_link(db, equipment.id)
        if position:
            positioned_count += 1
            if position_age_hours(position.recorded_at) > stale_hours: stale_count += 1
        if link and link.risk_class == "HIGH_ATTENTION_AREA": high_equipment += 1
        elif link and link.risk_class == "INSPECTION_AREA": inspection_equipment += 1
    dem_count = db.scalar(select(func.count()).select_from(TerrainDataset)) or 0
    profiled_area_count = db.scalar(select(func.count(func.distinct(WorkAreaTerrainProfile.work_area_id)))) or 0
    latest_fusion = db.scalar(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.status == "completed").order_by(TerrainFusionAnalysis.created_at.desc()).limit(1))
    latest_rain = db.scalar(select(RainfallObservation).order_by(RainfallObservation.observed_at.desc()).limit(1))
    rain_project = db.get(ConstructionProject, latest_rain.project_id) if latest_rain else None
    rainfall = project_overview(db, latest_rain.project_id) if latest_rain else None
    projects_with_rainfall = db.scalar(select(func.count(func.distinct(RainfallObservation.project_id)))) or 0
    if rainfall:
        rainfall["project_name"] = rain_project.project_name if rain_project else None
        rainfall["projects_with_data"] = projects_with_rainfall
    uav_count = db.scalar(select(func.count()).select_from(UavDataset).where(UavDataset.status != "archived")) or 0
    latest_uav = db.scalar(select(UavDataset).where(UavDataset.status != "archived", UavDataset.capture_time.is_not(None)).order_by(UavDataset.capture_time.desc()).limit(1))
    uav_project = db.get(ConstructionProject, latest_uav.project_id) if latest_uav else None
    uav_reviewed = db.scalar(select(func.count()).select_from(UavVerificationRecord).where(UavVerificationRecord.verification_status == "REVIEWED")) or 0
    assistance_counts = dict(db.execute(select(OperationAssistanceRecord.status, func.count()).group_by(OperationAssistanceRecord.status)).all())
    assistance_priority = db.scalar(select(func.count()).select_from(OperationAssistanceRecord).where(OperationAssistanceRecord.priority_level == "URGENT", OperationAssistanceRecord.status.in_(("PENDING", "IN_PROGRESS")))) or 0
    return ok({
        "dataset_count": datasets, "task_count": tasks,
        "latest_result": task_to_dict(latest.task) if latest else None,
        "latest_flood_ratio": latest.flood_ratio if latest else None,
        "current_risk_level": latest.risk_level if latest else None,
        "model": get_model_service().status(), "recent_tasks": [task_to_dict(r) for r in recent],
        "status_counts": grouped, "trend": trend,
        "latest_risk_assessment": {
            "id": latest_risk.id, "assessment_name": latest_risk.assessment_name,
            "attention_level": latest_risk.attention_level,
            "completed_at": latest_risk.completed_at,
            "high_attention_pixels": latest_risk.high_attention_pixels,
            "project_name": latest_project.project_name if latest_project else None,
            "evidence_summary": evidence_summary,
        } if latest_risk else None,
        "construction": {"active_projects": project_count, "active_work_areas": area_count,
                         "high_impact_areas": high_impact_count, "attention_impact_areas": attention_impact_count},
        "equipment": {"active_count": len(equipment_rows), "positioned_count": positioned_count,
                      "high_attention_count": high_equipment, "inspection_count": inspection_equipment,
                      "stale_position_count": stale_count},
        "terrain": {"dem_count": dem_count, "profiled_area_count": profiled_area_count,
                    "latest_high_attention_low_elevation_ratio": latest_fusion.high_attention_low_elevation_ratio if latest_fusion else None},
        "rainfall": rainfall,
        "uav": {"dataset_count":uav_count,"latest_capture_time":latest_uav.capture_time if latest_uav else None,
                "latest_project_name":uav_project.project_name if uav_project else None,"reviewed_count":uav_reviewed},
        "assistance": {"pending_count":assistance_counts.get("PENDING",0)+assistance_counts.get("IN_PROGRESS",0),
                       "priority_count":assistance_priority,"closed_count":assistance_counts.get("CLOSED",0)},
    })
