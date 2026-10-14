import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import get_config
from ..db.database import get_db
from ..db.models import DatasetRecord, InferenceResult, InferenceTask
from ..schemas.common import ok
from ..services.task_queue import create_task, task_queue
from ..services.change_analysis_service import is_source_referenced
from .helpers import task_to_dict

router = APIRouter(prefix="/tasks", tags=["tasks"])


def task_query():
    return select(InferenceTask).options(
        joinedload(InferenceTask.dataset), joinedload(InferenceTask.result)
    )


def _naive(dt):
    """SQLite 不接受 tz-aware datetime 绑定参数；非 datetime（如未传参）返回 None。"""
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def find_task(task_id: str, db: Session) -> InferenceTask:
    row = db.scalars(task_query().where(InferenceTask.id == task_id)).unique().first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return row


@router.get("")
def list_tasks(
    batch_id: str | None = None,
    dataset_name: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
):
    statement = task_query()
    if batch_id:
        statement = statement.where(InferenceTask.batch_id == batch_id)
    if dataset_name:
        statement = statement.where(
            InferenceTask.dataset.has(DatasetRecord.name.contains(dataset_name))
        )
    if status:
        statement = statement.where(InferenceTask.status == status)
    if risk_level:
        statement = statement.where(
            InferenceTask.result.has(InferenceResult.risk_level == risk_level)
        )
    _date_from = _naive(date_from)
    if _date_from:
        statement = statement.where(InferenceTask.created_at >= _date_from)
    _date_to = _naive(date_to)
    if _date_to:
        statement = statement.where(InferenceTask.created_at <= _date_to)
    statement = statement.order_by(InferenceTask.created_at.desc())
    rows = db.scalars(statement).unique().all()
    return ok([task_to_dict(row) for row in rows])


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    return ok(task_to_dict(find_task(task_id, db)))


def enqueue_copy(row: InferenceTask, db: Session, *, retry: bool) -> InferenceTask:
    if row.status == "running" or row.status == "pending":
        raise HTTPException(status_code=409, detail="任务正在执行或等待处理")
    expected = "failed" if retry else "completed"
    if row.status != expected:
        label = "重试" if retry else "重新识别"
        raise HTTPException(status_code=409, detail=f"仅{expected}任务允许{label}")
    if not row.dataset or not Path(row.dataset.path).is_file():
        raise HTTPException(status_code=400, detail="原始数据文件丢失")
    threshold = get_config().model.threshold if not retry else row.threshold
    task = create_task(
        db, row.dataset_id, threshold, retry_of_task_id=row.id if retry else None,
    )
    task_queue.submit(task.id)
    return task


@router.post("/{task_id}/retry")
def retry_task(task_id: str, db: Session = Depends(get_db)):
    task = enqueue_copy(find_task(task_id, db), db, retry=True)
    return ok(task_to_dict(task), "失败任务已重新加入队列")


@router.post("/{task_id}/rerun")
def rerun_task(task_id: str, db: Session = Depends(get_db)):
    task = enqueue_copy(find_task(task_id, db), db, retry=False)
    return ok(task_to_dict(task), "重新识别任务已加入队列")


@router.delete("/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    row = find_task(task_id, db)
    if row.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="待处理或识别中的任务不能删除")
    if is_source_referenced(db, task_id=row.id, result_id=row.result.id if row.result else None):
        raise HTTPException(status_code=409, detail="该结果已被时空变化分析引用，请先删除相关变化分析记录。")
    output_dir = Path(row.result.comparison_path).parent if row.result else None
    db.delete(row)
    db.commit()
    if output_dir and output_dir.is_dir():
        shutil.rmtree(output_dir, ignore_errors=True)
    return ok(message="任务及其结果已删除")
