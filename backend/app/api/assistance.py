import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import AssistanceVerification, OperationAssistanceRecord, RiskAssessment
from ..schemas.assistance import (AssistanceCreate, AssistanceUpdate, AssistanceVerificationCreate,
    AssistanceVerificationUpdate, CloseAssistanceRequest, GenerateAssistanceRequest)
from ..schemas.common import ok
from ..services.assistance_report_service import assistance_report_html, assistance_report_pdf
from ..services.operation_assistance_service import (AssistanceError, add_verification, close_record,
    create_manual, generate_for_risk, serialize_record, serialize_verification, trace_chain, update_status)

router = APIRouter(tags=["operation-assistance"])


def _record(record_id: str, db: Session):
    row=db.get(OperationAssistanceRecord,record_id)
    if not row: raise HTTPException(status_code=404,detail="作业辅助事项不存在")
    return row


def _verification(verification_id: str, db: Session):
    row=db.get(AssistanceVerification,verification_id)
    if not row: raise HTTPException(status_code=404,detail="人工核查记录不存在")
    return row


@router.post("/risk-assessments/{assessment_id}/operation-assistance/generate")
def generate(assessment_id: str, payload: GenerateAssistanceRequest, db: Session=Depends(get_db)):
    risk=db.get(RiskAssessment,assessment_id)
    if not risk: raise HTTPException(status_code=404,detail="风险研判记录不存在")
    try: created,existing=generate_for_risk(db,risk,payload.include_resume_checks)
    except AssistanceError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    types={}
    for item in created: types[item.assistance_type]=types.get(item.assistance_type,0)+1
    return ok({"created_count":len(created),"existing_open_count":len(existing),"type_counts":types,"created":[serialize_record(db,x,False) for x in created],"existing":[serialize_record(db,x,False) for x in existing]},"作业辅助事项生成完成")


@router.get("/operation-assistance.csv")
def records_csv(project_id: str|None=None, db: Session=Depends(get_db)):
    stmt=select(OperationAssistanceRecord)
    if project_id: stmt=stmt.where(OperationAssistanceRecord.project_id==project_id)
    rows=db.scalars(stmt.order_by(OperationAssistanceRecord.created_at)).all(); stream=io.StringIO(); writer=csv.writer(stream)
    writer.writerow(["事项ID","项目","区域","设备","类型","优先级","状态","风险研判ID","正式关注等级","主要依据","生成时间","核查时间","核查结果","闭环时间","规则版本"])
    for row in rows:
        data=serialize_record(db,row); check=(data["verifications"] or [None])[0]; risk=db.get(RiskAssessment,row.risk_assessment_id) if row.risk_assessment_id else None
        writer.writerow([row.id,data["project_name"] or "未关联项目",data["work_area_name"] or "",data["equipment_name"] or "",data["assistance_type_label"],data["priority_label"],data["status_label"],row.risk_assessment_id or "",risk.attention_level if risk else "",row.source_summary,row.created_at,check.get("verified_at") if check else "",check.get("verification_result_label") if check else "",row.closed_at or "",row.rule_version])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="operation_assistance.csv"'})


@router.get("/assistance-verifications.csv")
def verifications_csv(project_id: str|None=None, db: Session=Depends(get_db)):
    stmt=select(AssistanceVerification,OperationAssistanceRecord).join(OperationAssistanceRecord,AssistanceVerification.assistance_record_id==OperationAssistanceRecord.id)
    if project_id: stmt=stmt.where(OperationAssistanceRecord.project_id==project_id)
    stream=io.StringIO(); writer=csv.writer(stream); writer.writerow(["核查ID","事项ID","项目ID","核查时间","核查结果","现场发现","后续记录","附件说明"])
    for check,row in db.execute(stmt.order_by(AssistanceVerification.verified_at)).all(): writer.writerow([check.id,row.id,row.project_id or "",check.verified_at,serialize_verification(check)["verification_result_label"],check.finding_text,check.action_note,check.attachment_note])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="assistance_verifications.csv"'})


@router.get("/operation-assistance/summary")
def summary(project_id: str|None=None, db: Session=Depends(get_db)):
    condition=[]
    if project_id: condition.append(OperationAssistanceRecord.project_id==project_id)
    counts=dict(db.execute(select(OperationAssistanceRecord.status,func.count()).where(*condition).group_by(OperationAssistanceRecord.status)).all())
    urgent=db.scalar(select(func.count()).select_from(OperationAssistanceRecord).where(*condition,OperationAssistanceRecord.priority_level=="URGENT",OperationAssistanceRecord.status.in_(("PENDING","IN_PROGRESS")))) or 0
    return ok({"pending_count":counts.get("PENDING",0)+counts.get("IN_PROGRESS",0),"priority_count":urgent,"verified_count":counts.get("VERIFIED",0),"closed_count":counts.get("CLOSED",0),"status_counts":counts})


@router.post("/operation-assistance")
def create(payload: AssistanceCreate, db: Session=Depends(get_db)):
    try: row=create_manual(db,payload.model_dump())
    except AssistanceError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok(serialize_record(db,row),"人工作业辅助事项已创建")


@router.get("/operation-assistance")
def list_records(project_id: str|None=None,status: str|None=None,priority_level: str|None=None,work_area_id: str|None=None,equipment_id: str|None=None,risk_assessment_id: str|None=None,limit:int=Query(200,ge=1,le=500),db:Session=Depends(get_db)):
    stmt=select(OperationAssistanceRecord)
    for field,value in ((OperationAssistanceRecord.project_id,project_id),(OperationAssistanceRecord.status,status),(OperationAssistanceRecord.priority_level,priority_level),(OperationAssistanceRecord.work_area_id,work_area_id),(OperationAssistanceRecord.equipment_id,equipment_id),(OperationAssistanceRecord.risk_assessment_id,risk_assessment_id)):
        if value: stmt=stmt.where(field==value)
    rows=db.scalars(stmt.order_by(OperationAssistanceRecord.created_at.desc()).limit(limit)).all()
    return ok([serialize_record(db,row,False) for row in rows])


@router.get("/operation-assistance/{record_id}")
def detail(record_id:str,db:Session=Depends(get_db)): return ok(serialize_record(db,_record(record_id,db)))


@router.patch("/operation-assistance/{record_id}")
def update(record_id:str,payload:AssistanceUpdate,db:Session=Depends(get_db)):
    row=_record(record_id,db); values=payload.model_dump(exclude_unset=True); status=values.pop("status",None)
    if row.source_type=="rule" and "priority_level" in values: raise HTTPException(status_code=409,detail="规则生成事项的优先级为生成时快照，不能直接修改")
    if status:
        try: update_status(db,row,status)
        except AssistanceError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    if "inspection_checklist" in values: values["inspection_checklist"]=json.dumps(values["inspection_checklist"],ensure_ascii=False)
    for key,value in values.items(): setattr(row,key,value)
    db.commit(); return ok(serialize_record(db,row),"作业辅助事项已更新")


@router.post("/operation-assistance/{record_id}/verifications")
def create_verification(record_id:str,payload:AssistanceVerificationCreate,db:Session=Depends(get_db)):
    row=_record(record_id,db)
    if row.status in {"CLOSED","CANCELLED"}: raise HTTPException(status_code=409,detail="已闭环或已取消事项不能新增核查记录")
    verification=add_verification(db,row,payload.model_dump()); return ok(serialize_verification(verification),"人工核查结果已记录")


@router.patch("/assistance-verifications/{verification_id}")
def update_verification(verification_id:str,payload:AssistanceVerificationUpdate,db:Session=Depends(get_db)):
    row=_verification(verification_id,db); parent=_record(row.assistance_record_id,db)
    if parent.status=="CLOSED": raise HTTPException(status_code=409,detail="已闭环事项的历史核查记录不能修改")
    values=payload.model_dump(); values["checklist_json"]=json.dumps(values.pop("checklist",[]),ensure_ascii=False)
    if values.get("verified_at") is None: values.pop("verified_at")
    for key,value in values.items(): setattr(row,key,value)
    db.commit(); return ok(serialize_verification(row),"人工核查结果已更新")


@router.post("/operation-assistance/{record_id}/close")
def close(record_id:str,payload:CloseAssistanceRequest,db:Session=Depends(get_db)):
    try: row=close_record(db,_record(record_id,db),payload.closed_by_note)
    except AssistanceError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    return ok(serialize_record(db,row),"作业辅助事项已闭环")


@router.get("/operation-assistance/{record_id}/trace")
def trace(record_id:str,db:Session=Depends(get_db)): return ok(trace_chain(db,_record(record_id,db)))


@router.get("/operation-assistance/{record_id}/report.html",response_class=HTMLResponse)
def report_html(record_id:str,db:Session=Depends(get_db)): return HTMLResponse(assistance_report_html(serialize_record(db,_record(record_id,db))))


@router.get("/operation-assistance/{record_id}/report.pdf")
def report_pdf(record_id:str,db:Session=Depends(get_db)):
    content=assistance_report_pdf(serialize_record(db,_record(record_id,db)))
    return Response(content,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="assistance_{record_id[:8]}.pdf"'})
