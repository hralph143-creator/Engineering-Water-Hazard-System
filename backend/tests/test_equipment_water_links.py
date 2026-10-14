import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.api import dashboard as dashboard_api
from backend.app.api import equipment as equipment_api
from backend.app.db.database import Base
from backend.app.db.models import (
    ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentRiskLink,
    RiskAssessment, WorkArea, WorkAreaImpact,
)
from backend.app.schemas.equipment import EquipmentCreate, EquipmentUpdate, PositionCreate
from backend.app.services import equipment_service, risk_report_service
from backend.app.services.ai_context import build_risk_assessment_context
from backend.app.services.ai_prompts import RISK_ASSESSMENT_GUIDE


POLYGON = {"type":"Polygon","coordinates":[[[0,4],[2,4],[2,2],[0,2],[0,4]]]}
OVERLAP = {"type":"Polygon","coordinates":[[[0,4],[3,4],[3,1],[0,1],[0,4]]]}
MULTI = {"type":"MultiPolygon","coordinates":[[[[0,4],[1,4],[1,3],[0,3],[0,4]]],[[[3,1],[4,1],[4,0],[3,0],[3,1]]]]}


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path/'equipment.db').as_posix()}")
    @event.listens_for(engine, "connect")
    def _fk(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine); return engine


def _cfg(stale=24):
    return SimpleNamespace(equipment_position=SimpleNamespace(version="equipment-water-link-v1", position_stale_hours=stale))


def _project(db, project_id="p1", status="active"):
    row = ConstructionProject(id=project_id, project_name=f"项目{project_id}", project_code=f"CODE-{project_id}",
                              project_type="open_pit_mine", status=status)
    db.add(row); db.commit(); return row


def _equipment(db, equipment_id="e1", project_id="p1", equipment_type="excavator", lifecycle="active"):
    row = ConstructionEquipment(id=equipment_id, project_id=project_id, equipment_code=f"EQ-{equipment_id}",
                                equipment_name=f"设备{equipment_id}", equipment_type=equipment_type,
                                business_status="idle", lifecycle_status=lifecycle)
    db.add(row); db.commit(); return row


def _position(db, equipment_id="e1", project_id="p1", x=.5, y=3.5, crs="EPSG:4326", recorded_at=None, source="manual"):
    row = EquipmentPosition(id=f"pos-{equipment_id}-{x}-{y}", equipment_id=equipment_id, project_id=project_id,
                            x=x, y=y, crs=crs, position_source=source,
                            recorded_at=recorded_at or datetime.now(timezone.utc), accuracy_note="", source_note="")
    db.add(row); db.commit(); return row


def _area(db, area_id="a1", project_id="p1", geometry=POLYGON, area_code="A1"):
    row = WorkArea(id=area_id, project_id=project_id, area_name=f"区域{area_id}", area_code=area_code,
                   area_type="pit", geometry_type=geometry["type"], geometry_geojson=json.dumps(geometry),
                   crs="EPSG:4326", status="active")
    db.add(row); db.commit(); return row


def _risk(db, tmp_path, project_id="p1"):
    path = tmp_path / "zones.tif"
    values = np.array([[3,2,1,0],[3,2,1,0],[1,1,2,3],[0,1,2,3]], dtype=np.uint8)
    with rasterio.open(path,"w",driver="GTiff",width=4,height=4,count=1,dtype="uint8",crs="EPSG:4326",
                       transform=rasterio.Affine(1,0,0,0,-1,4),nodata=0) as dst: dst.write(values,1)
    row = RiskAssessment(id="r1",project_id=project_id,assessment_name="研判",source_type="change_analysis",
                         source_t1_name="T1",source_t2_name="T2",status="completed",attention_level="中水害关注",
                         rule_version="water-risk-v1",operation_note="辅助",risk_reason_codes="[]",risk_reason_text="[]",
                         inspection_advice="[]",risk_geotiff_path=str(path),completed_at=datetime.now(timezone.utc))
    db.add(row); db.commit(); return row


@pytest.mark.parametrize("table", ["construction_equipment","equipment_positions","equipment_risk_links"])
def test_equipment_tables_created(tmp_path, table):
    assert table in inspect(_engine(tmp_path)).get_table_names()


@pytest.mark.parametrize("kind", ["excavator","mining_truck"])
def test_create_supported_equipment_types(tmp_path, kind):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        payload = EquipmentCreate(project_id="p1",equipment_code=f"X-{kind}",equipment_name="机械",equipment_type=kind)
        assert equipment_api.create_equipment(payload,db)["data"]["equipment_type"] == kind


def test_equipment_code_unique_within_project(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db)
        with pytest.raises(HTTPException) as exc:
            equipment_api.create_equipment(EquipmentCreate(project_id="p1",equipment_code="EQ-e1",equipment_name="重复",equipment_type="loader"),db)
        assert exc.value.status_code == 409


def test_same_code_allowed_in_different_projects(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _project(db,"p2"); _equipment(db)
        row = ConstructionEquipment(id="e2",project_id="p2",equipment_code="EQ-e1",equipment_name="另一项目",equipment_type="loader",business_status="idle")
        db.add(row); db.commit(); assert row.id == "e2"


def test_edit_equipment(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db)
        data = equipment_api.update_equipment("e1",EquipmentUpdate(business_status="working",model="M100"),db)["data"]
        assert data["business_status"] == "working" and data["model"] == "M100"


def test_archive_equipment(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db)
        equipment_api.update_equipment("e1",EquipmentUpdate(lifecycle_status="archived"),db)
        assert db.get(ConstructionEquipment,"e1").lifecycle_status == "archived"


def test_delete_equipment_without_history(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db); equipment_api.delete_equipment("e1",db)
        assert db.get(ConstructionEquipment,"e1") is None


def test_delete_equipment_with_position_is_protected(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db); _position(db)
        with pytest.raises(HTTPException) as exc: equipment_api.delete_equipment("e1",db)
        assert exc.value.status_code == 409 and "归档" in exc.value.detail


def test_manual_position_entry(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db)
        payload = PositionCreate(x=120,y=30,crs="EPSG:4326",recorded_at=datetime.now(timezone.utc))
        data = equipment_api.add_position("e1",payload,db)["data"]
        assert data["position_source"] == "manual" and data["crs"] == "EPSG:4326"


def test_csv_position_import_reports_each_row(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db)
        content = "equipment_code,x,y,crs,recorded_at,source_note\nEQ-e1,120,30,EPSG:4326,2026-01-01T00:00:00Z,巡检\nMISSING,1,1,EPSG:4326,2026-01-01T00:00:00Z,\n"
        upload = UploadFile(file=io.BytesIO(content.encode()),filename="positions.csv")
        data = asyncio.run(equipment_api.import_positions(upload,db))["data"]
        assert data["success_count"] == 1 and data["failed_count"] == 1 and data["items"][1]["message"]


@pytest.mark.parametrize("x,y,message", [(float("nan"),1,"有限"),(181,0,"经度"),(0,91,"经度")])
def test_invalid_position_rejected(x,y,message):
    with pytest.raises(equipment_service.EquipmentError,match=message): equipment_service.validate_position(x,y,"EPSG:4326")


def test_invalid_position_crs_rejected():
    with pytest.raises(equipment_service.EquipmentError,match="空间参考"): equipment_service.validate_position(1,1,"bad-crs")


def test_projected_position_not_checked_as_longitude():
    assert equipment_service.validate_position(1000000,2000000,"EPSG:3857") == "EPSG:3857"


def test_point_reprojection():
    x,y = equipment_service.transform_point(0.00001,0.00001,"EPSG:4326","EPSG:3857")
    assert x > 1 and y > 1


@pytest.mark.parametrize("geometry,x,y,matched", [(POLYGON,.5,3.5,True),(POLYGON,3.5,.5,False),(MULTI,.5,3.5,True),(MULTI,3.5,.5,True)])
def test_point_in_polygon_and_multipolygon(geometry,x,y,matched):
    assert equipment_service.point_in_geometry(x,y,geometry) is matched


def test_multiple_area_hits_are_returned_deterministically(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db); position=_position(db); _area(db,"a2",geometry=OVERLAP,area_code="B"); _area(db,"a1",geometry=POLYGON,area_code="A")
        assert [x.id for x in equipment_service.areas_for_position(db,position)] == ["a1","a2"]


def test_project_area_isolation(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _project(db,"p2"); _equipment(db); position=_position(db); _area(db,"other","p2",OVERLAP)
        assert equipment_service.areas_for_position(db,position) == []


@pytest.mark.parametrize("x,y,expected,value", [(.5,3.5,"HIGH_ATTENTION_AREA",3),(1.5,3.5,"INSPECTION_AREA",2),(2.5,3.5,"GENERAL_WATER_AREA",1),(3.5,3.5,"NO_VALID_WATER_DATA",0),(8,8,"OUTSIDE_ANALYSIS_EXTENT",None)])
def test_raster_point_sampling(tmp_path,x,y,expected,value):
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db); position=_position(db,x=x,y=y); risk=_risk(db,tmp_path)
        assert equipment_service.sample_risk_zone(risk,position) == (expected,value)


def test_position_age_and_validity_rule():
    now=datetime.now(timezone.utc); assert 4.9 < equipment_service.position_age_hours(now-timedelta(hours=5),now) < 5.1


@pytest.mark.parametrize("hours,status", [(2,"valid"),(25,"stale")])
def test_risk_link_position_status(tmp_path,monkeypatch,hours,status):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        project=_project(db); equipment=_equipment(db); _area(db); _position(db,recorded_at=datetime.now(timezone.utc)-timedelta(hours=hours)); risk=_risk(db,tmp_path)
        row=equipment_service.create_risk_link(db,equipment,risk)
        assert row.position_status == status and ("更新时间较早" in row.advice_text) is (status=="stale")


def test_risk_link_persists_areas_and_area_impact(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); area=_area(db); _position(db); risk=_risk(db,tmp_path)
        db.add(WorkAreaImpact(id="wi",risk_assessment_id=risk.id,project_id="p1",work_area_id=area.id,impact_level="高水害影响",advice="核验")); db.commit()
        row=equipment_service.create_risk_link(db,equipment,risk)
        assert json.loads(row.work_area_ids)==["a1"] and row.work_area_impact_level=="高水害影响"


def test_repeat_risk_link_updates_same_record(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); _position(db); risk=_risk(db,tmp_path)
        first=equipment_service.create_risk_link(db,equipment,risk); second=equipment_service.create_risk_link(db,equipment,risk)
        assert first.id==second.id and len(db.scalars(select(EquipmentRiskLink)).all())==1


def test_batch_links_only_equipment_with_positions(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db); _equipment(db,"e2"); _position(db); risk=_risk(db,tmp_path)
        assert [x.equipment_id for x in equipment_service.run_project_equipment_links(db,risk)] == ["e1"]


def test_archived_equipment_not_in_batch(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); _equipment(db,lifecycle="archived"); _position(db); risk=_risk(db,tmp_path)
        assert equipment_service.run_project_equipment_links(db,risk)==[]


def test_archived_project_preserves_equipment_history(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        project=_project(db); equipment=_equipment(db); _position(db); risk=_risk(db,tmp_path); equipment_service.create_risk_link(db,equipment,risk)
        project.status="archived"; db.commit()
        assert db.get(ConstructionEquipment,"e1") and db.scalars(select(EquipmentRiskLink)).first()


def test_archived_project_rejects_new_batch(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db,status="archived"); _equipment(db); risk=_risk(db,tmp_path)
        with pytest.raises(equipment_service.EquipmentError,match="归档"): equipment_service.run_project_equipment_links(db,risk)


def test_dashboard_equipment_counts(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard_api,"get_model_service",lambda:SimpleNamespace(status=lambda:{})); monkeypatch.setattr(dashboard_api,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); position=_position(db,recorded_at=datetime.now(timezone.utc)-timedelta(hours=30)); risk=_risk(db,tmp_path)
        db.add(EquipmentRiskLink(id="l1",equipment_id=equipment.id,position_id=position.id,project_id="p1",risk_assessment_id=risk.id,recorded_at=position.recorded_at,position_age_hours=30,position_status="stale",risk_class="HIGH_ATTENTION_AREA",risk_label="高水害关注区域",reason_text="",advice_text="")); db.commit()
        assert dashboard_api.dashboard_summary(db)["data"]["equipment"] == {"active_count":1,"positioned_count":1,"high_attention_count":1,"inspection_count":0,"stale_position_count":1}


@pytest.mark.parametrize("with_devices", [False,True])
def test_report_equipment_section_is_optional(tmp_path,with_devices):
    with Session(_engine(tmp_path)) as db:
        _project(db); risk=_risk(db,tmp_path)
        links=[{"equipment_code":"EQ-1","equipment_name":"矿卡03","equipment_type_label":"矿卡","recorded_at":"2026-01-01","primary_work_area_name":"运输区","risk_label":"重点巡检区域","work_area_impact_level":"高水害影响","position_status":"valid","advice_text":"现场核验"}] if with_devices else None
        html=risk_report_service.risk_report_html(risk,equipment_links=links)
        assert ("工程机械水害关联" in html) is with_devices


def test_ai_context_reads_real_equipment_only(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); equipment.equipment_name="矿卡03"; _position(db); risk=_risk(db,tmp_path); equipment_service.create_risk_link(db,equipment,risk)
        raw=json.dumps(build_risk_assessment_context(db,risk.id),ensure_ascii=False)
        assert "矿卡03" in raw and "挖掘机99" not in raw


def test_ai_prompt_rejects_realtime_and_absolute_decisions():
    assert "不得声称设备实时在线" in RISK_ASSESSMENT_GUIDE and "可通行" in RISK_ASSESSMENT_GUIDE and "调度" in RISK_ASSESSMENT_GUIDE


def test_risk_delete_cascades_links_but_preserves_equipment(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); _position(db); risk=_risk(db,tmp_path); equipment_service.create_risk_link(db,equipment,risk)
        db.delete(risk); db.commit()
        assert db.get(ConstructionEquipment,"e1") and not db.scalars(select(EquipmentRiskLink)).all()


def test_position_referenced_by_link_cannot_be_deleted(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); position=_position(db); risk=_risk(db,tmp_path); equipment_service.create_risk_link(db,equipment,risk)
        db.delete(position)
        with pytest.raises(Exception): db.commit()


def test_rule_version_written_to_link(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); _position(db); risk=_risk(db,tmp_path)
        assert equipment_service.create_risk_link(db,equipment,risk).rule_version=="equipment-water-link-v1"


def test_equipment_link_csv_export(tmp_path,monkeypatch):
    monkeypatch.setattr(equipment_service,"get_config",lambda:_cfg())
    with Session(_engine(tmp_path)) as db:
        _project(db); equipment=_equipment(db); _position(db); risk=_risk(db,tmp_path); equipment_service.create_risk_link(db,equipment,risk)
        response=equipment_api.export_equipment_links_csv(risk.id,db)
        async def read_body():
            chunks=[]
            async for chunk in response.body_iterator: chunks.append(chunk if isinstance(chunk,bytes) else chunk.encode())
            return b"".join(chunks)
        body=asyncio.run(read_body()).decode("utf-8-sig")
        assert "equipment_code" in body and "equipment-water-link-v1" in body and "EQ-e1" in body


def test_areas_and_risk_views_expose_equipment_relationships():
    areas=open("frontend/src/views/AreasView.vue",encoding="utf-8").read(); risk=open("frontend/src/views/RiskView.vue",encoding="utf-8").read()
    assert "基于最近位置快照的关联设备" in areas and "分析工程机械水害关联" in risk


def test_legacy_database_upgrade_creates_equipment_tables_without_losing_rows(tmp_path,monkeypatch):
    from backend.app.db import database
    legacy=create_engine(f"sqlite:///{(tmp_path/'legacy-equipment.db').as_posix()}")
    with legacy.begin() as connection:
        connection.execute(text("CREATE TABLE datasets (id VARCHAR PRIMARY KEY, path TEXT, file_hash VARCHAR, project_id VARCHAR)"))
        connection.execute(text("CREATE TABLE inference_tasks (id VARCHAR PRIMARY KEY, started_at DATETIME, created_at DATETIME, batch_id VARCHAR, retry_of_task_id VARCHAR, project_id VARCHAR)"))
        connection.execute(text("CREATE TABLE inference_results (id VARCHAR PRIMARY KEY, probability_min FLOAT, probability_max FLOAT, probability_mean FLOAT, probability_median FLOAT, high_confidence_flood_pixel_count INTEGER, high_confidence_flood_ratio FLOAT)"))
        connection.execute(text("CREATE TABLE change_analyses (id VARCHAR PRIMARY KEY, created_at DATETIME, status VARCHAR, task_t1_id VARCHAR, task_t2_id VARCHAR, result_t1_id VARCHAR, result_t2_id VARCHAR, project_id VARCHAR)"))
        connection.execute(text("CREATE TABLE risk_assessments (id VARCHAR PRIMARY KEY, created_at DATETIME, status VARCHAR, source_result_id VARCHAR, source_change_analysis_id VARCHAR, project_id VARCHAR)"))
        connection.execute(text("INSERT INTO datasets (id,path) VALUES ('legacy','missing.tif')"))
    monkeypatch.setattr(database,"engine",legacy)
    Base.metadata.create_all(legacy); database.migrate_schema(); database.migrate_schema()
    with legacy.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM datasets WHERE id='legacy'"))==1
        assert {"construction_equipment","equipment_positions","equipment_risk_links"}.issubset(set(inspect(connection).get_table_names()))


def test_user_interface_avoids_false_live_gps_claim():
    paths=["frontend/src/views/EquipmentView.vue","frontend/src/views/RiskView.vue","frontend/src/views/AreasView.vue","frontend/src/views/DashboardView.vue"]
    source="".join(open(path,encoding="utf-8").read() for path in paths)
    assert "实时GPS" not in source and "位置快照" in source
