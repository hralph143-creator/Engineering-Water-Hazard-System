from __future__ import annotations

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
    ChangeAnalysis, ChangeRainfallContext, ConstructionProject, RainfallEvent,
    RainfallObservation, RainfallStation, RiskAssessment, RiskRainfallContext,
)
from ..schemas.common import ok
from ..schemas.rainfall import RainfallObservationCreate, RainfallStationCreate, RainfallStationUpdate
from ..services.rainfall_service import (
    RainfallError, STATION_TYPES, build_change_context, build_risk_context,
    event_is_referenced, generate_events, observation_is_referenced,
    project_overview, serialize_change_context, serialize_event,
    serialize_observation, serialize_risk_context, serialize_station,
    validate_observation, validate_station_coordinates,
)

router=APIRouter(tags=["rainfall"])


def _station(station_id:str,db:Session)->RainfallStation:
    row=db.get(RainfallStation,station_id)
    if not row: raise HTTPException(status_code=404,detail="雨量站不存在")
    return row


def _csv_response(headers:list[str],rows:list[list],filename:str):
    stream=io.StringIO(); writer=csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="{filename}"'})


@router.post("/rainfall/stations")
def create_station(payload:RainfallStationCreate,db:Session=Depends(get_db)):
    if not db.get(ConstructionProject,payload.project_id): raise HTTPException(status_code=404,detail="施工项目不存在")
    if payload.station_type not in STATION_TYPES: raise HTTPException(status_code=400,detail="雨量站类型无效")
    try: x,y,crs=validate_station_coordinates(payload.x,payload.y,payload.crs)
    except RainfallError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    row=RainfallStation(id=str(uuid.uuid4()),project_id=payload.project_id,station_code=payload.station_code.strip(),station_name=payload.station_name.strip(),station_type=payload.station_type,x=x,y=y,crs=crs,location_description=payload.location_description.strip(),source_name=payload.source_name.strip(),description=payload.description.strip())
    try: db.add(row); db.commit(); db.refresh(row)
    except IntegrityError as exc: db.rollback(); raise HTTPException(status_code=409,detail="项目内雨量站编号已存在") from exc
    return ok(serialize_station(row),"雨量站已创建")


@router.get("/rainfall/stations")
def list_stations(project_id:str|None=None,include_archived:bool=False,db:Session=Depends(get_db)):
    statement=select(RainfallStation)
    if project_id: statement=statement.where(RainfallStation.project_id==project_id)
    if not include_archived: statement=statement.where(RainfallStation.status=="active")
    return ok([serialize_station(x) for x in db.scalars(statement.order_by(RainfallStation.created_at.desc())).all()])


@router.patch("/rainfall/stations/{station_id}")
def update_station(station_id:str,payload:RainfallStationUpdate,db:Session=Depends(get_db)):
    row=_station(station_id,db); values=payload.model_dump(exclude_unset=True)
    if "station_type" in values and values["station_type"] not in STATION_TYPES: raise HTTPException(status_code=400,detail="雨量站类型无效")
    if values.pop("clear_coordinates",False): row.x=row.y=None; row.crs=None
    elif any(k in values for k in ("x","y","crs")):
        try: row.x,row.y,row.crs=validate_station_coordinates(values.pop("x",row.x),values.pop("y",row.y),values.pop("crs",row.crs))
        except RainfallError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    for key,value in values.items(): setattr(row,key,value.strip() if isinstance(value,str) else value)
    db.commit(); db.refresh(row); return ok(serialize_station(row),"雨量站已更新")


@router.delete("/rainfall/stations/{station_id}")
def archive_station(station_id:str,db:Session=Depends(get_db)):
    row=_station(station_id,db); row.status="archived"; db.commit(); return ok(message="雨量站已归档，历史观测继续保留")


@router.get("/rainfall/stations.csv")
def stations_csv(project_id:str|None=None,db:Session=Depends(get_db)):
    rows=list_stations(project_id,True,db)["data"]
    return _csv_response(["id","project_id","station_code","station_name","station_type","x","y","crs","source_name","status"],[[x[k] for k in ("id","project_id","station_code","station_name","station_type","x","y","crs","source_name","status")] for x in rows],"rainfall_stations.csv")


def _add_observation(payload:RainfallObservationCreate,db:Session)->RainfallObservation:
    station=db.get(RainfallStation,payload.station_id)
    if not station: raise RainfallError("雨量站不存在")
    if station.status!="active": raise RainfallError("雨量站已归档，不能新增观测")
    if station.project_id!=payload.project_id: raise RainfallError("雨量站与观测记录不属于同一项目")
    when,rate=validate_observation(payload.observed_at,payload.interval_minutes,payload.rainfall_mm)
    row=RainfallObservation(id=str(uuid.uuid4()),station_id=station.id,project_id=payload.project_id,observed_at=when,interval_minutes=payload.interval_minutes,rainfall_mm=payload.rainfall_mm,rainfall_rate_mm_h=rate,source_type=payload.source_type,quality_status=payload.quality_status,source_note=payload.source_note.strip())
    db.add(row); db.flush(); return row


@router.post("/rainfall/observations")
def create_observation(payload:RainfallObservationCreate,db:Session=Depends(get_db)):
    try: row=_add_observation(payload,db); db.commit(); db.refresh(row)
    except RainfallError as exc: db.rollback(); raise HTTPException(status_code=400,detail=str(exc)) from exc
    except IntegrityError as exc: db.rollback(); raise HTTPException(status_code=409,detail="相同雨量站、观测时间和时间间隔的记录已存在") from exc
    return ok(serialize_observation(row,db.get(RainfallStation,row.station_id)),"降雨观测已保存")


@router.get("/rainfall/observations")
def list_observations(project_id:str|None=None,station_id:str|None=None,date_from:datetime|None=None,date_to:datetime|None=None,db:Session=Depends(get_db)):
    statement=select(RainfallObservation)
    if project_id: statement=statement.where(RainfallObservation.project_id==project_id)
    if station_id: statement=statement.where(RainfallObservation.station_id==station_id)
    if date_from: statement=statement.where(RainfallObservation.observed_at>=date_from)
    if date_to: statement=statement.where(RainfallObservation.observed_at<=date_to)
    rows=db.scalars(statement.order_by(RainfallObservation.observed_at.desc()).limit(5000)).all(); stations={x.id:x for x in db.scalars(select(RainfallStation).where(RainfallStation.id.in_([r.station_id for r in rows]))).all()} if rows else {}
    return ok([serialize_observation(x,stations.get(x.station_id)) for x in rows])


@router.delete("/rainfall/observations/{observation_id}")
def delete_observation(observation_id:str,db:Session=Depends(get_db)):
    row=db.get(RainfallObservation,observation_id)
    if not row: raise HTTPException(status_code=404,detail="降雨观测不存在")
    if observation_is_referenced(db,row): raise HTTPException(status_code=409,detail="该观测已进入正式降雨背景分析，不能直接删除，请先重新计算或清理相关背景记录。")
    db.delete(row); db.commit(); return ok(message="降雨观测已删除")


@router.post("/rainfall/observations/import.csv")
def import_observations(project_id:str,file:UploadFile=File(...),db:Session=Depends(get_db)):
    if not db.get(ConstructionProject,project_id): raise HTTPException(status_code=404,detail="施工项目不存在")
    try: content=file.file.read().decode("utf-8-sig"); records=list(csv.DictReader(io.StringIO(content)))
    except Exception as exc: raise HTTPException(status_code=400,detail="CSV文件无法读取") from exc
    stations={x.station_code:x for x in db.scalars(select(RainfallStation).where(RainfallStation.project_id==project_id,RainfallStation.status=="active")).all()}; results=[]; success=0
    for line,item in enumerate(records,2):
        try:
            station=stations.get((item.get("station_code") or "").strip())
            if not station: raise RainfallError("项目内station_code不存在")
            payload=RainfallObservationCreate(station_id=station.id,project_id=project_id,observed_at=item.get("observed_at"),rainfall_mm=item.get("rainfall_mm"),interval_minutes=item.get("interval_minutes"),source_type="csv_import",source_note=(item.get("source_note") or ""))
            _add_observation(payload,db); db.commit(); success+=1; results.append({"line":line,"status":"success","reason":"导入成功"})
        except Exception as exc:
            db.rollback(); reason="重复观测记录" if isinstance(exc,IntegrityError) else str(exc); results.append({"line":line,"status":"failed","reason":reason})
    return ok({"total":len(records),"success_count":success,"failed_count":len(records)-success,"items":results},"CSV逐行处理完成")


@router.get("/rainfall/observations.csv")
def observations_csv(project_id:str|None=None,db:Session=Depends(get_db)):
    rows=list_observations(project_id=project_id,db=db)["data"]
    keys=("id","station_code","station_id","project_id","observed_at","interval_minutes","rainfall_mm","rainfall_rate_mm_h","source_type","quality_status","source_note")
    return _csv_response(list(keys),[[x[k] for k in keys] for x in rows],"rainfall_observations.csv")


@router.post("/rainfall/projects/{project_id}/events/generate")
def regenerate_events(project_id:str,db:Session=Depends(get_db)):
    try: rows=generate_events(db,project_id)
    except RainfallError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok([serialize_event(x) for x in rows],"已生成新版本降雨事件，历史版本继续保留")


@router.get("/rainfall/events")
def list_events(project_id:str|None=None,include_history:bool=False,db:Session=Depends(get_db)):
    statement=select(RainfallEvent)
    if project_id: statement=statement.where(RainfallEvent.project_id==project_id)
    if not include_history: statement=statement.where(RainfallEvent.event_status=="active")
    return ok([serialize_event(x) for x in db.scalars(statement.order_by(RainfallEvent.start_time.desc())).all()])


@router.delete("/rainfall/events/{event_id}")
def delete_event(event_id:str,db:Session=Depends(get_db)):
    row=db.get(RainfallEvent,event_id)
    if not row: raise HTTPException(status_code=404,detail="降雨事件不存在")
    if event_is_referenced(db,event_id): raise HTTPException(status_code=409,detail="该降雨事件已被变化或风险降雨背景引用，不能删除。")
    db.delete(row); db.commit(); return ok(message="降雨事件已删除")


@router.get("/rainfall/events.csv")
def events_csv(project_id:str|None=None,db:Session=Depends(get_db)):
    rows=list_events(project_id,True,db)["data"]; keys=("id","project_id","event_name","start_time","end_time","duration_minutes","total_rainfall_mm","peak_interval_rainfall_mm","peak_interval_minutes","peak_rainfall_rate_mm_h","peak_time","observation_count","station_count","event_status","rule_version")
    return _csv_response(list(keys),[[x[k] for k in keys] for x in rows],"rainfall_events.csv")


@router.get("/rainfall/projects/{project_id}/overview")
def overview(project_id:str,db:Session=Depends(get_db)): return ok(project_overview(db,project_id))


def _series(db:Session,project_id:str,start:datetime,end:datetime)->list[dict]:
    rows=db.scalars(select(RainfallObservation).where(RainfallObservation.project_id==project_id,RainfallObservation.observed_at>=start,RainfallObservation.observed_at<=end).order_by(RainfallObservation.observed_at)).all()
    return [{"observed_at":x.observed_at,"rainfall_mm":x.rainfall_mm,"rainfall_rate_mm_h":x.rainfall_rate_mm_h,"station_id":x.station_id,"interval_minutes":x.interval_minutes} for x in rows]


@router.post("/change-analyses/{analysis_id}/rainfall-context")
def run_change_context(analysis_id:str,db:Session=Depends(get_db)):
    row=db.get(ChangeAnalysis,analysis_id)
    if not row: raise HTTPException(status_code=404,detail="变化分析不存在")
    try: context=build_change_context(db,row)
    except RainfallError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    data=serialize_change_context(context); data["series"]=_series(db,context.project_id,context.window_start,context.window_end); return ok(data,"变化分析降雨背景已生成")


@router.get("/change-analyses/{analysis_id}/rainfall-context")
def get_change_context(analysis_id:str,db:Session=Depends(get_db)):
    row=db.scalar(select(ChangeRainfallContext).where(ChangeRainfallContext.change_analysis_id==analysis_id))
    if not row: return ok(None,"暂无降雨背景分析")
    data=serialize_change_context(row); data["series"]=_series(db,row.project_id,row.window_start,row.window_end); return ok(data)


@router.get("/change-analyses/{analysis_id}/rainfall-context.csv")
def change_context_csv(analysis_id:str,db:Session=Depends(get_db)):
    data=get_change_context(analysis_id,db)["data"]
    if not data: raise HTTPException(status_code=404,detail="暂无降雨背景分析")
    keys=("change_analysis_id","project_id","window_start","window_end","pre_window_hours","total_rainfall_mm","rainfall_event_count","observation_count","station_count","peak_rainfall_rate_mm_h","peak_time","coverage_status","time_reference","rule_version")
    return _csv_response(list(keys),[[data[k] for k in keys]],f"change_rainfall_{analysis_id[:8]}.csv")


@router.post("/risk-assessments/{assessment_id}/rainfall-context")
def run_risk_context(assessment_id:str,db:Session=Depends(get_db)):
    row=db.get(RiskAssessment,assessment_id)
    if not row: raise HTTPException(status_code=404,detail="风险研判不存在")
    try: context=build_risk_context(db,row)
    except RainfallError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok(serialize_risk_context(context),"风险研判降雨背景已生成")


@router.get("/risk-assessments/{assessment_id}/rainfall-context")
def get_risk_context(assessment_id:str,db:Session=Depends(get_db)):
    row=db.scalar(select(RiskRainfallContext).where(RiskRainfallContext.risk_assessment_id==assessment_id)); return ok(serialize_risk_context(row) if row else None,"暂无降雨背景分析" if not row else "")


@router.get("/risk-assessments/{assessment_id}/rainfall-context.csv")
def risk_context_csv(assessment_id:str,db:Session=Depends(get_db)):
    data=get_risk_context(assessment_id,db)["data"]
    if not data: raise HTTPException(status_code=404,detail="暂无降雨背景分析")
    keys=("risk_assessment_id","project_id","window_start","window_end","total_rainfall_mm","rainfall_event_count","observation_count","station_count","peak_rainfall_rate_mm_h","peak_time","latest_event_start","latest_event_end","coverage_status","time_reference","rule_version")
    return _csv_response(list(keys),[[data[k] for k in keys]],f"risk_rainfall_{assessment_id[:8]}.csv")
