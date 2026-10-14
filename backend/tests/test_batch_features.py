import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import datasets as datasets_api
from backend.app.api import tasks as tasks_api
from backend.app.db.database import Base
from backend.app.db.models import DatasetRecord, InferenceTask
from backend.app.schemas.inference import BatchInferenceRequest
from backend.app.services.task_queue import TaskQueueService
from tests.make_demo_data import generate_demo


def make_upload(path: Path, name: str | None = None) -> UploadFile:
    return UploadFile(filename=name or path.name, file=BytesIO(path.read_bytes()))


def test_batch_upload_success_duplicate_and_failed(tmp_path, monkeypatch):
    source = tmp_path / "source"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    generate_demo(source)
    images = sorted((source / "v1.1/data/flood_events/HandLabeled/S1Hand").glob("*.tif"))[:3]
    assert len(images) == 3
    monkeypatch.setattr(
        datasets_api, "get_config", lambda: SimpleNamespace(paths=SimpleNamespace(uploads=uploads))
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'batch.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        items = [datasets_api.process_upload(make_upload(path), db) for path in images]
        items.append(datasets_api.process_upload(make_upload(images[0], "renamed.tif"), db))
        items.append(datasets_api.process_upload(UploadFile(filename="bad.tif", file=BytesIO(b"bad")), db))
        assert [item["status"] for item in items] == [
            "success", "success", "success", "duplicate", "failed"
        ]
        assert db.query(DatasetRecord).count() == 3
        assert len(list(uploads.glob("*.tif"))) == 3


def test_batch_request_rejects_more_than_50():
    try:
        BatchInferenceRequest(dataset_ids=[str(i) for i in range(51)])
    except ValueError:
        pass
    else:
        raise AssertionError("51 dataset IDs must be rejected")


def test_local_queue_is_fifo(monkeypatch):
    service = TaskQueueService()
    executed: list[str] = []
    monkeypatch.setattr(service, "_execute", executed.append)
    service._worker = threading.Thread(target=service._work, daemon=True)
    service._worker.start()
    for task_id in ("task-a", "task-b", "task-c"):
        service.submit(task_id)
    service._queue.join()
    service.stop()
    assert executed == ["task-a", "task-b", "task-c"]


def test_failed_retry_creates_new_task_and_preserves_old(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'retry.db').as_posix()}")
    Base.metadata.create_all(engine)
    source = tmp_path / "source.tif"
    source.write_bytes(b"source")
    with Session(engine, expire_on_commit=False) as db:
        dataset = DatasetRecord(
            id="dataset", name="source.tif", path=str(source), width=1, height=1, bands=2,
            dtype="float32", transform="[]", bounds="[]", vv_stats="{}", vh_stats="{}",
        )
        failed = InferenceTask(
            id="failed-task", dataset=dataset, status="failed", threshold=.42,
            checkpoint="model.pt", model_name="U-Net", error_message="controlled failure",
        )
        db.add_all([dataset, failed])
        db.commit()
        submitted: list[str] = []
        monkeypatch.setattr(tasks_api.task_queue, "submit", submitted.append)
        new_task = tasks_api.enqueue_copy(failed, db, retry=True)
        assert new_task.id != failed.id
        assert new_task.status == "pending"
        assert new_task.retry_of_task_id == failed.id
        assert db.get(InferenceTask, failed.id).status == "failed"
        assert submitted == [new_task.id]
