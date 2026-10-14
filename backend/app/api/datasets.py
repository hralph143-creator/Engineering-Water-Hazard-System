import hashlib
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.database import get_db
from ..db.models import DatasetRecord, InferenceResult, InferenceTask
from ..schemas.common import ok
from ..services.raster_service import RasterValidationError, inspect_geotiff
from .helpers import dataset_to_dict
from ..schemas.rainfall import CaptureTimeUpdate

router = APIRouter(prefix="/datasets", tags=["datasets"])
ALLOWED_SUFFIXES = {".tif", ".tiff"}
MAX_BATCH_FILES = 50


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_upload(file: UploadFile, db: Session) -> dict:
    filename = Path(file.filename or "unnamed").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return {"filename": filename, "status": "failed", "message": "仅支持 .tif 或 .tiff 格式的 GeoTIFF"}

    dataset_id = str(uuid.uuid4())
    target = get_config().paths.uploads / f"{dataset_id}{suffix}"
    try:
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        file_hash = sha256_file(target)
        duplicate = db.scalar(select(DatasetRecord).where(DatasetRecord.file_hash == file_hash))
        if duplicate:
            target.unlink(missing_ok=True)
            return {
                "filename": filename, "status": "duplicate", "dataset_id": duplicate.id,
                "message": "数据已存在（SHA256 相同）", "dataset": dataset_to_dict(duplicate),
            }
        metadata = inspect_geotiff(target)
        row = DatasetRecord(
            id=dataset_id, name=filename, path=str(target), file_hash=file_hash,
            status="valid", **metadata,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "filename": filename, "status": "success", "dataset_id": row.id,
            "message": "上传并校验成功", "dataset": dataset_to_dict(row),
        }
    except RasterValidationError as exc:
        db.rollback()
        target.unlink(missing_ok=True)
        return {"filename": filename, "status": "failed", "message": str(exc)}
    except IntegrityError:
        db.rollback()
        target.unlink(missing_ok=True)
        duplicate = db.scalar(select(DatasetRecord).where(DatasetRecord.file_hash == file_hash))
        return {
            "filename": filename, "status": "duplicate", "dataset_id": duplicate.id if duplicate else None,
            "message": "数据已存在（SHA256 相同）",
            "dataset": dataset_to_dict(duplicate) if duplicate else None,
        }
    except Exception as exc:
        db.rollback()
        target.unlink(missing_ok=True)
        return {"filename": filename, "status": "failed", "message": f"{type(exc).__name__}: {exc}"}


@router.post("/upload")
def upload_dataset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    item = process_upload(file, db)
    if item["status"] == "failed":
        raise HTTPException(status_code=400, detail=item["message"])
    if item["status"] == "duplicate":
        raise HTTPException(status_code=409, detail=item["message"])
    return ok(item["dataset"], "遥感影像导入成功")


@router.post("/upload-batch")
def upload_batch(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {MAX_BATCH_FILES} 个文件")
    items = [process_upload(file, db) for file in files]
    data = {
        "total": len(items),
        "success_count": sum(item["status"] == "success" for item in items),
        "duplicate_count": sum(item["status"] == "duplicate" for item in items),
        "failed_count": sum(item["status"] == "failed" for item in items),
        "items": items,
    }
    return ok(data, "批量上传处理完成")


@router.get("")
def list_datasets(db: Session = Depends(get_db)):
    rows = db.scalars(select(DatasetRecord).order_by(DatasetRecord.created_at.desc())).all()
    return ok([dataset_to_dict(row) for row in rows])


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    row = db.get(DatasetRecord, dataset_id)
    if not row:
        raise HTTPException(status_code=404, detail="遥感数据不存在")
    data = dataset_to_dict(row)
    history = db.execute(
        select(
            InferenceTask.finished_at, InferenceResult.flood_ratio, InferenceResult.risk_level
        )
        .join(InferenceResult, InferenceResult.task_id == InferenceTask.id)
        .where(
            InferenceTask.dataset_id == dataset_id,
            InferenceTask.status == "completed",
        )
        .order_by(InferenceTask.finished_at.desc())
    ).all()
    data["history"] = {
        "inference_count": len(history),
        "last_inference_at": history[0][0] if history else None,
        "last_flood_ratio": history[0][1] if history else None,
        "last_risk_level": history[0][2] if history else None,
    }
    return ok(data)


@router.patch("/{dataset_id}/capture-time")
def update_capture_time(dataset_id: str, payload: CaptureTimeUpdate, db: Session = Depends(get_db)):
    row = db.get(DatasetRecord, dataset_id)
    if not row:
        raise HTTPException(status_code=404, detail="遥感数据不存在")
    row.capture_time = payload.capture_time
    db.commit(); db.refresh(row)
    return ok(dataset_to_dict(row), "影像采集时间已更新")


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    row = db.get(DatasetRecord, dataset_id)
    if not row:
        raise HTTPException(status_code=404, detail="遥感数据不存在")
    task = db.scalar(select(InferenceTask).where(InferenceTask.dataset_id == dataset_id).limit(1))
    if task:
        raise HTTPException(status_code=409, detail="该数据已有识别任务，不能删除")
    path = Path(row.path)
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)
    return ok(message="数据已删除")
