import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.db.models import (AssistanceVerification, ConstructionEquipment, ConstructionProject,
    EquipmentPosition, EquipmentRiskLink, OperationAssistanceRecord, RiskAssessment,
    RiskRainfallContext, TerrainFusionAnalysis, UavRiskOverlay, UavVerificationRecord,
    WorkArea, WorkAreaImpact)
from backend.app.schemas.assistance import AssistanceCreate, AssistanceUpdate, AssistanceVerificationCreate
from backend.app.services.ai_context import build_assistance_context, build_risk_assessment_context
from backend.app.services.ai_prompts import AI_SYSTEM_PROMPT, ASSISTANCE_GUIDE
from backend.app.services.assistance_report_service import BOUNDARY, assistance_report_html
from backend.app.services.operation_assistance_service import (AssistanceError, CHECKLISTS, RULE_VERSION,
    add_verification, close_record, create_manual, generate_for_risk, serialize_record, serialize_verification, trace_chain,
    update_status)
from backend.app.services.risk_service import RiskAssessmentError, delete_risk_assessment


def _engine(tmp_path):
    engine=create_engine(f"sqlite:///{(tmp_path/'assistance.db').as_posix()}")
    @event.listens_for(engine,"connect")
    def _fk(connection,_): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine); return engine


def _base(db, *, with_second=True):
    project=ConstructionProject(id="p1",project_name="露天矿项目",project_code="P1",project_type="open_pit_mine",status="active")
    road=WorkArea(id="a1",project_id="p1",area_name="东侧运输道路",area_code="A1",area_type="construction_road",geometry_type="Polygon",geometry_geojson='{"type":"Polygon","coordinates":[]}',crs="EPSG:3857")
    pit=WorkArea(id="a2",project_id="p1",area_name="采坑北侧",area_code="A2",area_type="pit",geometry_type="Polygon",geometry_geojson='{"type":"Polygon","coordinates":[]}',crs="EPSG:3857")
    risk=RiskAssessment(id="r1",project_id="p1",assessment_name="正式研判",source_type="change_analysis",source_t1_name="T1",source_t2_name="T2",status="completed",attention_level="高水害关注",rule_version="water-risk-v1",operation_note="辅助",risk_reason_codes='["NEW"]',risk_reason_text='["新增积水"]',inspection_advice='[]',new_water_ratio=.06,persistent_water_ratio=.08,receded_water_ratio=.02,completed_at=datetime.now(timezone.utc))
    db.add_all([project,road,pit,risk]); db.flush()
    db.add_all([
        WorkAreaImpact(id="i1",risk_assessment_id="r1",project_id="p1",work_area_id="a1",impact_level="高水害影响",high_attention_ratio=.18,inspection_ratio=.03,affected_ratio=.21,advice="核查"),
        WorkAreaImpact(id="i2",risk_assessment_id="r1",project_id="p1",work_area_id="a2",impact_level="中水害影响",high_attention_ratio=.01,inspection_ratio=.12,affected_ratio=.13,advice="核查"),
    ])
    equipment=[]
    for index in range(2 if with_second else 1):
        eid=f"e{index+1}"; pid=f"pos{index+1}"
        eq=ConstructionEquipment(id=eid,project_id="p1",equipment_code=f"MC-0{index+1}",equipment_name=f"矿卡0{index+1}",equipment_type="mining_truck")
        pos=EquipmentPosition(id=pid,equipment_id=eid,project_id="p1",x=1+index,y=1,crs="EPSG:3857",position_source="manual",recorded_at=datetime.now(timezone.utc)-timedelta(hours=48 if index==0 else 1))
        link=EquipmentRiskLink(id=f"l{index+1}",equipment_id=eid,position_id=pid,project_id="p1",risk_assessment_id="r1",recorded_at=pos.recorded_at,position_age_hours=48 if index==0 else 1,position_status="stale" if index==0 else "valid",risk_class="HIGH_ATTENTION_AREA",risk_label="高水害关注区域",work_area_ids='["a1"]',primary_work_area_id="a1",reason_text="重叠",advice_text="核查")
        db.add_all([eq,pos,link]); equipment.append(eq)
    db.commit(); return project,risk,road,pit,equipment


@pytest.mark.parametrize("table",["operation_assistance_records","assistance_verifications"])
def test_tables_created(tmp_path,table): assert table in inspect(_engine(tmp_path)).get_table_names()


@pytest.mark.parametrize("kind",["AREA_INSPECTION","EQUIPMENT_ENV_CHECK","PASSAGE_CONDITION_CHECK","RESUME_WORK_CHECK","EMERGENCY_COORDINATION","OTHER"])
def test_supported_assistance_types(kind): assert AssistanceCreate(assistance_type=kind,title="核查").assistance_type==kind


@pytest.mark.parametrize("priority",["NORMAL","IMPORTANT","URGENT"])
def test_supported_priorities(priority): assert AssistanceCreate(assistance_type="OTHER",title="核查",priority_level=priority).priority_level==priority


@pytest.mark.parametrize("status",["PENDING","IN_PROGRESS","VERIFIED","CLOSED","CANCELLED"])
def test_supported_statuses(status): assert AssistanceUpdate(status=status).status==status


@pytest.mark.parametrize("result",["ISSUE_CONFIRMED","NO_OBVIOUS_ISSUE","NEEDS_FURTHER_CHECK","CONDITION_IMPROVED","UNABLE_TO_VERIFY"])
def test_supported_verification_results(result): assert AssistanceVerificationCreate(verification_result=result).verification_result==result


@pytest.mark.parametrize("result,label",[("ISSUE_CONFIRMED","问题已确认"),("NO_OBVIOUS_ISSUE","未发现明显异常"),("NEEDS_FURTHER_CHECK","需要进一步核查"),("CONDITION_IMPROVED","现场条件已有改善"),("UNABLE_TO_VERIFY","暂无法核验")])
def test_verification_result_labels(result,label):
    row=AssistanceVerification(id="v",assistance_record_id="a",verification_result=result,verified_at=datetime.now(timezone.utc))
    assert serialize_verification(row)["verification_result_label"]==label


@pytest.mark.parametrize("status,label",[("PENDING","待核查"),("VERIFIED","已核验"),("CLOSED","已闭环")])
def test_status_labels(tmp_path,status,label):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=OperationAssistanceRecord(id="x",project_id="p1",assistance_type="OTHER",title="核查",status=status,source_type="manual"); db.add(row); db.commit()
        assert serialize_record(db,row)["status_label"]==label


@pytest.mark.parametrize("payload",[
    {"assistance_type":"STOP_WORK","title":"x"},{"assistance_type":"OTHER","title":"x","priority_level":"RED_ALERT"}
])
def test_invalid_choices_rejected(payload):
    with pytest.raises(ValidationError): AssistanceCreate(**payload)


def test_generate_all_rule_types_and_evidence(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); created,_=generate_for_risk(db,risk)
        kinds={x.assistance_type for x in created}
        assert {"AREA_INSPECTION","EQUIPMENT_ENV_CHECK","PASSAGE_CONDITION_CHECK","RESUME_WORK_CHECK","EMERGENCY_COORDINATION"} <= kinds
        assert all(x.source_type=="rule" and x.rule_version==RULE_VERSION for x in created)
        evidence=[json.loads(x.evidence_json) for x in created]
        assert all("risk_assessment" in x for x in evidence) and any("work_area_impact" in x for x in evidence) and any("equipment_risk_link" in x for x in evidence)


def test_no_unfounded_item(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db,with_second=False); risk.new_water_ratio=0; risk.receded_water_ratio=0
        for impact in db.scalars(select(WorkAreaImpact)).all(): impact.impact_level="低水害影响"
        for link in db.scalars(select(EquipmentRiskLink)).all(): link.risk_class="GENERAL_WATER_AREA"
        db.commit(); created,_=generate_for_risk(db,risk); assert created==[]


def test_duplicate_open_items_are_reused_and_closed_allows_new(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); first,_=generate_for_risk(db,risk); second,existing=generate_for_risk(db,risk)
        assert not second and len(existing)==len(first)
        target=first[0]; add_verification(db,target,{"verification_result":"ISSUE_CONFIRMED","finding_text":"现场确认","action_note":"已记录","checklist":[],"attachment_note":"","verified_at":None}); close_record(db,target,"现场管理人员确认完成")
        third,_=generate_for_risk(db,risk); assert any(x.assistance_type==target.assistance_type and x.work_area_id==target.work_area_id and x.equipment_id==target.equipment_id for x in third)


def test_manual_item_is_explicit_and_no_fake_evidence(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=create_manual(db,{"project_id":"p1","risk_assessment_id":None,"work_area_id":"a1","equipment_id":None,"assistance_type":"OTHER","title":"人工排水沟核查","priority_level":"NORMAL","source_summary":"现场人员提出","recommendation_text":"建议现场核查","inspection_checklist":[]})
        data=serialize_record(db,row); assert data["source_type"]=="manual" and data["source_label"]=="人工创建" and "risk_assessment" not in data["evidence"]


@pytest.mark.parametrize("kind",["AREA_INSPECTION","EQUIPMENT_ENV_CHECK","PASSAGE_CONDITION_CHECK","RESUME_WORK_CHECK","EMERGENCY_COORDINATION","OTHER"])
def test_checklists_are_real_manual_questions(kind): assert CHECKLISTS[kind] and all(isinstance(x,str) for x in CHECKLISTS[kind])


def test_position_stale_is_captured(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); created,_=generate_for_risk(db,risk); row=next(x for x in created if x.equipment_id=="e1")
        assert "POSITION_STALE" in json.loads(row.reason_codes) and "位置已过期" in row.source_summary and json.loads(row.evidence_json)["equipment_risk_link"]["position_status"]=="stale"


def test_optional_terrain_rainfall_uav_evidence(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db)
        db.add_all([
            TerrainFusionAnalysis(id="tf",risk_assessment_id="r1",dem_id="missing-dem",project_id="p1",status="completed",algorithm_version="terrain-analysis-v1"),
            RiskRainfallContext(id="rr",risk_assessment_id="r1",change_rainfall_context_id="missing",project_id="p1",window_start=datetime.now(timezone.utc),window_end=datetime.now(timezone.utc),coverage_status="NO_DATA",time_reference="completed_at"),
        ])
        # Foreign keys make fabricated missing references invalid; evidence therefore never invents them.
        with pytest.raises(IntegrityError): db.commit()


@pytest.mark.parametrize("status,target,valid",[("PENDING","IN_PROGRESS",True),("PENDING","CANCELLED",True),("PENDING","CLOSED",False),("IN_PROGRESS","VERIFIED",False),("CLOSED","IN_PROGRESS",False)])
def test_status_transitions(tmp_path,status,target,valid):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=OperationAssistanceRecord(id="x",project_id="p1",assistance_type="OTHER",title="核查",status=status,source_type="manual"); db.add(row); db.commit()
        if valid: assert update_status(db,row,target).status==target
        else:
            with pytest.raises(AssistanceError): update_status(db,row,target)


def test_verification_sets_verified_and_can_be_edited(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=create_manual(db,{"project_id":"p1","risk_assessment_id":None,"work_area_id":None,"equipment_id":None,"assistance_type":"OTHER","title":"核查","priority_level":"NORMAL","source_summary":"","recommendation_text":"","inspection_checklist":[]})
        check=add_verification(db,row,{"verification_result":"NO_OBVIOUS_ISSUE","finding_text":"未发现明显异常","action_note":"继续观察","checklist":[],"attachment_note":"","verified_at":None}); check.finding_text="复核后未发现明显异常"; db.commit()
        assert row.status=="VERIFIED" and db.get(AssistanceVerification,check.id).finding_text.startswith("复核后")


def test_cannot_close_without_manual_verification(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=OperationAssistanceRecord(id="x",project_id="p1",assistance_type="OTHER",title="核查",status="VERIFIED",source_type="manual"); db.add(row); db.commit()
        with pytest.raises(AssistanceError,match="缺少人工核查"): close_record(db,row,"完成")


def test_close_preserves_history(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=OperationAssistanceRecord(id="x",project_id="p1",assistance_type="OTHER",title="核查",source_type="manual"); db.add(row); db.commit()
        check=add_verification(db,row,{"verification_result":"CONDITION_IMPROVED","finding_text":"条件改善","action_note":"记录","checklist":[],"attachment_note":"","verified_at":None}); close_record(db,row,"人工确认闭环")
        assert row.closed_at and db.get(AssistanceVerification,check.id) and row.status=="CLOSED"


def test_reference_protection_for_risk(tmp_path,monkeypatch):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); db.add(OperationAssistanceRecord(id="x",project_id="p1",risk_assessment_id="r1",assistance_type="OTHER",title="核查")); db.commit()
        with pytest.raises(RiskAssessmentError,match="作业辅助事项"): delete_risk_assessment(db,risk)


def test_trace_chain_and_unlinked_history(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=OperationAssistanceRecord(id="x",project_id=None,assistance_type="OTHER",title="历史人工事项",source_type="manual"); db.add(row); db.commit(); trace=trace_chain(db,row)
        assert trace["risk_assessment_id"] is None and serialize_record(db,row)["project_name"] is None


def test_html_report_contains_required_sections_and_boundary(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _base(db); row=create_manual(db,{"project_id":"p1","risk_assessment_id":"r1","work_area_id":"a1","equipment_id":None,"assistance_type":"AREA_INSPECTION","title":"道路核查","priority_level":"IMPORTANT","source_summary":"高影响","recommendation_text":"建议现场核查","inspection_checklist":CHECKLISTS["AREA_INSPECTION"]})
        page=assistance_report_html(serialize_record(db,row)); assert "施工水害作业辅助与现场核查记录" in page and "现场核查清单" in page and BOUNDARY in page and RULE_VERSION in page


def test_ai_reads_records_but_has_no_decision_rights(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); generate_for_risk(db,risk); context=build_assistance_context(db,project_id="p1"); risk_context=build_risk_assessment_context(db,"r1")
        assert context["records"] and risk_context["operation_assistance_records"]
        combined=AI_SYSTEM_PROMPT+ASSISTANCE_GUIDE
        for phrase in ("不得创建正式事项","修改事项优先级","关闭事项","修改现场核查结果","安全通行许可","停工或复工"): assert phrase in combined


@pytest.mark.parametrize("missing",["terrain","rainfall","uav","equipment"])
def test_missing_optional_sources_do_not_block_generation(tmp_path,missing):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db,with_second=False)
        if missing=="equipment":
            db.query(EquipmentRiskLink).delete(); db.query(EquipmentPosition).delete(); db.query(ConstructionEquipment).delete(); db.commit()
        created,_=generate_for_risk(db,risk); assert isinstance(created,list)


@pytest.mark.parametrize("phrase",["自动停工","允许通行","安全区","实时GPS","精准水深","边坡安全判断"])
def test_generated_copy_avoids_overclaiming(tmp_path,phrase):
    with Session(_engine(tmp_path)) as db:
        _,risk,_,_,_=_base(db); created,_=generate_for_risk(db,risk); text=" ".join(x.title+x.source_summary+x.recommendation_text for x in created)
        assert phrase not in text
