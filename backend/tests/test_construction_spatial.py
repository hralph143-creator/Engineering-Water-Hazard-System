import json
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.api import dashboard as dashboard_api
from backend.app.api import spatial as spatial_api
from backend.app.db.database import Base
from backend.app.db.models import ConstructionProject, RiskAssessment, WorkArea, WorkAreaImpact
from backend.app.schemas.spatial import ImpactRunRequest, ProjectCreate, ProjectUpdate, WorkAreaCreate
from backend.app.services import risk_report_service, spatial_service
from backend.app.services.ai_context import build_risk_assessment_context
from backend.app.services.ai_prompts import RISK_ASSESSMENT_GUIDE


POLYGON = {"type": "Polygon", "coordinates": [[[0, 4], [4, 4], [4, 0], [0, 0], [0, 4]]]}
TRIANGLE = {"type": "Polygon", "coordinates": [[[0, 4], [4, 4], [0, 0], [0, 4]]]}
MULTI = {"type": "MultiPolygon", "coordinates": [[[[0, 4], [2, 4], [2, 2], [0, 2], [0, 4]]], [[[2, 2], [4, 2], [4, 0], [2, 0], [2, 2]]]]}


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'spatial.db').as_posix()}")
    @event.listens_for(engine, "connect")
    def _fk(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine


def _cfg():
    return SimpleNamespace(work_area_impact=SimpleNamespace(
        version="work-area-impact-v1", high_attention_ratio=.25, inspection_ratio=.25
    ))


def _project(db, *, status="active"):
    row = ConstructionProject(id="project-1", project_name="西部采区工程", project_code="P-001",
                              project_type="open_pit_mine", description="", location_text="矿区西部", status=status)
    db.add(row); db.commit(); return row


def _area(db, geometry=POLYGON, *, area_id="area-1", area_type="pit", status="active", crs="EPSG:4326"):
    row = WorkArea(id=area_id, project_id="project-1", area_name="西侧采坑", area_code=f"A-{area_id}",
                   area_type=area_type, description="", geometry_type=geometry["type"],
                   geometry_geojson=json.dumps(geometry), crs=crs, status=status)
    db.add(row); db.commit(); return row


def _risk_tif(path, values=None, crs="EPSG:4326"):
    values = np.array(values if values is not None else [
        [3, 3, 2, 1], [3, 0, 2, 1], [1, 1, 1, 1], [1, 1, 1, 1]
    ], dtype=np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8", crs=crs,
                       transform=rasterio.Affine(1, 0, 0, 0, -1, 4), nodata=0) as dst:
        dst.write(values, 1)


def _risk(db, tmp_path, *, risk_id="risk-1"):
    path = tmp_path / f"{risk_id}.tif"; _risk_tif(path)
    row = RiskAssessment(id=risk_id, assessment_name="西部采区水害研判", source_type="change_analysis",
                         source_t1_name="前期", source_t2_name="后期", status="completed",
                         attention_level="中水害关注", rule_version="water-risk-v1", operation_note="辅助研判",
                         risk_reason_text="[]", risk_reason_codes="[]", inspection_advice="[]",
                         risk_geotiff_path=str(path), general_pixels=10, inspection_pixels=2,
                         high_attention_pixels=3, nodata_pixels=1)
    db.add(row); db.commit(); return row


@pytest.mark.parametrize("table", ["construction_projects", "work_areas", "work_area_impacts"])
def test_new_tables_created(tmp_path, table):
    assert table in inspect(_engine(tmp_path)).get_table_names()


def test_create_project(tmp_path):
    with Session(_engine(tmp_path)) as db:
        result = spatial_api.create_project(ProjectCreate(project_name="矿山工程", project_code="M-01", project_type="open_pit_mine"), db)
        assert result["data"]["project_name"] == "矿山工程"


def test_edit_project(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        result = spatial_api.update_project("project-1", ProjectUpdate(location_text="更新位置"), db)
        assert result["data"]["location_text"] == "更新位置"


def test_archive_project_preserves_areas(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); _area(db)
        spatial_api.archive_project("project-1", db)
        assert db.get(ConstructionProject, "project-1").status == "archived"
        assert db.get(WorkArea, "area-1") is not None


@pytest.mark.parametrize("geometry,kind", [(POLYGON, "Polygon"), (MULTI, "MultiPolygon")])
def test_supported_geometry_is_normalized(geometry, kind):
    assert spatial_service.validate_geometry(geometry)["type"] == kind


@pytest.mark.parametrize("geometry,message", [
    ({}, "仅支持"),
    ({"type":"Point", "coordinates":[0, 0]}, "仅支持"),
    ({"type":"Polygon", "coordinates":[]}, "不能为空"),
    ({"type":"Polygon", "coordinates":[[[0,0],[1,0],[1,1],[0,1]]]}, "闭合"),
    ({"type":"Polygon", "coordinates":[[[0,0],[1,0],[1,float("nan")],[0,0]]]}, "有限数值"),
])
def test_invalid_geojson_rejected(geometry, message):
    with pytest.raises(spatial_service.SpatialAnalysisError, match=message): spatial_service.validate_geometry(geometry)


@pytest.mark.parametrize("raw,expected", [("EPSG:4326", "EPSG:4326"), ("EPSG:3857", "EPSG:3857")])
def test_crs_is_validated_and_normalized(raw, expected):
    assert spatial_service.validate_crs(raw) == expected


def test_invalid_crs_rejected():
    with pytest.raises(spatial_service.SpatialAnalysisError, match="空间参考"):
        spatial_service.validate_crs("not-a-crs")


def test_create_area_persists_project_geometry_and_crs(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        payload = WorkAreaCreate(project_id="project-1", area_name="道路影响区", area_code="R-01",
                                 area_type="construction_road", geometry_geojson=MULTI, crs="EPSG:4326")
        data = spatial_api.create_area(payload, db)["data"]
        assert data["project_id"] == "project-1" and data["geometry_type"] == "MultiPolygon" and data["crs"] == "EPSG:4326"


def test_archived_project_rejects_new_area(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db, status="archived")
        payload = WorkAreaCreate(project_id="project-1", area_name="区域", area_code="A", area_type="other", geometry_geojson=POLYGON, crs="EPSG:4326")
        with pytest.raises(HTTPException) as exc: spatial_api.create_area(payload, db)
        assert exc.value.status_code == 409


def test_polygon_mask_uses_real_shape_and_counts_nodata(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); area = _area(db, TRIANGLE); risk = _risk(db, tmp_path)
        result = spatial_service.analyze_area(risk, area, _cfg())
        assert result["total_valid_pixels"] < 15
        assert result["nodata_pixels"] == 1
        assert result["general_pixels"] + result["inspection_pixels"] + result["high_attention_pixels"] == result["total_valid_pixels"]
        assert result["general_ratio"] + result["inspection_ratio"] + result["high_attention_ratio"] == pytest.approx(1)


def test_crs_transformation_is_applied(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        tiny = {"type":"Polygon", "coordinates":[[[0,0.00004],[0.00004,0.00004],[0.00004,0],[0,0],[0,0.00004]]]}
        area = _area(db, tiny, crs="EPSG:4326")
        path = tmp_path / "mercator.tif"; _risk_tif(path, crs="EPSG:3857")
        risk = _risk(db, tmp_path); risk.risk_geotiff_path = str(path); db.commit()
        assert spatial_service.analyze_area(risk, area, _cfg())["total_valid_pixels"] > 0


@pytest.mark.parametrize("high,inspection,expected", [
    (.25, 0, "高水害影响"), (.01, 0, "中水害影响"), (0, .25, "中水害影响"), (0, .24, "低水害影响")
])
def test_impact_level_rules(high, inspection, expected):
    assert spatial_service._impact_level(high, inspection, _cfg().work_area_impact) == expected


def test_analysis_uses_configured_rule_version_and_cautious_advice(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); area = _area(db); risk = _risk(db, tmp_path)
        result = spatial_service.analyze_area(risk, area, _cfg())
        assert result["algorithm_version"] == "work-area-impact-v1"
        assert "不代表" in spatial_service.impact_advice(area, "低水害影响")


def test_impacts_persist_link_and_repeat_updates_same_row(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_service, "get_config", _cfg)
    with Session(_engine(tmp_path)) as db:
        project = _project(db); area = _area(db); risk = _risk(db, tmp_path)
        first = spatial_service.run_project_impacts(db, risk, project)
        second = spatial_service.run_project_impacts(db, risk, project)
        assert first[0].id == second[0].id
        assert db.scalars(select(WorkAreaImpact)).all()[0].work_area_id == area.id
        assert risk.project_id == project.id


def test_impact_history_query_uses_real_area_name(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_service, "get_config", _cfg)
    with Session(_engine(tmp_path)) as db:
        project = _project(db); _area(db); risk = _risk(db, tmp_path)
        spatial_service.run_project_impacts(db, risk, project)
        data = spatial_api.area_impacts("area-1", db)["data"]
        assert data[0]["area_name"] == "西侧采坑"


def test_work_area_with_impact_cannot_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_service, "get_config", _cfg)
    with Session(_engine(tmp_path)) as db:
        project = _project(db); _area(db); risk = _risk(db, tmp_path)
        spatial_service.run_project_impacts(db, risk, project)
        with pytest.raises(HTTPException) as exc: spatial_api.delete_area("area-1", db)
        assert exc.value.status_code == 409 and "归档" in exc.value.detail


def test_deleting_risk_does_not_delete_work_area(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_service, "get_config", _cfg)
    with Session(_engine(tmp_path)) as db:
        project = _project(db); _area(db); risk = _risk(db, tmp_path)
        spatial_service.run_project_impacts(db, risk, project)
        db.delete(risk); db.commit()
        assert db.get(WorkArea, "area-1") is not None
        assert not db.scalars(select(WorkAreaImpact)).all()


@pytest.mark.parametrize("with_impacts", [False, True])
def test_report_optional_area_section(tmp_path, with_impacts):
    with Session(_engine(tmp_path)) as db:
        risk = _risk(db, tmp_path)
        impacts = [{"area_name":"西侧采坑", "area_type_label":"采坑", "high_attention_ratio":.2,
                    "inspection_ratio":.1, "general_ratio":.7, "impact_level":"高水害影响", "advice":"现场核验"}] if with_impacts else None
        html = risk_report_service.risk_report_html(risk, impacts, "西部采区工程")
        assert ("施工区域影响分析" in html) is with_impacts


def test_ai_context_contains_only_persisted_project_and_area(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_service, "get_config", _cfg)
    with Session(_engine(tmp_path)) as db:
        project = _project(db); _area(db); risk = _risk(db, tmp_path)
        spatial_service.run_project_impacts(db, risk, project)
        context = build_risk_assessment_context(db, risk.id)
        raw = json.dumps(context, ensure_ascii=False, default=str)
        assert "西部采区工程" in raw and "西侧采坑" in raw and "东侧运输道路" not in raw


def test_ai_prompt_forbids_inventing_or_reclassifying():
    assert "不存在" in RISK_ASSESSMENT_GUIDE and "重新分类" in RISK_ASSESSMENT_GUIDE


def test_dashboard_uses_real_construction_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_api, "get_model_service", lambda: SimpleNamespace(status=lambda: {}))
    with Session(_engine(tmp_path)) as db:
        _project(db); _area(db); risk = _risk(db, tmp_path)
        db.add(WorkAreaImpact(id="impact-1", risk_assessment_id=risk.id, project_id="project-1", work_area_id="area-1",
                              impact_level="高水害影响", advice="核验")); db.commit()
        data = dashboard_api.dashboard_summary(db)["data"]["construction"]
        assert data == {"active_projects":1, "active_work_areas":1, "high_impact_areas":1, "attention_impact_areas":0}


def test_nullable_project_associations_exist():
    for table in ("datasets", "inference_tasks", "change_analyses", "risk_assessments"):
        assert Base.metadata.tables[table].c.project_id.nullable


def test_legacy_database_migration_is_idempotent_and_preserves_rows(tmp_path, monkeypatch):
    from backend.app.db import database
    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with legacy.begin() as connection:
        connection.execute(text("CREATE TABLE datasets (id VARCHAR PRIMARY KEY, path TEXT)"))
        connection.execute(text("CREATE TABLE inference_tasks (id VARCHAR PRIMARY KEY, started_at DATETIME)"))
        connection.execute(text("CREATE TABLE inference_results (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE change_analyses (id VARCHAR PRIMARY KEY, created_at DATETIME, status VARCHAR, task_t1_id VARCHAR, task_t2_id VARCHAR, result_t1_id VARCHAR, result_t2_id VARCHAR)"))
        connection.execute(text("CREATE TABLE risk_assessments (id VARCHAR PRIMARY KEY, created_at DATETIME, status VARCHAR, source_result_id VARCHAR, source_change_analysis_id VARCHAR)"))
        connection.execute(text("INSERT INTO datasets (id,path) VALUES ('old-data','missing.tif')"))
    monkeypatch.setattr(database, "engine", legacy)
    database.migrate_schema(); database.migrate_schema()
    with legacy.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM datasets WHERE id='old-data'")) == 1
        inspector = inspect(connection)
        for table in ("datasets", "inference_tasks", "change_analyses", "risk_assessments"):
            assert "project_id" in {column["name"] for column in inspector.get_columns(table)}


def test_ui_never_labels_low_water_impact_as_safe():
    source = open("frontend/src/views/AreasView.vue", encoding="utf-8").read() + open("frontend/src/views/RiskView.vue", encoding="utf-8").read()
    assert "作业区域水害空间影响" in source and "安全区域" not in source
