from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.db.models import DatasetRecord, InferenceResult, InferenceTask
from backend.app.services.forecast_service import build_forecast


NOW = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'forecast.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _result(db: Session, index: int, ratio: float, project_id: str | None = None):
    moment = NOW + timedelta(hours=index * 6)
    dataset = DatasetRecord(id=f"d{index}", name=f"影像{index}", path=f"/{index}.tif", width=10, height=10, bands=2, dtype="float32", transform="[]", bounds="[]", vv_stats="{}", vh_stats="{}", capture_time=moment, project_id=project_id)
    task = InferenceTask(id=f"t{index}", dataset_id=dataset.id, status="completed", threshold=.5, checkpoint="model.pt", finished_at=moment, project_id=project_id)
    result = InferenceResult(id=f"r{index}", task_id=task.id, probability_path=f"/{index}/probability.tif", mask_path=f"/{index}/mask.tif", comparison_path=f"/{index}/comparison.png", flood_pixel_count=int(ratio * 100), valid_pixel_count=100, flood_ratio=ratio, mean_confidence=.9, risk_level="中风险", inference_seconds=1, created_at=moment)
    db.add_all([dataset, task, result]); db.commit()


def test_forecast_requires_two_results(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _result(db, 0, .1)
        data = build_forecast(db)
        assert data["ready"] is False
        assert data["status"] == "INSUFFICIENT_DATA"
        assert data["projections"] == []


def test_forecast_projects_rising_series_without_fabricating_rain(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _result(db, 0, .10)
        _result(db, 1, .13)
        _result(db, 2, .16)
        data = build_forecast(db)
        assert data["ready"] is True
        assert data["direction"] == "RISING"
        assert [item["hours"] for item in data["projections"]] == [6, 12, 24]
        assert data["projections"][0]["flood_ratio"] > .16
        assert data["rainfall"]["has_data"] is False
        assert "不是天气预报" in data["disclaimer"]


def test_forecast_does_not_mix_projects(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _result(db, 0, .80, "older")
        _result(db, 1, .10, "latest")
        _result(db, 2, .11, "latest")
        data = build_forecast(db)
        assert data["project_id"] == "latest"
        assert data["sample_count"] == 2
