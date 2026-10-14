import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from ..config import get_config
from ..db.database import get_db
from ..db.models import DatasetRecord, InferenceTask
from ..schemas.common import ok
from ..schemas.inference import BatchInferenceRequest, InferenceRequest
from ..services.task_queue import create_task, task_queue
from .helpers import task_to_dict

router = APIRouter(prefix="/inference", tags=["inference"])


def validate_dataset(db: Session, dataset_id: str) -> tuple[DatasetRecord | None, str | None]:
    dataset = db.get(DatasetRecord, dataset_id)
    if not dataset:
        return None, "遥感数据不存在"
    if dataset.status != "valid":
        return None, "遥感数据状态无效"
    if not Path(dataset.path).is_file():
        return None, "原始数据文件丢失"
    return dataset, None


@router.post("/run")
def run_inference(payload: InferenceRequest, db: Session = Depends(get_db)):
    dataset, error = validate_dataset(db, payload.dataset_id)
    if error:
        raise HTTPException(status_code=404 if not dataset and error == "遥感数据不存在" else 400, detail=error)
    threshold = get_config().model.threshold if payload.threshold is None else payload.threshold
    task = create_task(db, dataset.id, threshold)
    event = task_queue.submit(task.id, waitable=True)
    assert event is not None
    if not task_queue.wait(task.id, event):
        raise HTTPException(status_code=504, detail="推理仍在队列执行，请在积水智能识别页面查看任务状态")
    db.expire_all()
    task = db.query(InferenceTask).options(
        joinedload(InferenceTask.dataset), joinedload(InferenceTask.result)
    ).filter(InferenceTask.id == task.id).one()
    if task.status == "failed":
        if task.error_message and "ModelUnavailableError" in task.error_message:
            raise HTTPException(status_code=503, detail="模型当前不可用，请检查模型文件后重启服务")
        raise HTTPException(status_code=500, detail=f"推理失败：{task.error_message}")
    return ok(task_to_dict(task), "积水智能识别完成")


@router.post("/batch")
def batch_inference(payload: BatchInferenceRequest, db: Session = Depends(get_db)):
    if len(set(payload.dataset_ids)) != len(payload.dataset_ids):
        raise HTTPException(status_code=400, detail="dataset_ids 不能包含重复 ID")
    batch_id = str(uuid.uuid4())
    threshold = get_config().model.threshold if payload.threshold is None else payload.threshold
    tasks: list[InferenceTask] = []
    failed: list[dict] = []
    for dataset_id in payload.dataset_ids:
        dataset, error = validate_dataset(db, dataset_id)
        if error:
            failed.append({"dataset_id": dataset_id, "message": error})
            continue
        task = create_task(db, dataset.id, threshold, batch_id=batch_id)
        tasks.append(task)
    for task in tasks:
        task_queue.submit(task.id)
    return ok({
        "batch_id": batch_id, "total": len(payload.dataset_ids),
        "created_count": len(tasks), "failed_count": len(failed),
        "task_ids": [task.id for task in tasks], "failed": failed,
    }, "批量识别任务已创建")
