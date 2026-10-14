from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import change_analyses as api
from backend.app.api import results as results_api
from backend.app.db.database import Base
from backend.app.db.models import ChangeAnalysis, DatasetRecord, InferenceResult, InferenceTask
from backend.app.schemas.change_analysis import ChangeAnalysisCreate
from backend.app.services import change_analysis_service as service
from backend.app.services.ai_context import AIContextError, build_change_analysis_context
from backend.app.services.change_report_service import generate_change_report_html
from backend.app.services import change_report_service


def _mask(path: Path, values, *, x=0):
    values = np.asarray(values, dtype=np.uint8)
    with rasterio.open(
        path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
        count=1, dtype="uint8", crs="EPSG:4326",
        transform=rasterio.Affine(1, 0, x, 0, -1, values.shape[0]),
    ) as dst:
        dst.write(values, 1)


def _database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'changes.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _seed(db: Session, tmp_path, *, mismatch=False):
    arrays = ([[0, 1], [1, 0]], [[1, 1], [0, 0]])
    for index, values in enumerate(arrays, 1):
        mask = tmp_path / f"mask-{index}.tif"
        _mask(mask, values, x=10 if mismatch and index == 2 else 0)
        dataset = DatasetRecord(
            id=f"d{index}", name=f"scene-{index}.tif", path=str(tmp_path / f"source-{index}.tif"),
            width=2, height=2, bands=2, dtype="float32", crs="EPSG:4326",
        )
        task = InferenceTask(
            id=f"t{index}", dataset=dataset, status="completed", threshold=0.5,
            checkpoint="model.pt", model_name="U-Net",
            created_at=datetime(2026, 1, index, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, index, 1, tzinfo=timezone.utc),
        )
        result = InferenceResult(
            id=f"r{index}", task=task, probability_path=str(mask), mask_path=str(mask),
            comparison_path=str(tmp_path / f"out-{index}" / "comparison.png"),
            flood_pixel_count=2, valid_pixel_count=4, flood_ratio=0.5,
            mean_confidence=0.8, risk_level="低风险", inference_seconds=1.0,
        )
        db.add_all([dataset, task, result])
    db.commit()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(service, "get_config", lambda: SimpleNamespace(paths=SimpleNamespace(outputs=outputs)))
    return outputs


def test_change_table_is_created(tmp_path):
    engine = _database(tmp_path)
    assert "change_analyses" in Base.metadata.tables
    with Session(engine) as db:
        assert db.query(ChangeAnalysis).count() == 0


def test_create_persists_four_classes_and_files(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2", name="测试变化")
        assert row.status == "completed" and row.spatial_consistent is True
        assert row.new_water_pixels == 1 and row.receded_water_pixels == 1
        assert row.persistent_water_pixels == 1 and row.stable_non_water_pixels == 1
        assert row.valid_pixels == 4
        assert Path(row.change_png_path).is_file() and Path(row.change_geotiff_path).is_file()
        assert (configured / "changes" / row.id / "summary.json").is_file()


def test_create_uses_source_snapshots(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        assert row.dataset_t1_name == "scene-1.tif"
        assert row.dataset_t2_name == "scene-2.tif"
        assert row.model_t1_name == "U-Net" and row.threshold_t2 == 0.5


def test_same_result_is_rejected(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        with pytest.raises(service.ChangeAnalysisError, match="两个不同"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r1")


def test_missing_result_is_rejected(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        with pytest.raises(service.ChangeAnalysisError, match="不存在"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="missing")


def test_unfinished_task_is_rejected(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        db.get(InferenceTask, "t2").status = "running"
        db.commit()
        with pytest.raises(service.ChangeAnalysisError, match="已完成"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")


def test_missing_mask_is_rejected(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        Path(db.get(InferenceResult, "r2").mask_path).unlink()
        with pytest.raises(service.ChangeAnalysisError, match="文件不存在"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")


def test_spatial_mismatch_is_saved_as_failed(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path, mismatch=True)
        with pytest.raises(service.ChangeAnalysisError, match="空间参考"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        row = db.query(ChangeAnalysis).one()
        assert row.status == "failed" and row.spatial_consistent is False
        assert row.change_png_path is None


@pytest.mark.parametrize("variant", ["crs", "transform", "bounds", "dimensions"])
def test_each_spatial_mismatch_is_rejected(tmp_path, configured, variant):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        path = Path(db.get(InferenceResult, "r2").mask_path)
        if variant == "crs":
            values, crs, transform = [[1, 1], [0, 0]], "EPSG:3857", rasterio.Affine(1, 0, 0, 0, -1, 2)
        elif variant == "transform":
            values, crs, transform = [[1, 1], [0, 0]], "EPSG:4326", rasterio.Affine(2, 0, 0, 0, -2, 4)
        elif variant == "bounds":
            values, crs, transform = [[1, 1], [0, 0]], "EPSG:4326", rasterio.Affine(1, 0, 8, 0, -1, 2)
        else:
            values, crs, transform = [[1, 1, 0], [0, 0, 1]], "EPSG:4326", rasterio.Affine(1, 0, 0, 0, -1, 2)
        values = np.asarray(values, dtype=np.uint8)
        with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
                           count=1, dtype="uint8", crs=crs, transform=transform) as dst:
            dst.write(values, 1)
        with pytest.raises(service.ChangeAnalysisError, match="空间参考"):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        assert db.query(ChangeAnalysis).one().status == "failed"


def test_serialized_record_contains_urls_and_no_local_paths(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        data = service.serialize_change_analysis(row)
        assert data["image_url"].endswith("/image")
        assert data["geotiff_url"].endswith("/geotiff")
        assert str(tmp_path) not in str(data)


def test_source_reference_detection(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        assert service.is_source_referenced(db, task_id="t1") is False
        service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        assert service.is_source_referenced(db, task_id="t1") is True
        assert service.is_source_referenced(db, result_id="r2") is True


def test_delete_removes_record_and_own_directory(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        analysis_id = row.id
        output_dir = configured / "changes" / analysis_id
        service.delete_change_analysis(db, row)
        assert db.get(ChangeAnalysis, analysis_id) is None and not output_dir.exists()
        assert db.get(InferenceResult, "r1") is not None and db.get(InferenceResult, "r2") is not None


def test_referenced_result_delete_is_protected(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        with pytest.raises(HTTPException) as raised:
            results_api.delete_result("r1", db=db)
        assert raised.value.status_code == 409
        assert str(raised.value.detail) == "该结果已被时空变化分析引用，请先删除相关变化分析记录。"


def test_api_create_list_detail_and_csv(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        created = api.create(ChangeAnalysisCreate(result_t1_id="r1", result_t2_id="r2"), db=db)["data"]
        assert api.list_all(db=db, status=None, limit=100)["data"][0]["id"] == created["id"]
        assert api.detail(created["id"], db=db)["data"]["status"] == "completed"
        response = api.export_csv(created["id"], db=db)
        assert response.media_type == "text/csv"


def test_ai_context_uses_persisted_statistics_without_paths(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        context = build_change_analysis_context(db, row.id)
        assert context["change"]["new_water_pixels"] == 1
        assert context["analysis"]["spatial_consistent"] is True
        assert str(tmp_path) not in str(context)


def test_ai_context_rejects_failed_record(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path, mismatch=True)
        with pytest.raises(service.ChangeAnalysisError):
            service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        row = db.query(ChangeAnalysis).one()
        with pytest.raises(AIContextError) as raised:
            build_change_analysis_context(db, row.id)
        assert raised.value.status_code == 409


def test_change_report_contains_business_metrics(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2", name="一月变化")
        report = generate_change_report_html(row)
        assert "一月变化" in report and "新增积水" in report and "消退积水" in report
        assert "空间一致性" in report and "工程机械绝对安全通行条件" in report


def test_change_pdf_uses_shared_renderer(tmp_path, configured, monkeypatch):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        monkeypatch.setattr(change_report_service, "render_html_pdf", lambda html: b"%PDF-mock" if "新增积水" in html else b"")
        assert change_report_service.generate_change_report_pdf(row) == b"%PDF-mock"


def test_user_visible_change_text_has_no_old_flood_terms(tmp_path, configured):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed(db, tmp_path)
        row = service.create_change_analysis(db, result_t1_id="r1", result_t2_id="r2")
        visible = generate_change_report_html(row)
    frontend = (Path(__file__).parents[2] / "frontend" / "src" / "views" / "CompareView.vue").read_text(encoding="utf-8")
    for old_term in ("新增洪水", "持续洪水", "消退洪水", "new flood", "persistent flood", "receded flood"):
        assert old_term not in visible and old_term not in frontend
