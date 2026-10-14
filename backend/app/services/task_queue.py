import queue
import logging
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import get_config
from ..db.database import SessionLocal
from ..db.models import DatasetRecord, InferenceResult, InferenceTask
from .model_service import get_model_service

logger = logging.getLogger(__name__)


def risk_level(ratio: float) -> str:
    risk = get_config().risk
    if ratio < risk.low:
        return "安全"
    if ratio < risk.medium:
        return "低风险"
    if ratio < risk.high:
        return "中风险"
    return "高风险"


class TaskQueueService:
    """单工作线程 FIFO 队列；所有识别任务复用后端启动时加载的模型服务。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._events: dict[str, threading.Event] = {}
        self._events_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        with SessionLocal() as db:
            stale = db.scalars(select(InferenceTask).where(InferenceTask.status == "running")).all()
            for task in stale:
                task.status = "failed"
                task.finished_at = datetime.now(timezone.utc)
                task.error_message = "服务重启，原运行任务已中断，可点击重试"
            pending_ids = list(db.scalars(
                select(InferenceTask.id).where(InferenceTask.status == "pending")
                .order_by(InferenceTask.created_at.asc())
            ))
            db.commit()
        self._worker = threading.Thread(target=self._work, name="flood-inference-queue", daemon=True)
        self._worker.start()
        logger.info("识别任务队列已启动，恢复待处理任务 %d 条", len(pending_ids))
        for task_id in pending_ids:
            self.submit(task_id)

    def stop(self) -> None:
        if not self._worker:
            return
        self._queue.put(None)
        self._worker.join(timeout=10)
        self._worker = None

    def submit(self, task_id: str, waitable: bool = False) -> threading.Event | None:
        event = threading.Event()
        if waitable:
            with self._events_lock:
                self._events[task_id] = event
        self._queue.put(task_id)
        return event if waitable else None

    def wait(self, task_id: str, event: threading.Event, timeout: float = 120.0) -> bool:
        completed = event.wait(timeout)
        if completed:
            with self._events_lock:
                self._events.pop(task_id, None)
        return completed

    def _work(self) -> None:
        while True:
            task_id = self._queue.get()
            try:
                if task_id is None:
                    return
                self._execute(task_id)
            finally:
                if task_id is not None:
                    with self._events_lock:
                        event = self._events.get(task_id)
                    if event:
                        event.set()
                self._queue.task_done()

    @staticmethod
    def _execute(task_id: str) -> None:
        cfg = get_config()
        output_dir = cfg.paths.outputs / task_id
        with SessionLocal() as db:
            task = db.get(InferenceTask, task_id)
            if not task or task.status != "pending":
                return
            task.status = "running"
            task.started_at = datetime.now(timezone.utc)
            task.error_message = None
            db.commit()
            logger.info("识别任务开始 task_id=%s", task_id)
            try:
                dataset = db.get(DatasetRecord, task.dataset_id)
                if not dataset:
                    raise FileNotFoundError("遥感数据记录不存在")
                image_path = Path(dataset.path)
                if not image_path.is_file():
                    raise FileNotFoundError(f"原始数据文件丢失：{image_path.name}")
                values = get_model_service().infer(image_path, output_dir, task.threshold)
                required = [
                    Path(values["probability_path"]), Path(values["mask_path"]),
                    Path(values["comparison_path"]),
                ]
                if not all(path.is_file() for path in required):
                    raise OSError("推理输出文件不完整")
                db.add(InferenceResult(
                    id=str(uuid.uuid4()), task_id=task.id,
                    risk_level=risk_level(values["flood_ratio"]), **values,
                ))
                task.status = "completed"
                task.finished_at = datetime.now(timezone.utc)
                task.error_message = None
                db.commit()
                logger.info("识别任务完成 task_id=%s", task_id)
            except Exception as exc:
                logger.exception("识别任务失败 task_id=%s error_type=%s", task_id, type(exc).__name__)
                db.rollback()
                task = db.get(InferenceTask, task_id)
                if task:
                    task.status = "failed"
                    task.finished_at = datetime.now(timezone.utc)
                    task.error_message = "识别任务执行失败，请检查数据文件、模型状态和系统日志"
                    db.commit()
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)


task_queue = TaskQueueService()


def create_task(
    db, dataset_id: str, threshold: float, *, batch_id: str | None = None,
    retry_of_task_id: str | None = None,
) -> InferenceTask:
    cfg = get_config()
    task = InferenceTask(
        id=str(uuid.uuid4()), dataset_id=dataset_id, status="pending", threshold=threshold,
        checkpoint=str(cfg.model.checkpoint), model_name=cfg.model.name,
        batch_id=batch_id, retry_of_task_id=retry_of_task_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
