from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..db.models import (
    AssistanceVerification, ChangeAnalysis, ConstructionEquipment, ConstructionProject,
    DatasetRecord, EquipmentRiskLink, EquipmentTerrainSnapshot, InferenceResult,
    InferenceTask, OperationAssistanceRecord, RiskAssessment, RiskRainfallContext,
    TerrainFusionAnalysis, UavRiskOverlay, UavVerificationRecord, WorkArea,
    WorkAreaImpact, WorkAreaTerrainProfile,
)

RULE_VERSION = "operation-assistance-v1"
OPEN_STATUSES = ("PENDING", "IN_PROGRESS")
TYPE_LABELS = {"AREA_INSPECTION":"区域巡检", "EQUIPMENT_ENV_CHECK":"设备周边环境核查", "PASSAGE_CONDITION_CHECK":"通行条件核验", "RESUME_WORK_CHECK":"复工作业核验", "EMERGENCY_COORDINATION":"应急协同", "OTHER":"其他"}
PRIORITY_LABELS = {"NORMAL":"一般", "IMPORTANT":"重点", "URGENT":"优先"}
STATUS_LABELS = {"PENDING":"待核查", "IN_PROGRESS":"核查中", "VERIFIED":"已核验", "CLOSED":"已闭环", "CANCELLED":"已取消"}
RESULT_LABELS = {"ISSUE_CONFIRMED":"问题已确认", "NO_OBVIOUS_ISSUE":"未发现明显异常", "NEEDS_FURTHER_CHECK":"需要进一步核查", "CONDITION_IMPROVED":"现场条件已有改善", "UNABLE_TO_VERIFY":"暂无法核验"}

CHECKLISTS = {
    "AREA_INSPECTION": ["是否存在明显积水", "是否存在严重泥泞", "排水是否通畅", "周边是否仍有持续来水", "实际机械作业条件是否已现场确认"],
    "EQUIPMENT_ENV_CHECK": ["设备实际当前位置是否已确认", "设备周边是否存在积水或泥泞", "周边排水是否通畅", "实际机械作业条件是否已现场确认"],
    "PASSAGE_CONDITION_CHECK": ["是否存在明显积水", "是否存在严重泥泞", "排水是否通畅", "实际通行条件是否已现场确认", "周边是否仍有持续来水"],
    "RESUME_WORK_CHECK": ["地面情况是否已现场核查", "泥泞及承载情况是否已核查", "排水情况是否已核查", "实际机械作业条件是否已确认"],
    "EMERGENCY_COORDINATION": ["重点区域现场情况是否已汇总", "相关设备实际位置是否已确认", "现场核查责任人及时间是否已协调", "后续复核需求是否已记录"],
    "OTHER": ["事项涉及的现场条件是否已核查", "核查结论和后续动作是否已记录"],
}


class AssistanceError(ValueError):
    pass


def _loads(value, fallback):
    try: return json.loads(value or "")
    except (TypeError, ValueError): return fallback


def _iso(value): return value.isoformat() if value else None


def _snapshot(model, fields):
    if not model: return None
    result = {name: getattr(model, name, None) for name in fields}
    return {key: (_iso(value) if isinstance(value, datetime) else value) for key, value in result.items()}


def build_evidence(db: Session, risk: RiskAssessment | None, impact: WorkAreaImpact | None = None,
                   link: EquipmentRiskLink | None = None, area: WorkArea | None = None) -> dict:
    evidence: dict = {}
    if risk:
        evidence["risk_assessment"] = _snapshot(risk, ["id","assessment_name","attention_level","rule_version","completed_at","current_water_ratio","new_water_ratio","persistent_water_ratio","receded_water_ratio","net_change_ratio"])
        if risk.source_change_analysis_id:
            change = db.get(ChangeAnalysis, risk.source_change_analysis_id)
            if change: evidence["change_analysis"] = _snapshot(change, ["id","name","algorithm_version","new_water_ratio","persistent_water_ratio","receded_water_ratio","net_change_ratio","completed_at"])
    if impact: evidence["work_area_impact"] = _snapshot(impact, ["id","work_area_id","impact_level","high_attention_ratio","inspection_ratio","affected_ratio","algorithm_version","created_at"])
    if link: evidence["equipment_risk_link"] = _snapshot(link, ["id","equipment_id","position_id","risk_class","risk_label","position_status","position_age_hours","recorded_at","primary_work_area_id","work_area_impact_level","rule_version"])
    if risk:
        fusion = db.scalar(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id == risk.id).order_by(TerrainFusionAnalysis.created_at.desc()).limit(1))
        terrain = {}
        if fusion: terrain["fusion"] = _snapshot(fusion, ["id","dem_id","status","high_attention_low_elevation_ratio","inspection_low_elevation_ratio","algorithm_version","created_at"])
        if area:
            profile = db.scalar(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.work_area_id == area.id).order_by(WorkAreaTerrainProfile.created_at.desc()).limit(1))
            if profile: terrain["work_area_profile"] = _snapshot(profile, ["id","dem_id","low_elevation_ratio","mean_elevation","mean_slope","terrain_rule_version","created_at"])
        if link:
            snap = db.scalar(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.position_id == link.position_id).order_by(EquipmentTerrainSnapshot.created_at.desc()).limit(1))
            if snap: terrain["equipment_snapshot"] = _snapshot(snap, ["id","dem_id","elevation","slope","is_low_elevation","algorithm_version","created_at"])
        if terrain: evidence["terrain"] = terrain
        rainfall = db.scalar(select(RiskRainfallContext).where(RiskRainfallContext.risk_assessment_id == risk.id))
        if rainfall: evidence["rainfall"] = _snapshot(rainfall, ["id","total_rainfall_mm","rainfall_event_count","observation_count","station_count","peak_rainfall_rate_mm_h","coverage_status","rule_version","window_start","window_end"])
        overlays = db.scalars(select(UavRiskOverlay).where(UavRiskOverlay.risk_assessment_id == risk.id).order_by(UavRiskOverlay.created_at)).all()
        checks = db.scalars(select(UavVerificationRecord).where(UavVerificationRecord.risk_assessment_id == risk.id).order_by(UavVerificationRecord.created_at)).all()
        if overlays or checks:
            evidence["uav"] = {
                "overlays": [_snapshot(x,["id","uav_dataset_id","high_attention_coverage_ratio","inspection_coverage_ratio","time_reference_status","analysis_version","created_at"]) for x in overlays],
                "manual_verifications": [_snapshot(x,["id","uav_dataset_id","work_area_id","verification_status","observation_type","finding_text","verified_at","structure_version"]) for x in checks],
            }
    return evidence


def _existing(db, risk_id, area_id, equipment_id, assistance_type):
    return db.scalar(select(OperationAssistanceRecord).where(
        OperationAssistanceRecord.risk_assessment_id == risk_id,
        OperationAssistanceRecord.work_area_id.is_(None) if area_id is None else OperationAssistanceRecord.work_area_id == area_id,
        OperationAssistanceRecord.equipment_id.is_(None) if equipment_id is None else OperationAssistanceRecord.equipment_id == equipment_id,
        OperationAssistanceRecord.assistance_type == assistance_type,
        OperationAssistanceRecord.status.in_(OPEN_STATUSES),
    ).order_by(OperationAssistanceRecord.created_at.desc()).limit(1))


def _create_rule(db, risk, kind, title, priority, summary, recommendation, reasons, area=None, equipment=None, impact=None, link=None):
    existing = _existing(db, risk.id, area.id if area else None, equipment.id if equipment else None, kind)
    if existing: return existing, False
    row = OperationAssistanceRecord(
        id=str(uuid.uuid4()), project_id=risk.project_id, risk_assessment_id=risk.id,
        work_area_id=area.id if area else None, equipment_id=equipment.id if equipment else None,
        assistance_type=kind, title=title, priority_level=priority, status="PENDING", source_type="rule",
        source_summary=summary, evidence_json=json.dumps(build_evidence(db,risk,impact,link,area),ensure_ascii=False),
        reason_codes=json.dumps(reasons,ensure_ascii=False), recommendation_text=recommendation,
        inspection_checklist=json.dumps(CHECKLISTS[kind],ensure_ascii=False), rule_version=RULE_VERSION,
    )
    db.add(row); db.flush(); return row, True


def generate_for_risk(db: Session, risk: RiskAssessment, include_resume_checks: bool = True) -> tuple[list, list]:
    if risk.status != "completed": raise AssistanceError("仅已完成的风险研判可以生成作业辅助事项")
    created, existing = [], []
    impacts = db.scalars(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == risk.id)).all()
    links = db.scalars(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == risk.id)).all()
    for impact in impacts:
        area = db.get(WorkArea, impact.work_area_id)
        if not area: continue
        if impact.impact_level == "高水害影响":
            item, fresh = _create_rule(db,risk,"AREA_INSPECTION",f"{area.area_name}水害现场核查","URGENT" if impact.high_attention_ratio >= .1 else "IMPORTANT",f"该区域与高水害关注区域空间重叠，高关注占比{impact.high_attention_ratio:.2%}。","建议优先现场核查该区域积水、泥泞、排水及实际施工条件。",["HIGH_WORK_AREA_IMPACT"],area=area,impact=impact)
            (created if fresh else existing).append(item)
        if area.area_type == "construction_road" and impact.impact_level in {"中水害影响","高水害影响"}:
            item, fresh = _create_rule(db,risk,"PASSAGE_CONDITION_CHECK",f"{area.area_name}通行条件现场核验","URGENT" if impact.impact_level.startswith("高") else "IMPORTANT",f"施工道路区域存在{impact.impact_level}。","建议现场核实施工道路积水、泥泞、排水及实际通行条件。",["ROAD_WATER_IMPACT"],area=area,impact=impact)
            (created if fresh else existing).append(item)
    for link in links:
        if link.risk_class != "HIGH_ATTENTION_AREA": continue
        equipment=db.get(ConstructionEquipment,link.equipment_id); area=db.get(WorkArea,link.primary_work_area_id) if link.primary_work_area_id else None
        if not equipment: continue
        stale = link.position_status == "stale"
        summary = "设备最近位置与高水害关注区域重叠。" + ("设备位置已过期，请先确认设备实际当前位置。" if stale else "")
        item, fresh = _create_rule(db,risk,"EQUIPMENT_ENV_CHECK",f"核查{equipment.equipment_name}最近位置周边水害环境","URGENT" if not stale else "IMPORTANT",summary,"建议确认设备实际位置，并核查周边积水、泥泞、排水及实际作业条件。",["EQUIPMENT_IN_HIGH_ATTENTION"] + (["POSITION_STALE"] if stale else []),area=area,equipment=equipment,link=link)
        (created if fresh else existing).append(item)
    if include_resume_checks and ((risk.receded_water_ratio or 0) > 0 or db.scalar(select(UavVerificationRecord.id).where(UavVerificationRecord.risk_assessment_id==risk.id,UavVerificationRecord.observation_type=="NO_OBVIOUS_WATER").limit(1))):
        reasons=["RECEDED_WATER"] if (risk.receded_water_ratio or 0)>0 else ["UAV_NO_OBVIOUS_WATER"]
        item,fresh=_create_rule(db,risk,"RESUME_WORK_CHECK","积水消退后作业条件现场核验","IMPORTANT","识别结果存在积水消退，或无人机人工复核未见明显积水。","积水消退或影像未见明显积水，不代表地面承载、泥泞程度及其他施工条件已经恢复；建议核查地面、排水及实际机械作业条件。",reasons)
        (created if fresh else existing).append(item)
    affected=sum(1 for x in impacts if x.impact_level in {"中水害影响","高水害影响"}); high_equipment=sum(1 for x in links if x.risk_class=="HIGH_ATTENTION_AREA")
    if (risk.new_water_ratio or 0) >= .05 and affected >= 2 and high_equipment >= 2:
        item,fresh=_create_rule(db,risk,"EMERGENCY_COORDINATION","项目重点区域及相关设备协同核查","URGENT",f"新增积水占比{risk.new_water_ratio:.2%}，{affected}个施工区域受影响，{high_equipment}台设备最近位置关联高水害关注区域。","建议项目管理人员统一核查重点区域及相关设备情况。",["SIGNIFICANT_NEW_WATER","MULTIPLE_AREAS_AFFECTED","MULTIPLE_EQUIPMENT_LINKED"])
        (created if fresh else existing).append(item)
    db.commit()
    return created, existing


def create_manual(db: Session, values: dict) -> OperationAssistanceRecord:
    values = dict(values)
    project = db.get(ConstructionProject, values.get("project_id")) if values.get("project_id") else None
    risk = db.get(RiskAssessment, values.get("risk_assessment_id")) if values.get("risk_assessment_id") else None
    area = db.get(WorkArea, values.get("work_area_id")) if values.get("work_area_id") else None
    equipment = db.get(ConstructionEquipment, values.get("equipment_id")) if values.get("equipment_id") else None
    for value,label in ((values.get("project_id"),project),(values.get("risk_assessment_id"),risk),(values.get("work_area_id"),area),(values.get("equipment_id"),equipment)):
        if value and not label: raise AssistanceError("关联对象不存在")
    project_id=values.get("project_id") or (risk.project_id if risk else None) or (area.project_id if area else None) or (equipment.project_id if equipment else None)
    if any(x and x.project_id != project_id for x in (risk,area,equipment)): raise AssistanceError("关联对象必须属于同一项目")
    checklist=values.pop("inspection_checklist",[]) or CHECKLISTS[values["assistance_type"]]
    values["project_id"] = project_id
    row=OperationAssistanceRecord(id=str(uuid.uuid4()),status="PENDING",source_type="manual",evidence_json=json.dumps(build_evidence(db,risk,area=area),ensure_ascii=False),reason_codes="[]",inspection_checklist=json.dumps(checklist,ensure_ascii=False),rule_version=RULE_VERSION,**values)
    db.add(row); db.commit(); return row


def add_verification(db: Session, row: OperationAssistanceRecord, values: dict) -> AssistanceVerification:
    values["verified_at"] = values.get("verified_at") or datetime.now(timezone.utc)
    values["checklist_json"] = json.dumps(values.pop("checklist",[]),ensure_ascii=False)
    verification=AssistanceVerification(id=str(uuid.uuid4()),assistance_record_id=row.id,**values)
    db.add(verification); row.status="VERIFIED"; db.commit(); return verification


def close_record(db: Session, row: OperationAssistanceRecord, note: str):
    if row.status != "VERIFIED": raise AssistanceError("只有已完成人工核验的事项可以闭环")
    if not db.scalar(select(AssistanceVerification.id).where(AssistanceVerification.assistance_record_id==row.id).limit(1)): raise AssistanceError("缺少人工核查记录，不能闭环")
    row.status="CLOSED"; row.closed_at=datetime.now(timezone.utc); row.closed_by_note=note; db.commit(); return row


def update_status(db: Session, row: OperationAssistanceRecord, status: str):
    allowed={"PENDING":{"IN_PROGRESS","CANCELLED"},"IN_PROGRESS":{"CANCELLED"},"VERIFIED":{"IN_PROGRESS"},"CLOSED":set(),"CANCELLED":set()}
    if status == row.status: return row
    if status in {"VERIFIED","CLOSED"} or status not in allowed.get(row.status,set()): raise AssistanceError("该事项状态不能按此方式变更")
    row.status=status; db.commit(); return row


def serialize_verification(row):
    return {"id":row.id,"assistance_record_id":row.assistance_record_id,"verification_result":row.verification_result,"verification_result_label":RESULT_LABELS.get(row.verification_result,row.verification_result),"finding_text":row.finding_text,"action_note":row.action_note,"checklist":_loads(row.checklist_json,[]),"attachment_note":row.attachment_note,"verified_at":row.verified_at,"created_at":row.created_at,"updated_at":row.updated_at}


def serialize_record(db: Session, row: OperationAssistanceRecord, include_verifications=True):
    project=db.get(ConstructionProject,row.project_id) if row.project_id else None; area=db.get(WorkArea,row.work_area_id) if row.work_area_id else None; equipment=db.get(ConstructionEquipment,row.equipment_id) if row.equipment_id else None
    data={"id":row.id,"project_id":row.project_id,"project_name":project.project_name if project else None,"risk_assessment_id":row.risk_assessment_id,"work_area_id":row.work_area_id,"work_area_name":area.area_name if area else None,"equipment_id":row.equipment_id,"equipment_name":equipment.equipment_name if equipment else None,"equipment_code":equipment.equipment_code if equipment else None,"assistance_type":row.assistance_type,"assistance_type_label":TYPE_LABELS.get(row.assistance_type,row.assistance_type),"title":row.title,"priority_level":row.priority_level,"priority_label":PRIORITY_LABELS.get(row.priority_level,row.priority_level),"status":row.status,"status_label":STATUS_LABELS.get(row.status,row.status),"source_type":row.source_type,"source_label":"系统规则生成" if row.source_type=="rule" else "人工创建","source_summary":row.source_summary,"evidence":_loads(row.evidence_json,{}),"reason_codes":_loads(row.reason_codes,[]),"recommendation_text":row.recommendation_text,"inspection_checklist":_loads(row.inspection_checklist,[]),"created_at":row.created_at,"updated_at":row.updated_at,"closed_at":row.closed_at,"closed_by_note":row.closed_by_note,"rule_version":row.rule_version}
    if include_verifications:
        checks=db.scalars(select(AssistanceVerification).where(AssistanceVerification.assistance_record_id==row.id).order_by(AssistanceVerification.verified_at.desc())).all(); data["verifications"]=[serialize_verification(x) for x in checks]
    return data


def trace_chain(db: Session, row: OperationAssistanceRecord) -> dict:
    risk=db.get(RiskAssessment,row.risk_assessment_id) if row.risk_assessment_id else None; change=db.get(ChangeAnalysis,risk.source_change_analysis_id) if risk and risk.source_change_analysis_id else None
    result_ids=[x for x in ([change.result_t1_id,change.result_t2_id] if change else ([risk.source_result_id] if risk else [])) if x]
    results=db.scalars(select(InferenceResult).where(InferenceResult.id.in_(result_ids))).all() if result_ids else []
    tasks=db.scalars(select(InferenceTask).where(InferenceTask.id.in_([x.task_id for x in results]))).all() if results else []
    datasets=db.scalars(select(DatasetRecord).where(DatasetRecord.id.in_([x.dataset_id for x in tasks]))).all() if tasks else []
    return {"assistance_record_id":row.id,"risk_assessment_id":risk.id if risk else None,"change_analysis_id":change.id if change else None,"inference_result_ids":[x.id for x in results],"inference_task_ids":[x.id for x in tasks],"dataset_ids":[x.id for x in datasets],"work_area_id":row.work_area_id,"equipment_id":row.equipment_id,"evidence":_loads(row.evidence_json,{})}
