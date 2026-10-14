import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api import dashboard as dashboard_api
from backend.app.api import rainfall as rainfall_api
from backend.app.db.database import Base
from backend.app.db.models import (
    ChangeAnalysis, ChangeRainfallContext, ConstructionProject, DatasetRecord,
    RainfallEvent, RainfallObservation, RainfallStation, RiskAssessment,
    RiskRainfallContext,
)
from backend.app.schemas.rainfall import RainfallObservationCreate, RainfallStationCreate, RainfallStationUpdate
from backend.app.services import rainfall_service, risk_report_service
from backend.app.services.ai_context import build_change_analysis_context, build_risk_assessment_context
from backend.app.services.ai_prompts import CHANGE_ANALYSIS_GUIDE, RISK_ASSESSMENT_GUIDE


NOW=datetime(2026,10,10,12,tzinfo=timezone.utc)


def _engine(tmp_path):
    engine=create_engine(f"sqlite:///{(tmp_path/'rain.db').as_posix()}")
    Base.metadata.create_all(engine); return engine


def _cfg(tmp_path,gap=6,pre=24):
    out=tmp_path/"outputs"; out.mkdir(exist_ok=True)
    return SimpleNamespace(paths=SimpleNamespace(outputs=out),rainfall=SimpleNamespace(version="rainfall-event-v1",rain_event_gap_hours=gap,change_rainfall_pre_window_hours=pre),equipment_position=SimpleNamespace(position_stale_hours=24))


def _project(db,id="p1"):
    row=ConstructionProject(id=id,project_name="东采区",project_code=id.upper(),project_type="open_pit_mine",status="active"); db.add(row); db.commit(); return row


def _station(db,code="S1",coords=False):
    row=RainfallStation(id=f"st-{code}",project_id="p1",station_code=code,station_name=f"站点{code}",station_type="manual_station",x=116.1 if coords else None,y=39.9 if coords else None,crs="EPSG:4326" if coords else None,source_name="现场雨量计"); db.add(row); db.commit(); return row


def _observation(db,station,when=NOW,mm=5,interval=60):
    row=RainfallObservation(id=f"o-{station.station_code}-{when.timestamp()}-{interval}",station_id=station.id,project_id="p1",observed_at=when,interval_minutes=interval,rainfall_mm=mm,rainfall_rate_mm_h=mm/(interval/60),source_type="manual",quality_status="valid"); db.add(row); db.commit(); return row


def _change(db,capture=True,project=True):
    for suffix,when in (("1",NOW-timedelta(hours=6)),("2",NOW)):
        db.add(DatasetRecord(id=f"d{suffix}",name=f"D{suffix}",path=f"/{suffix}.tif",source_type="Sentinel-1 SAR",width=1,height=1,bands=2,dtype="float32",transform="[]",bounds="[]",vv_stats="{}",vh_stats="{}",capture_time=when if capture else None,created_at=when-timedelta(days=1)))
    db.commit()
    row=ChangeAnalysis(id="c1",name="变化",task_t1_id="t1",task_t2_id="t2",result_t1_id="r1",result_t2_id="r2",dataset_t1_id="d1",dataset_t2_id="d2",dataset_t1_name="D1",dataset_t2_name="D2",finished_t1_at=NOW-timedelta(hours=5),finished_t2_at=NOW,model_t1_name="U",model_t2_name="U",threshold_t1=.5,threshold_t2=.5,status="completed",spatial_consistent=True,project_id="p1" if project else None,new_water_ratio=.1,persistent_water_ratio=.1,receded_water_ratio=0,stable_non_water_ratio=.8,t1_water_ratio=.1,t2_water_ratio=.2,net_change_ratio=.1,completed_at=NOW)
    db.add(row); db.commit(); return row


def _risk(db,change,level="中水害关注"):
    row=RiskAssessment(id="risk1",project_id="p1",assessment_name="研判",source_type="change_analysis",source_change_analysis_id=change.id,source_t1_name="D1",source_t2_name="D2",status="completed",attention_level=level,rule_version="water-risk-v1",risk_reason_codes="[]",risk_reason_text="[]",inspection_advice="[]",operation_note="辅助",completed_at=NOW); db.add(row); db.commit(); return row


@pytest.mark.parametrize("table",["rainfall_stations","rainfall_observations","rainfall_events","change_rainfall_contexts","risk_rainfall_contexts"])
def test_rainfall_tables_created(tmp_path,table): assert table in inspect(_engine(tmp_path)).get_table_names()


def test_create_station_without_coordinates(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); data=rainfall_api.create_station(RainfallStationCreate(project_id="p1",station_code="S1",station_name="人工点"),db)["data"]
        assert data["x"] is None and data["crs"] is None


def test_create_station_with_coordinates(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); data=rainfall_api.create_station(RainfallStationCreate(project_id="p1",station_code="S1",station_name="自动站",station_type="automatic_station",x=116,y=39,crs="EPSG:4326"),db)["data"]
        assert data["x"]==116 and data["station_type"]=="automatic_station"


@pytest.mark.parametrize("x,y,crs,message",[(181,30,"EPSG:4326","范围"),(116,91,"EPSG:4326","范围"),(1,2,"BAD_CRS","CRS")])
def test_invalid_station_coordinates(tmp_path,x,y,crs,message):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        with pytest.raises(HTTPException,match=message): rainfall_api.create_station(RainfallStationCreate(project_id="p1",station_code="S1",station_name="站",x=x,y=y,crs=crs),db)


def test_partial_station_coordinates_rejected():
    with pytest.raises(ValidationError): RainfallStationCreate(project_id="p1",station_code="S1",station_name="站",x=1)


def test_update_and_archive_station(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db)
        updated=rainfall_api.update_station(station.id,RainfallStationUpdate(station_name="新名称",source_name="人工记录"),db)["data"]
        assert updated["station_name"]=="新名称"
        rainfall_api.archive_station(station.id,db); assert db.get(RainfallStation,station.id).status=="archived"


def test_manual_observation_and_rate(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"datetime",SimpleNamespace(now=lambda tz:NOW+timedelta(days=1)))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db)
        data=rainfall_api.create_observation(RainfallObservationCreate(station_id=station.id,project_id="p1",observed_at=NOW,interval_minutes=30,rainfall_mm=5),db)["data"]
        assert data["rainfall_mm"]==5 and data["rainfall_rate_mm_h"]==10


@pytest.mark.parametrize("mm,interval",[(-1,60),(float("nan"),60),(float("inf"),60),(1,0)])
def test_invalid_observation_values(mm,interval):
    with pytest.raises((rainfall_service.RainfallError,ValidationError,ValueError)):
        rainfall_service.validate_observation(NOW,interval,mm)


def test_future_observation_rejected():
    with pytest.raises(rainfall_service.RainfallError,match="晚于当前"):
        rainfall_service.validate_observation(datetime.now(timezone.utc)+timedelta(days=1),60,1)


def test_duplicate_observation_constraint(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); _observation(db,station)
        db.add(RainfallObservation(id="dup",station_id=station.id,project_id="p1",observed_at=NOW,interval_minutes=60,rainfall_mm=1,rainfall_rate_mm_h=1))
        with pytest.raises(IntegrityError): db.commit()


def test_zero_mm_is_data_but_no_records_is_no_data(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); assert rainfall_service.project_overview(db,"p1",NOW)["has_data"] is False
        _observation(db,station,mm=0); overview=rainfall_service.project_overview(db,"p1",NOW)
        assert overview["has_data"] is True and overview["rainfall_24h_mm"]==0


def test_csv_import_and_partial_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"datetime",SimpleNamespace(now=lambda tz:NOW+timedelta(days=1)))
    content="station_code,observed_at,rainfall_mm,interval_minutes,source_note\nS1,2026-10-10T10:00:00+00:00,2,60,人工\nBAD,2026-10-10T11:00:00+00:00,3,60,错误\n"
    with Session(_engine(tmp_path)) as db:
        _project(db); _station(db); file=UploadFile(file=io.BytesIO(content.encode()),filename="rain.csv")
        data=rainfall_api.import_observations("p1",file,db)["data"]
        assert data["total"]==2 and data["success_count"]==1 and data["failed_count"]==1 and data["items"][1]["line"]==3


def test_event_split_total_peak_and_version(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,gap=6))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db)
        for hours,mm,interval in ((-10,2,60),(-8,5,30),(0,3,60)): _observation(db,station,NOW+timedelta(hours=hours),mm,interval)
        events=rainfall_service.generate_events(db,"p1")
        assert len(events)==2 and events[0].total_rainfall_mm==7 and events[0].peak_rainfall_rate_mm_h==10 and events[0].rule_version=="rainfall-event-v1"


def test_multi_station_event_uses_station_average_total(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); a=_station(db,"S1"); b=_station(db,"S2"); _observation(db,a,NOW,4); _observation(db,b,NOW,8)
        event=rainfall_service.generate_events(db,"p1")[0]
        assert event.station_count==2 and event.observation_count==2 and event.total_rainfall_mm==6


def test_event_regeneration_preserves_old_version(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); _observation(db,station)
        first=rainfall_service.generate_events(db,"p1")[0]; second=rainfall_service.generate_events(db,"p1")[0]
        assert first.id!=second.id and db.get(RainfallEvent,first.id).event_status=="superseded"


def test_project_overview_accumulations(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db)
        _observation(db,station,NOW-timedelta(hours=12),2); _observation(db,station,NOW-timedelta(hours=48),3); _observation(db,station,NOW-timedelta(days=5),4)
        data=rainfall_service.project_overview(db,"p1",NOW)
        assert data["rainfall_24h_mm"]==2 and data["rainfall_72h_mm"]==5 and data["rainfall_7d_mm"]==9


def test_capture_time_priority_and_complete_coverage(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db,capture=True)
        for hours in range(-6,1): _observation(db,station,NOW+timedelta(hours=hours),0)
        row=rainfall_service.build_change_context(db,change)
        assert rainfall_service.aware(row.window_start)==NOW-timedelta(hours=6) and row.coverage_status=="COMPLETE" and row.time_reference=="dataset_capture_time"


def test_missing_capture_time_is_limited(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db,capture=False); _observation(db,station,NOW,1)
        assert rainfall_service.build_change_context(db,change).coverage_status=="TIME_REFERENCE_LIMITED"


@pytest.mark.parametrize("records,status",[(0,"NO_DATA"),(1,"PARTIAL")])
def test_context_no_data_and_partial(tmp_path,monkeypatch,records,status):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db,capture=True)
        if records: _observation(db,station,NOW-timedelta(hours=3),2)
        row=rainfall_service.build_change_context(db,change); assert row.coverage_status==status and row.has_rainfall_data is bool(records)


def test_change_and_risk_context_persist_without_changing_level(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db); risk=_risk(db,change); _observation(db,station,NOW,10); rainfall_service.generate_events(db,"p1")
        change_context=rainfall_service.build_change_context(db,change); risk_context=rainfall_service.build_risk_context(db,risk)
        assert db.get(ChangeRainfallContext,change_context.id) and db.get(RiskRainfallContext,risk_context.id)
        assert db.get(RiskAssessment,risk.id).attention_level=="中水害关注"


def test_unassociated_change_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); change=_change(db,project=False)
        with pytest.raises(rainfall_service.RainfallError,match="尚未关联"): rainfall_service.build_change_context(db,change)


def _read_stream(response):
    async def read():
        chunks=[]
        async for chunk in response.body_iterator: chunks.append(chunk if isinstance(chunk,bytes) else chunk.encode())
        return b"".join(chunks).decode("utf-8-sig")
    return asyncio.run(read())


def test_context_api_and_all_csv_exports(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db); risk=_risk(db,change); _observation(db,station,NOW,2); rainfall_service.generate_events(db,"p1")
        assert rainfall_api.run_change_context(change.id,db)["data"]["series"]
        assert rainfall_api.run_risk_context(risk.id,db)["data"]["total_rainfall_mm"]==2
        exports=[rainfall_api.stations_csv("p1",db),rainfall_api.observations_csv("p1",db),rainfall_api.events_csv("p1",db),rainfall_api.change_context_csv(change.id,db),rainfall_api.risk_context_csv(risk.id,db)]
        contents=[_read_stream(x) for x in exports]
        assert all(content.strip() and ("id" in content.lower() or "project_id" in content.lower()) for content in contents)


def test_reference_protection_for_observation_and_event(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db); obs=_observation(db,station,NOW,2); event=rainfall_service.generate_events(db,"p1")[0]; rainfall_service.build_change_context(db,change)
        assert rainfall_service.observation_is_referenced(db,obs) and rainfall_service.event_is_referenced(db,event.id)
        with pytest.raises(HTTPException): rainfall_api.delete_observation(obs.id,db)
        with pytest.raises(HTTPException): rainfall_api.delete_event(event.id,db)


def test_station_with_observations_is_archived_not_deleted(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); _observation(db,station); rainfall_api.archive_station(station.id,db)
        assert db.get(RainfallStation,station.id) and db.scalar(select(RainfallObservation.id))


def test_report_html_and_pdf_include_optional_rainfall(tmp_path,monkeypatch):
    with Session(_engine(tmp_path)) as db:
        _project(db); change=_change(db); risk=_risk(db,change)
        rainfall={"source_names":["现场雨量计"],"station_count":1,"window_start":NOW-timedelta(hours=6),"window_end":NOW,"total_rainfall_mm":12.3,"rainfall_event_count":1,"observation_count":3,"peak_rainfall_rate_mm_h":5,"coverage_label":"数据覆盖完整","rule_version":"rainfall-event-v1"}
        html=risk_report_service.risk_report_html(risk,rainfall=rainfall); assert "降雨背景分析" in html and "因果关系" in html
        monkeypatch.setattr(risk_report_service,"render_html_pdf",lambda document:b"%PDF-rain" if "降雨背景分析" in document else b"")
        assert risk_report_service.risk_report_pdf(risk,rainfall=rainfall)==b"%PDF-rain"


def test_ai_reads_real_rainfall_and_distinguishes_no_data(tmp_path,monkeypatch):
    monkeypatch.setattr(rainfall_service,"get_config",lambda:_cfg(tmp_path,pre=0))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); change=_change(db); risk=_risk(db,change)
        empty=build_change_analysis_context(db,change.id)["rainfall_background"]; assert empty["available"] is False and "没有对应时间范围" in empty["message"]
        _observation(db,station,NOW,4); rainfall_service.build_change_context(db,change); rainfall_service.build_risk_context(db,risk)
        raw=json.dumps(build_risk_assessment_context(db,risk.id),ensure_ascii=False,default=str)
        assert "现场雨量计" in raw and "4.0" in raw and "rainfall-event-v1" in raw


@pytest.mark.parametrize("phrase",["不得声称降雨导致积水","不得预测未来降雨","无记录不等于没有降雨"])
def test_ai_rainfall_language_constraints(phrase):
    combined=CHANGE_ANALYSIS_GUIDE+RISK_ASSESSMENT_GUIDE+("无记录不等于没有降雨。")
    assert phrase in combined


def test_dashboard_uses_real_rainfall(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard_api,"get_model_service",lambda:SimpleNamespace(status=lambda:{})); monkeypatch.setattr(dashboard_api,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); station=_station(db); _observation(db,station,datetime.now(timezone.utc),3)
        data=dashboard_api.dashboard_summary(db)["data"]["rainfall"]
        assert data["project_name"]=="东采区" and data["projects_with_data"]==1 and data["rainfall_24h_mm"]==3


def test_legacy_database_idempotent_tables_and_capture_time(tmp_path):
    engine=create_engine(f"sqlite:///{(tmp_path/'legacy.db').as_posix()}")
    with engine.begin() as c: c.execute(text("CREATE TABLE legacy_data (id VARCHAR PRIMARY KEY, value VARCHAR)")); c.execute(text("INSERT INTO legacy_data VALUES ('1','kept')"))
    Base.metadata.create_all(engine); Base.metadata.create_all(engine)
    with engine.connect() as c:
        assert c.scalar(text("SELECT value FROM legacy_data"))=="kept"
        assert {"rainfall_stations","rainfall_observations","rainfall_events","change_rainfall_contexts","risk_rainfall_contexts"}.issubset(set(inspect(c).get_table_names()))


def test_user_visible_layer_has_no_fake_realtime_weather():
    paths=["frontend/src/views/DatasetsView.vue","frontend/src/views/CompareView.vue","frontend/src/views/RiskView.vue","frontend/src/views/DashboardView.vue"]
    source="".join(Path(x).read_text(encoding="utf-8") for x in paths)
    assert "实时天气" in source and "未来2小时暴雨预测" not in source and "天气图标" not in source
