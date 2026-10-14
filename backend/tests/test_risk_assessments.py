import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import change_analyses as change_api
from backend.app.api import risk_assessments as api
from backend.app.db.database import Base
from backend.app.db.models import ChangeAnalysis, RiskAssessment
from backend.app.schemas.risk_assessment import RiskAssessmentCreate
from backend.app.services import risk_report_service, risk_service
from backend.app.services.ai_context import build_risk_assessment_context
from backend.app.services.ai_prompts import RISK_ASSESSMENT_GUIDE


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'risk.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _change_tif(path):
    values = np.array([[0, 1, 2], [3, 4, 1]], dtype=np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=3, height=2, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=rasterio.Affine(1, 0, 0, 0, -1, 2)) as dst:
        dst.write(values, 1)


def _change(db, tmp_path, *, status="completed", with_tif=True, **ratios):
    tif = tmp_path / "change.tif"
    if with_tif: _change_tif(tif)
    defaults = dict(t2_water_ratio=.12, new_water_ratio=.06, persistent_water_ratio=.03,
                    receded_water_ratio=.02, net_change_ratio=.04)
    defaults.update(ratios)
    row = ChangeAnalysis(
        id="change-1", name="变化分析", task_t1_id="t1", task_t2_id="t2",
        result_t1_id="r1", result_t2_id="r2", dataset_t1_id="d1", dataset_t2_id="d2",
        dataset_t1_name="前期.tif", dataset_t2_name="后期.tif",
        model_t1_name="U-Net", model_t2_name="U-Net", threshold_t1=.5, threshold_t2=.5,
        status=status, spatial_consistent=status == "completed", algorithm_version="pixel-change-v1",
        change_geotiff_path=str(tif), change_png_path=str(tmp_path / "change.png"), valid_pixels=5,
        new_water_pixels=2, persistent_water_pixels=1, receded_water_pixels=1,
        stable_non_water_pixels=1, **defaults,
    )
    db.add(row); db.commit(); return row


@pytest.fixture
def configured(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"; outputs.mkdir()
    rules = SimpleNamespace(version="water-risk-v1", new_attention=.01, new_high=.05,
                            persistent_attention=.02, persistent_high=.08,
                            net_expansion=.01, net_reduction=-.01)
    cfg = SimpleNamespace(paths=SimpleNamespace(outputs=outputs), risk=SimpleNamespace(medium=.08, high=.2), risk_rules=rules)
    monkeypatch.setattr(risk_service, "get_config", lambda: cfg)
    return cfg


def test_risk_table_created(tmp_path):
    _engine(tmp_path)
    assert "risk_assessments" in Base.metadata.tables


def test_normal_create_persists(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path)
        row = risk_service.create_risk_assessment(db, "change-1", "正式研判")
        assert row.status == "completed" and row.assessment_name == "正式研判"
        assert db.get(RiskAssessment, row.id) is not None


def test_missing_source_rejected(tmp_path, configured):
    with Session(_engine(tmp_path)) as db, pytest.raises(risk_service.RiskAssessmentError, match="不存在"):
        risk_service.create_risk_assessment(db, "missing")


def test_failed_source_rejected(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path, status="failed")
        with pytest.raises(risk_service.RiskAssessmentError, match="已完成"):
            risk_service.create_risk_assessment(db, "change-1")


def test_missing_change_geotiff_rejected(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path, with_tif=False)
        with pytest.raises(risk_service.RiskAssessmentError, match="GeoTIFF"):
            risk_service.create_risk_assessment(db, "change-1")


@pytest.mark.parametrize("values,expected", [
    ({"t2_water_ratio":.01,"new_water_ratio":0,"persistent_water_ratio":0,"net_change_ratio":0}, "低水害关注"),
    ({"t2_water_ratio":.1,"new_water_ratio":.02,"persistent_water_ratio":0,"net_change_ratio":0}, "中水害关注"),
    ({"t2_water_ratio":.21,"new_water_ratio":0,"persistent_water_ratio":0,"net_change_ratio":0}, "高水害关注"),
])
def test_attention_levels_are_rule_based(tmp_path, configured, values, expected):
    with Session(_engine(tmp_path)) as db:
        row = _change(db, tmp_path, **values)
        assert risk_service.evaluate_rules(row, configured)["attention_level"] == expected


@pytest.mark.parametrize("field,value,code", [
    ("new_water_ratio", .02, "NEW_WATER_DETECTED"),
    ("persistent_water_ratio", .03, "PERSISTENT_WATER_PRESENT"),
    ("receded_water_ratio", .03, "WATER_RECEDING"),
    ("net_change_ratio", .02, "NET_EXPANSION"),
])
def test_reason_codes_trigger(tmp_path, configured, field, value, code):
    with Session(_engine(tmp_path)) as db:
        row = _change(db, tmp_path, **{field:value})
        assert code in risk_service.evaluate_rules(row, configured)["reason_codes"]


def test_receded_water_generates_inspection_advice(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        row = _change(db, tmp_path)
        advice = "".join(risk_service.evaluate_rules(row, configured)["inspection_advice"])
        assert "消退区域" in advice and "承载" in advice


def test_general_area_is_not_called_safe(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path)
        row = risk_service.create_risk_assessment(db, "change-1")
        report = risk_report_service.risk_report_html(row)
        assert "一般区域" in report and "不代表绝对安全" in report
        assert "安全作业区" not in report


def test_generates_png_and_geotiff(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        assert Path(row.risk_png_path).is_file() and Path(row.risk_geotiff_path).is_file()


def test_zone_classification_values_and_nodata(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        with rasterio.open(row.risk_geotiff_path) as src:
            values = src.read(1); nodata = src.nodata
        assert values.tolist() == [[1, 3, 2], [3, 0, 3]]
        assert nodata == 0 and row.nodata_pixels == 1
        assert row.high_attention_pixels == 3 and row.inspection_pixels == 1 and row.general_pixels == 1


def test_summary_json_and_rule_version_saved(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        data = json.loads(Path(row.summary_json_path).read_text(encoding="utf-8"))
        assert data["rule_version"] == "water-risk-v1" == row.rule_version


def test_api_list_and_detail(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path)
        made = api.create(RiskAssessmentCreate(source_change_analysis_id="change-1"), db=db)["data"]
        assert api.list_all(db=db, status=None, limit=100)["data"][0]["id"] == made["id"]
        assert api.detail(made["id"], db=db)["data"]["attention_level"] == "高水害关注"


def test_delete_own_artifacts_not_change_source(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        change = _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        out = Path(row.risk_png_path).parent; rid = row.id
        risk_service.delete_risk_assessment(db, row)
        assert not out.exists() and db.get(RiskAssessment, rid) is None
        assert db.get(ChangeAnalysis, change.id) is not None and Path(change.change_geotiff_path).is_file()


def test_change_delete_reference_protection(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); risk_service.create_risk_assessment(db, "change-1")
        with pytest.raises(HTTPException) as raised: change_api.remove("change-1", db=db)
        assert raised.value.status_code == 409
        assert raised.value.detail == "该时空变化分析已被风险研判记录引用，请先删除相关风险研判记录。"


def test_html_report(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        html = risk_report_service.risk_report_html(row)
        assert "施工区域水害风险辅助研判报告" in html and "道路承载评价" in html


def test_pdf_report_uses_shared_renderer(tmp_path, configured, monkeypatch):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        monkeypatch.setattr(risk_report_service, "render_html_pdf", lambda html: b"%PDF-risk" if "关注等级" in html else b"")
        assert risk_report_service.risk_report_pdf(row) == b"%PDF-risk"


def test_csv_export_contains_required_fields(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        response = api.export_csv(row.id, db=db)
        assert response.media_type == "text/csv"
        assert "risk_" in response.headers["content-disposition"]


def test_ai_context_reads_formal_rule_result(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        context = build_risk_assessment_context(db, row.id)
        assert context["formal_rule_result"]["attention_level"] == row.attention_level
        assert context["formal_rule_result"]["rule_version"] == "water-risk-v1"


def test_ai_instruction_forbids_overriding_level():
    assert "严禁修改、覆盖、重新分类" in RISK_ASSESSMENT_GUIDE
    assert "根据系统当前遥感水害结果" in RISK_ASSESSMENT_GUIDE


def test_user_visible_terms_have_no_unsafe_general_zone_claim():
    root = Path(__file__).parents[2]
    visible = (root / "frontend/src/views/RiskView.vue").read_text(encoding="utf-8")
    assert "一般区域" in visible and "不代表安全作业区" in visible
    assert "正常作业区" not in visible and "绝对安全区域" not in visible


def test_risk_artifact_directory_layout(tmp_path, configured):
    with Session(_engine(tmp_path)) as db:
        _change(db, tmp_path); row = risk_service.create_risk_assessment(db, "change-1")
        folder = configured.paths.outputs / "risks" / row.id
        assert {p.name for p in folder.iterdir()} == {"risk_zone.png", "risk_zone.tif", "summary.json"}


def test_database_metadata_keeps_original_tables_and_adds_risk():
    names = set(Base.metadata.tables)
    assert {"datasets", "inference_tasks", "inference_results", "change_analyses", "risk_assessments"} <= names
