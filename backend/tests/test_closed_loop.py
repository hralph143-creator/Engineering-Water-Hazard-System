"""业务闭环高价值测试。

覆盖：概率统计正确性、旧结果 NULL 兼容、文件缺失检测、任务筛选、
Settings 校验、空间一致/不一致对比、变化分类、Dashboard 最新结果、HTML 报告、迁移幂等。
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import dashboard as dashboard_api
from backend.app.api import settings as settings_api
from backend.app.api import tasks as tasks_api
from backend.app import config as config_module
from backend.app.api.helpers import result_file_status, task_to_dict
from backend.app.db import database as database_module
from backend.app.db.database import Base, migrate_schema
from backend.app.db.models import DatasetRecord, InferenceResult, InferenceTask
from backend.app.services.compare_service import (
    SpatialMismatchError,
    change_analysis,
    spatial_consistent,
)
from backend.app.services.model_service import compute_probability_statistics
from backend.app.services.report_service import generate_report_html


def _make_mask_tif(path, data, transform):
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:4326", transform=transform, nodata=None,
    ) as dst:
        dst.write(data.astype(np.uint8), 1)


def _result_obj(mask_path):
    return SimpleNamespace(
        mask_path=str(mask_path),
        probability_path=str(mask_path),
        comparison_path=str(mask_path),
    )


def test_probability_stats_only_valid_pixels():
    prob = np.array([[0.1, np.nan], [0.9, np.inf]], dtype=np.float32)
    mask = np.array([[0, 1], [1, 0]], dtype=np.uint8)
    valid = np.isfinite(prob)
    s = compute_probability_statistics(prob, mask, valid, 0.5)
    assert s["min"] == pytest.approx(0.1)
    assert s["max"] == pytest.approx(0.9)
    assert s["mean"] == pytest.approx(0.5)
    assert s["median"] == pytest.approx(0.5)
    assert s["flood_count"] == 1
    assert s["mean_confidence"] == pytest.approx(0.9)


def test_probability_stats_high_conf_threshold():
    prob = np.array([[0.7, 0.9, 0.85]], dtype=np.float32)
    mask = np.array([[1, 1, 1]], dtype=np.uint8)
    valid = np.ones_like(prob, dtype=bool)
    s = compute_probability_statistics(prob, mask, valid, 0.5)
    assert s["flood_count"] == 3
    assert s["high_confidence_flood_pixel_count"] == 2
    assert s["high_confidence_flood_ratio"] == pytest.approx(2 / 3)
    # 阈值 0.9 时，高置信阈值为 max(0.9, 0.8)=0.9
    s2 = compute_probability_statistics(prob, mask, valid, 0.9)
    assert s2["high_confidence_flood_pixel_count"] == 1


def test_result_file_status_detection(tmp_path):
    base = tmp_path / "out"
    base.mkdir()
    r = _result_obj(base / "mask.tif")
    assert result_file_status(r)["status"] == "missing"
    (base / "mask.tif").write_bytes(b"x")
    st = result_file_status(r)
    assert st["status"] == "partial"
    assert "probability.tif" in st["missing_files"]
    for name in ["probability.tif", "mask.tif", "comparison.png", "vv.png",
                 "vh.png", "probability.png", "mask.png"]:
        (base / name).write_bytes(b"x")
    st = result_file_status(r)
    assert st["status"] == "complete" and not st["missing_files"]


def test_task_to_dict_null_compat(tmp_path):
    result = SimpleNamespace(
        id="r1", flood_pixel_count=1, valid_pixel_count=10, flood_ratio=0.1,
        mean_confidence=0.9, risk_level="安全", inference_seconds=1.0,
        created_at=None, probability_min=None, probability_max=None,
        probability_mean=None, probability_median=None,
        high_confidence_flood_pixel_count=None, high_confidence_flood_ratio=None,
        probability_path="", mask_path="",
        comparison_path=str(tmp_path / "missing" / "x.png"),
    )
    task = SimpleNamespace(
        id="t1", dataset_id="d1", dataset=SimpleNamespace(name="a.tif"),
        status="completed", threshold=0.5, created_at=None, batch_id=None,
        retry_of_task_id=None, started_at=None, finished_at=None,
        checkpoint="E:/f/checkpoints/best_model.pt", model_name="U-Net",
        error_message=None, result=result,
    )
    d = task_to_dict(task)
    assert d["result"]["probability_min"] is None
    assert d["result"]["high_confidence_flood_ratio"] is None
    assert d["result"]["file_status"]["status"] == "missing"
    assert d["dataset_name"] == "a.tif"


def _seed_tasks_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'f.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        d1 = DatasetRecord(id="d1", name="Bolivia_A.tif", path=str(tmp_path / "a.tif"),
                           width=10, height=10, bands=2, dtype="float32", status="valid")
        db.add(d1)
        db.flush()
        t1 = InferenceTask(id="t1", dataset_id="d1", status="completed", threshold=0.5,
                           checkpoint="x", model_name="U-Net",
                           created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        t2 = InferenceTask(id="t2", dataset_id="d1", status="failed", threshold=0.5,
                           checkpoint="x", model_name="U-Net",
                           created_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        db.add_all([t1, t2])
        db.flush()
        r1 = InferenceResult(id="r1", task_id="t1", probability_path="", mask_path="",
                             comparison_path="", flood_pixel_count=1, valid_pixel_count=10,
                             flood_ratio=0.1, mean_confidence=0.9, risk_level="安全",
                             inference_seconds=1.0,
                             created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        r2 = InferenceResult(id="r2", task_id="t2", probability_path="", mask_path="",
                             comparison_path="", flood_pixel_count=5, valid_pixel_count=10,
                             flood_ratio=0.5, mean_confidence=0.8, risk_level="中风险",
                             inference_seconds=1.0,
                             created_at=datetime(2026, 2, 2, tzinfo=timezone.utc))
        db.add_all([r1, r2])
        db.commit()
    return engine


def test_tasks_status_filter(tmp_path):
    engine = _seed_tasks_db(tmp_path)
    with Session(engine) as db:
        rows = tasks_api.list_tasks(status="completed", db=db)["data"]
    assert len(rows) == 1 and rows[0]["id"] == "t1"


def test_tasks_name_filter(tmp_path):
    engine = _seed_tasks_db(tmp_path)
    with Session(engine) as db:
        rows = tasks_api.list_tasks(dataset_name="Bolivia", db=db)["data"]
    assert len(rows) == 2


def test_tasks_date_filter(tmp_path):
    engine = _seed_tasks_db(tmp_path)
    with Session(engine) as db:
        rows = tasks_api.list_tasks(date_from=datetime(2026, 1, 15, tzinfo=timezone.utc), db=db)["data"]
        assert [r["id"] for r in rows] == ["t2"]
        rows = tasks_api.list_tasks(date_to=datetime(2026, 1, 15, tzinfo=timezone.utc), db=db)["data"]
        assert [r["id"] for r in rows] == ["t1"]


def test_settings_threshold_validation(monkeypatch):
    cfg = SimpleNamespace(model=SimpleNamespace(threshold=0.5),
                          risk=SimpleNamespace(low=0.02, medium=0.08, high=0.2))
    monkeypatch.setattr(settings_api, "get_config", lambda: cfg)
    with pytest.raises(HTTPException):
        settings_api.update_settings(settings_api.SettingsUpdate(threshold=1.5))


def test_settings_risk_order_validation(monkeypatch):
    cfg = SimpleNamespace(model=SimpleNamespace(threshold=0.5),
                          risk=SimpleNamespace(low=0.02, medium=0.08, high=0.2))
    monkeypatch.setattr(settings_api, "get_config", lambda: cfg)
    with pytest.raises(HTTPException):
        settings_api.update_settings(settings_api.SettingsUpdate(low=0.5, medium=0.3))
    with pytest.raises(HTTPException):
        settings_api.update_settings(settings_api.SettingsUpdate(high=0.0))


def test_settings_valid_update_calls_save(monkeypatch):
    cfg = SimpleNamespace(model=SimpleNamespace(threshold=0.5),
                          risk=SimpleNamespace(low=0.02, medium=0.08, high=0.2))
    monkeypatch.setattr(settings_api, "get_config", lambda: cfg)
    monkeypatch.setattr(settings_api, "_settings_payload", lambda c: {"ok": True})
    monkeypatch.setattr(settings_api, "get_llm_config", lambda: config_module.LLMConfig())
    monkeypatch.setattr(settings_api, "save_local_llm_settings", lambda **kw: None)
    saved = {}
    monkeypatch.setattr(settings_api, "save_editable_settings",
                        lambda **kw: saved.update(kw))
    settings_api.update_settings(settings_api.SettingsUpdate(threshold=0.6, low=0.03))
    assert saved["threshold"] == 0.6 and saved["low"] == 0.03


def test_settings_path_update_calls_save(monkeypatch):
    cfg = SimpleNamespace(model=SimpleNamespace(threshold=0.5),
                          risk=SimpleNamespace(low=0.02, medium=0.08, high=0.2))
    monkeypatch.setattr(settings_api, "get_config", lambda: cfg)
    monkeypatch.setattr(settings_api, "_settings_payload", lambda c: {"ok": True})
    monkeypatch.setattr(settings_api, "get_llm_config", lambda: config_module.LLMConfig())
    monkeypatch.setattr(settings_api, "save_local_llm_settings", lambda **kw: None)
    saved = {}
    monkeypatch.setattr(settings_api, "save_editable_settings", lambda **kw: saved.update(kw))
    settings_api.update_settings(settings_api.SettingsUpdate(
        model_checkpoint="E:/models/flood model.pt",
        sar_stats="E:/models/sar stats.json",
        output_directory="E:/flood outputs",
        upload_directory="E:/flood uploads",
        database_path="E:/flood data/floodrisk.db",
    ))
    assert saved["model_checkpoint"] == "E:/models/flood model.pt"
    assert saved["output_directory"] == "E:/flood outputs"
    assert saved["database_path"].endswith("floodrisk.db")


def test_settings_ai_key_is_write_only(monkeypatch):
    cfg = SimpleNamespace(model=SimpleNamespace(threshold=0.5),
                          risk=SimpleNamespace(low=0.02, medium=0.08, high=0.2))
    monkeypatch.setattr(settings_api, "get_config", lambda: cfg)
    monkeypatch.setattr(settings_api, "get_llm_config", lambda: config_module.LLMConfig())
    monkeypatch.setattr(settings_api, "save_editable_settings", lambda **kw: None)
    monkeypatch.setattr(settings_api, "_settings_payload", lambda c: {"ai": {"key_configured": True}})
    saved = {}
    monkeypatch.setattr(settings_api, "save_local_llm_settings", lambda **kw: saved.update(kw))
    response = settings_api.update_settings(settings_api.SettingsUpdate(
        ai_enabled=True, ai_base_url="https://example.com/v1", ai_model="model-x",
        ai_api_key="write-only-secret",
    ))
    assert saved["api_key"] == "write-only-secret"
    assert "write-only-secret" not in json.dumps(response)


def test_settings_rejects_wrong_path_suffix():
    with pytest.raises(HTTPException):
        settings_api.update_settings(settings_api.SettingsUpdate(model_checkpoint="E:/models/model.txt"))


def test_save_editable_paths_quotes_spaces(tmp_path, monkeypatch):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "model:\n  checkpoint: old.pt\n  sar_stats: old.json\n  threshold: 0.5\n"
        "paths:\n  uploads: old_uploads\n  outputs: old_outputs\n  database: old.db\n"
        "risk:\n  low: 0.02\n  medium: 0.08\n  high: 0.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
    config_module.save_editable_settings(
        model_checkpoint="E:/models/flood model.pt",
        output_directory="E:/flood outputs",
    )
    content = config_path.read_text(encoding="utf-8")
    assert 'checkpoint: "E:/models/flood model.pt"' in content
    assert 'outputs: "E:/flood outputs"' in content


def test_compare_change_classification(tmp_path):
    a = np.array([[0, 0, 1, 1], [0, 1, 1, 0], [0, 0, 0, 1]], dtype=np.uint8)
    b = np.array([[1, 0, 1, 1], [0, 0, 1, 0], [1, 1, 0, 1]], dtype=np.uint8)
    t = rasterio.Affine(1, 0, 0, 0, -1, 3)
    pa, pb = tmp_path / "a.tif", tmp_path / "b.tif"
    _make_mask_tif(pa, a, t)
    _make_mask_tif(pb, b, t)
    assert spatial_consistent(_result_obj(pa), _result_obj(pb))
    res = change_analysis(_result_obj(pa), _result_obj(pb), tmp_path / "out")
    assert res["counts"]["new_flood_pixels"] == int(((a == 0) & (b == 1)).sum())
    assert res["counts"]["recession_pixels"] == int(((a == 1) & (b == 0)).sum())
    assert res["counts"]["persistent_pixels"] == int(((a == 1) & (b == 1)).sum())
    assert (tmp_path / "out" / "change_mask.tif").is_file()
    assert (tmp_path / "out" / "change_mask.png").is_file()


def test_compare_spatial_mismatch_rejected(tmp_path):
    a = np.zeros((3, 3), dtype=np.uint8)
    b = np.zeros((3, 3), dtype=np.uint8)
    pa, pb = tmp_path / "a.tif", tmp_path / "b.tif"
    _make_mask_tif(pa, a, rasterio.Affine(1, 0, 0, 0, -1, 3))
    _make_mask_tif(pb, b, rasterio.Affine(2, 0, 0, 0, -1, 3))
    assert not spatial_consistent(_result_obj(pa), _result_obj(pb))
    with pytest.raises(SpatialMismatchError):
        change_analysis(_result_obj(pa), _result_obj(pb), tmp_path / "out")


def test_dashboard_latest_is_recent_completed(tmp_path, monkeypatch):
    engine = _seed_tasks_db(tmp_path)
    monkeypatch.setattr(dashboard_api, "get_model_service",
                        lambda: SimpleNamespace(status=lambda: {}))
    with Session(engine) as db:
        d = dashboard_api.dashboard_summary(db)["data"]
        assert d["latest_flood_ratio"] == 0.1
        assert d["current_risk_level"] == "安全"
    # 追加更新的 completed 结果（晚于 pending 任务），latest 必须取它
    with Session(engine, expire_on_commit=False) as db:
        t3 = InferenceTask(id="t3", dataset_id="d1", status="pending", threshold=0.5,
                           checkpoint="x", model_name="U-Net",
                           created_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        t4 = InferenceTask(id="t4", dataset_id="d1", status="completed", threshold=0.5,
                           checkpoint="x", model_name="U-Net",
                           created_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
        db.add_all([t3, t4])
        db.flush()
        r4 = InferenceResult(id="r4", task_id="t4", probability_path="", mask_path="",
                             comparison_path="", flood_pixel_count=5, valid_pixel_count=10,
                             flood_ratio=0.5, mean_confidence=0.8, risk_level="中风险",
                             inference_seconds=1.0,
                             created_at=datetime(2026, 4, 2, tzinfo=timezone.utc))
        db.add(r4)
        db.commit()
    with Session(engine) as db:
        d = dashboard_api.dashboard_summary(db)["data"]
        assert d["latest_flood_ratio"] == 0.5
        assert d["current_risk_level"] == "中风险"


def test_report_html_generation(tmp_path):
    result = SimpleNamespace(
        id="r1", valid_pixel_count=10, flood_pixel_count=2, flood_ratio=0.2,
        mean_confidence=0.9, inference_seconds=1.0, probability_min=0.1,
        probability_max=0.95, probability_mean=0.5, probability_median=0.48,
        high_confidence_flood_pixel_count=1, high_confidence_flood_ratio=0.5,
        risk_level="低风险",
        comparison_path=str(tmp_path / "out" / "comparison.png"),
        task=SimpleNamespace(
            checkpoint="E:/floodrisk/checkpoints/best_model.pt", model_name="U-Net",
            threshold=0.5, finished_at=None,
            dataset=SimpleNamespace(name="a.tif", source_type="Sentinel-1 SAR",
                                   width=10, height=10, bands=2, crs="EPSG:4326"),
        ),
    )
    html = generate_report_html(result)
    assert "积水占比" in html and "20.00%" in html
    assert "基础水害提示" in html and "低风险" in html
    assert "不替代现场勘察、工程安全鉴定及施工单位正式安全决策" in html
    assert "a.tif" in html and "数据与识别参数" in html
    assert "best_model.pt" not in html and "U-Net" in html
    assert "模型配置" not in html and "预测结果" not in html


def test_migration_idempotent(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database_module, "engine", engine)
    migrate_schema()
    migrate_schema()  # 第二次调用不得报错
    import sqlite3
    conn = sqlite3.connect((tmp_path / "m.db").as_posix())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inference_results)")}
    assert {"probability_min", "probability_max", "probability_mean", "probability_median",
            "high_confidence_flood_pixel_count", "high_confidence_flood_ratio"} <= cols
    conn.close()
