from __future__ import annotations

import json
import math
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rasterio.crs import CRS
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import (
    ChangeAnalysis, ChangeRainfallContext, ConstructionProject, DatasetRecord,
    RainfallEvent, RainfallObservation, RainfallStation, RiskAssessment,
    RiskRainfallContext,
)

RULE_VERSION = "rainfall-event-v1"
STATION_TYPES = {"manual_station", "automatic_station", "external_source"}
COVERAGE_LABELS = {
    "COMPLETE": "数据覆盖完整", "PARTIAL": "数据覆盖不完整",
    "NO_DATA": "无降雨数据", "TIME_REFERENCE_LIMITED": "时间参考受限",
}


class RainfallError(ValueError):
    pass


class RainfallDataProvider(ABC):
    @abstractmethod
    def records(self) -> list[dict]: ...


class ManualRainfallProvider(RainfallDataProvider):
    def __init__(self, record: dict): self.record = record
    def records(self) -> list[dict]: return [self.record]


class CSVImportRainfallProvider(RainfallDataProvider):
    def __init__(self, records: list[dict]): self._records = records
    def records(self) -> list[dict]: return self._records


def aware(value: datetime) -> datetime:
    if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_station_coordinates(x: float | None, y: float | None, crs: str | None) -> tuple[float | None,float | None,str | None]:
    if x is None and y is None and not crs: return None,None,None
    if x is None or y is None or not crs: raise RainfallError("雨量站坐标必须同时填写X、Y和CRS")
    if not math.isfinite(x) or not math.isfinite(y): raise RainfallError("雨量站坐标必须是有限数值")
    try: parsed=CRS.from_user_input(crs)
    except Exception as exc: raise RainfallError("雨量站CRS无效") from exc
    if parsed.is_geographic and not (-180<=x<=180 and -90<=y<=90): raise RainfallError("地理坐标经纬度超出合法范围")
    return float(x),float(y),parsed.to_string()


def validate_observation(observed_at: datetime, interval_minutes: int, rainfall_mm: float) -> tuple[datetime,float]:
    when=aware(observed_at)
    if when > datetime.now(timezone.utc)+timedelta(minutes=5): raise RainfallError("观测时间不能晚于当前时间")
    if interval_minutes<=0: raise RainfallError("观测时间间隔必须大于0")
    if not math.isfinite(rainfall_mm) or rainfall_mm<0: raise RainfallError("降雨量必须是大于等于0的有限数值")
    return when,float(rainfall_mm)/(interval_minutes/60)


def serialize_station(row: RainfallStation) -> dict:
    return {key:getattr(row,key) for key in ("id","project_id","station_code","station_name","station_type","x","y","crs","location_description","source_name","status","description","created_at","updated_at")}


def serialize_observation(row: RainfallObservation, station: RainfallStation | None=None) -> dict:
    data={key:getattr(row,key) for key in ("id","station_id","project_id","observed_at","interval_minutes","rainfall_mm","rainfall_rate_mm_h","source_type","quality_status","source_note","created_at")}
    data["station_code"]=station.station_code if station else None; data["station_name"]=station.station_name if station else None
    return data


def serialize_event(row: RainfallEvent) -> dict:
    return {key:getattr(row,key) for key in ("id","project_id","generation_id","event_name","start_time","end_time","duration_minutes","total_rainfall_mm","peak_interval_rainfall_mm","peak_interval_minutes","peak_rainfall_rate_mm_h","peak_time","observation_count","station_count","event_status","rule_version","created_at","updated_at")}


def station_average_total(rows: list[RainfallObservation]) -> float:
    totals=defaultdict(float)
    for row in rows: totals[row.station_id]+=row.rainfall_mm
    return sum(totals.values())/len(totals) if totals else 0.0


def generate_events(db: Session, project_id: str) -> list[RainfallEvent]:
    if not db.get(ConstructionProject,project_id): raise RainfallError("施工项目不存在")
    positive=list(db.scalars(select(RainfallObservation).where(RainfallObservation.project_id==project_id,RainfallObservation.rainfall_mm>0).order_by(RainfallObservation.observed_at)).all())
    generation=str(uuid.uuid4()); gap=timedelta(hours=get_config().rainfall.rain_event_gap_hours)
    active=list(db.scalars(select(RainfallEvent).where(RainfallEvent.project_id==project_id,RainfallEvent.event_status=="active")).all())
    for old in active: old.event_status="superseded"
    groups:list[list[RainfallObservation]]=[]
    for row in positive:
        if not groups or aware(row.observed_at)-aware(groups[-1][-1].observed_at)>gap: groups.append([row])
        else: groups[-1].append(row)
    output=[]
    for index,group in enumerate(groups,1):
        first=min(group,key=lambda x:aware(x.observed_at)); last=max(group,key=lambda x:aware(x.observed_at)); peak=max(group,key=lambda x:x.rainfall_rate_mm_h)
        start=aware(first.observed_at)-timedelta(minutes=first.interval_minutes); end=aware(last.observed_at)
        event=RainfallEvent(id=str(uuid.uuid4()),project_id=project_id,generation_id=generation,event_name=f"降雨事件 {start:%Y-%m-%d %H:%M}",start_time=start,end_time=end,duration_minutes=max(1,round((end-start).total_seconds()/60)),total_rainfall_mm=station_average_total(group),peak_interval_rainfall_mm=peak.rainfall_mm,peak_interval_minutes=peak.interval_minutes,peak_rainfall_rate_mm_h=peak.rainfall_rate_mm_h,peak_time=aware(peak.observed_at),observation_count=len(group),station_count=len({x.station_id for x in group}),event_status="active",rule_version=get_config().rainfall.version)
        db.add(event); output.append(event)
    db.commit()
    for row in output: db.refresh(row)
    return output


def project_overview(db: Session, project_id: str, now: datetime | None=None) -> dict:
    current=aware(now or datetime.now(timezone.utc)); rows=list(db.scalars(select(RainfallObservation).where(RainfallObservation.project_id==project_id).order_by(RainfallObservation.observed_at)).all())
    stations=list(db.scalars(select(RainfallStation).where(RainfallStation.project_id==project_id,RainfallStation.status=="active")).all())
    latest=db.scalar(select(RainfallEvent).where(RainfallEvent.project_id==project_id,RainfallEvent.event_status=="active").order_by(RainfallEvent.end_time.desc()).limit(1))
    def total(hours:int):
        selected=[x for x in rows if aware(x.observed_at)>=current-timedelta(hours=hours) and aware(x.observed_at)<=current]
        return station_average_total(selected) if selected else None
    return {"project_id":project_id,"station_count":len(stations),"observation_count":len(rows),"latest_observation_at":aware(rows[-1].observed_at) if rows else None,"rainfall_24h_mm":total(24),"rainfall_72h_mm":total(72),"rainfall_7d_mm":total(168),"latest_event":serialize_event(latest) if latest else None,"has_data":bool(rows)}


def _coverage(rows:list[RainfallObservation], start:datetime, end:datetime, limited:bool)->str:
    if not rows: return "NO_DATA"
    if limited: return "TIME_REFERENCE_LIMITED"
    grouped=defaultdict(list)
    for row in rows: grouped[row.station_id].append(row)
    for items in grouped.values():
        items.sort(key=lambda x:aware(x.observed_at)); coverage_start=aware(items[0].observed_at)-timedelta(minutes=items[0].interval_minutes); coverage_end=aware(items[-1].observed_at)
        gaps=[(aware(b.observed_at)-aware(a.observed_at)).total_seconds()/60 for a,b in zip(items,items[1:])]
        allowed=max((x.interval_minutes for x in items),default=60)*1.5
        if coverage_start<=start and coverage_end>=end and all(g<=allowed for g in gaps): return "COMPLETE"
    return "PARTIAL"


def _change_window(db:Session, change:ChangeAnalysis)->tuple[datetime,datetime,bool,str]:
    d1=db.get(DatasetRecord,change.dataset_t1_id); d2=db.get(DatasetRecord,change.dataset_t2_id)
    capture1=d1.capture_time if d1 else None; capture2=d2.capture_time if d2 else None
    limited=not (capture1 and capture2)
    first=capture1 or change.finished_t1_at or change.created_at; second=capture2 or change.finished_t2_at or change.completed_at or change.created_at
    first,second=aware(first),aware(second)
    if first>second: first,second=second,first
    pre=get_config().rainfall.change_rainfall_pre_window_hours
    return first-timedelta(hours=pre),second,limited,"dataset_capture_time" if not limited else "task_or_import_time_reference"


def _context_values(db:Session, project_id:str, start:datetime, end:datetime, limited:bool)->dict:
    rows=list(db.scalars(select(RainfallObservation).where(RainfallObservation.project_id==project_id,RainfallObservation.observed_at>=start,RainfallObservation.observed_at<=end).order_by(RainfallObservation.observed_at)).all())
    events=list(db.scalars(select(RainfallEvent).where(RainfallEvent.project_id==project_id,RainfallEvent.event_status=="active",RainfallEvent.end_time>=start,RainfallEvent.start_time<=end).order_by(RainfallEvent.start_time)).all())
    peak=max(rows,key=lambda x:x.rainfall_rate_mm_h) if rows else None
    return {"total_rainfall_mm":station_average_total(rows) if rows else None,"rainfall_event_count":len(events),"observation_count":len(rows),"station_count":len({x.station_id for x in rows}),"peak_rainfall_rate_mm_h":peak.rainfall_rate_mm_h if peak else None,"peak_time":aware(peak.observed_at) if peak else None,"has_rainfall_data":bool(rows),"coverage_status":_coverage(rows,start,end,limited),"rainfall_event_ids":json.dumps([x.id for x in events])}


def serialize_change_context(row:ChangeRainfallContext)->dict:
    return {"id":row.id,"change_analysis_id":row.change_analysis_id,"project_id":row.project_id,"window_start":row.window_start,"window_end":row.window_end,"pre_window_hours":row.pre_window_hours,"total_rainfall_mm":row.total_rainfall_mm,"rainfall_event_count":row.rainfall_event_count,"observation_count":row.observation_count,"station_count":row.station_count,"peak_rainfall_rate_mm_h":row.peak_rainfall_rate_mm_h,"peak_time":row.peak_time,"has_rainfall_data":row.has_rainfall_data,"coverage_status":row.coverage_status,"coverage_label":COVERAGE_LABELS.get(row.coverage_status,row.coverage_status),"time_reference":row.time_reference,"rainfall_event_ids":json.loads(row.rainfall_event_ids or "[]"),"rule_version":row.rule_version,"created_at":row.created_at}


def build_change_context(db:Session, change:ChangeAnalysis)->ChangeRainfallContext:
    if not change.project_id: raise RainfallError("该变化分析尚未关联施工项目，无法加载项目降雨背景。")
    start,end,limited,time_reference=_change_window(db,change); values=_context_values(db,change.project_id,start,end,limited)
    row=db.scalar(select(ChangeRainfallContext).where(ChangeRainfallContext.change_analysis_id==change.id))
    if row is None: row=ChangeRainfallContext(id=str(uuid.uuid4()),change_analysis_id=change.id,project_id=change.project_id,window_start=start,window_end=end,pre_window_hours=get_config().rainfall.change_rainfall_pre_window_hours,coverage_status=values["coverage_status"],time_reference=time_reference); db.add(row)
    for key,value in {**values,"project_id":change.project_id,"window_start":start,"window_end":end,"pre_window_hours":get_config().rainfall.change_rainfall_pre_window_hours,"time_reference":time_reference,"rule_version":get_config().rainfall.version,"created_at":datetime.now(timezone.utc)}.items(): setattr(row,key,value)
    db.flush(); out=get_config().paths.outputs/"rainfall"/"contexts"; out.mkdir(parents=True,exist_ok=True); path=out/f"change_{change.id}.json"; row.summary_json_path=str(path); db.commit(); db.refresh(row); path.write_text(json.dumps(serialize_change_context(row),ensure_ascii=False,indent=2,default=str),encoding="utf-8"); return row


def serialize_risk_context(row:RiskRainfallContext)->dict:
    return {key:getattr(row,key) for key in ("id","risk_assessment_id","change_rainfall_context_id","project_id","window_start","window_end","total_rainfall_mm","rainfall_event_count","observation_count","station_count","peak_rainfall_rate_mm_h","peak_time","latest_event_start","latest_event_end","has_rainfall_data","coverage_status","time_reference","rule_version","created_at")} | {"coverage_label":COVERAGE_LABELS.get(row.coverage_status,row.coverage_status),"rainfall_event_ids":json.loads(row.rainfall_event_ids or "[]")}


def build_risk_context(db:Session, risk:RiskAssessment)->RiskRainfallContext:
    if not risk.source_change_analysis_id: raise RainfallError("该风险研判没有可关联的时空变化分析")
    change=db.get(ChangeAnalysis,risk.source_change_analysis_id)
    if not change: raise RainfallError("来源时空变化分析不存在")
    if not change.project_id and risk.project_id:
        change.project_id=risk.project_id
        db.flush()
    context=build_change_context(db,change); ids=json.loads(context.rainfall_event_ids or "[]"); events=[db.get(RainfallEvent,x) for x in ids]; events=[x for x in events if x]
    latest=max(events,key=lambda x:aware(x.end_time)) if events else None
    row=db.scalar(select(RiskRainfallContext).where(RiskRainfallContext.risk_assessment_id==risk.id))
    if row is None: row=RiskRainfallContext(id=str(uuid.uuid4()),risk_assessment_id=risk.id,change_rainfall_context_id=context.id,project_id=context.project_id,window_start=context.window_start,window_end=context.window_end,coverage_status=context.coverage_status,time_reference=context.time_reference); db.add(row)
    values={"change_rainfall_context_id":context.id,"project_id":context.project_id,"window_start":context.window_start,"window_end":context.window_end,"total_rainfall_mm":context.total_rainfall_mm,"rainfall_event_count":context.rainfall_event_count,"observation_count":context.observation_count,"station_count":context.station_count,"peak_rainfall_rate_mm_h":context.peak_rainfall_rate_mm_h,"peak_time":context.peak_time,"latest_event_start":latest.start_time if latest else None,"latest_event_end":latest.end_time if latest else None,"has_rainfall_data":context.has_rainfall_data,"coverage_status":context.coverage_status,"time_reference":context.time_reference,"rainfall_event_ids":context.rainfall_event_ids,"rule_version":context.rule_version,"created_at":datetime.now(timezone.utc)}
    for key,value in values.items(): setattr(row,key,value)
    db.commit(); db.refresh(row); return row


def event_is_referenced(db:Session,event_id:str)->bool:
    for model in (ChangeRainfallContext,RiskRainfallContext):
        for raw in db.scalars(select(model.rainfall_event_ids)).all():
            if event_id in json.loads(raw or "[]"): return True
    return False


def observation_is_referenced(db:Session,row:RainfallObservation)->bool:
    for model in (ChangeRainfallContext,RiskRainfallContext):
        found=db.scalar(select(model.id).where(model.project_id==row.project_id,model.window_start<=row.observed_at,model.window_end>=row.observed_at).limit(1))
        if found: return True
    return False
