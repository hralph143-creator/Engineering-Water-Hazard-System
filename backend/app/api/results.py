import csv
import io
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db.database import get_db
from ..db.models import InferenceResult, InferenceTask
from ..schemas.common import ok
from .helpers import require_file, task_to_dict
from ..services.change_analysis_service import is_source_referenced

router = APIRouter(prefix="/results", tags=["results"])


def find_result(result_id: str, db: Session) -> InferenceResult:
    row = db.get(InferenceResult, result_id)
    if not row: raise HTTPException(status_code=404, detail="识别结果不存在")
    return row


@router.get("")
def list_results(db: Session = Depends(get_db)):
    rows = db.scalars(select(InferenceTask).options(joinedload(InferenceTask.dataset), joinedload(InferenceTask.result)).order_by(InferenceTask.started_at.desc())).unique().all()
    return ok([task_to_dict(row) for row in rows])


@router.get("/{result_id}")
def get_result(result_id: str, db: Session = Depends(get_db)):
    row = find_result(result_id, db)
    return ok(task_to_dict(row.task))


@router.get("/{result_id}/files/{kind}")
def result_file(result_id: str, kind: str, db: Session = Depends(get_db)):
    row = find_result(result_id, db)
    base = Path(row.comparison_path).parent
    if kind == "flow_mask":
        target = require_file(str(base / "mask.png"), "积水分布")
        with Image.open(target) as source:
            grayscale = source.convert("L")
            low, high = grayscale.getextrema()
            threshold = (low + high) / 2
            alpha = grayscale.point(lambda value: 255 if value > threshold else 0)
            transparent = Image.new("RGBA", grayscale.size, (255, 255, 255, 0))
            transparent.putalpha(alpha)
            output = io.BytesIO()
            transparent.save(output, format="PNG", optimize=True)
        output.seek(0)
        return StreamingResponse(output, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
    files = {
        "probability": (row.probability_path, "image/tiff"), "mask": (row.mask_path, "image/tiff"),
        "comparison": (row.comparison_path, "image/png"), "vv_preview": (base / "vv.png", "image/png"),
        "vh_preview": (base / "vh.png", "image/png"), "probability_preview": (base / "probability.png", "image/png"),
        "mask_preview": (base / "mask.png", "image/png"),
    }
    if kind not in files: raise HTTPException(status_code=404, detail="不支持的结果文件类型")
    path, media = files[kind]
    target = require_file(str(path), "结果")
    return FileResponse(target, media_type=media, filename=target.name)


@router.get("/{result_id}/export.csv")
def export_csv(result_id: str, db: Session = Depends(get_db)):
    row = find_result(result_id, db); task = row.task; dataset = task.dataset
    stream = io.StringIO(); writer = csv.writer(stream)
    writer.writerow(["数据名称", "影像尺寸", "CRS", "识别时间", "模型", "阈值", "有效像素数", "积水像素数", "积水占比", "基础水害提示"])
    writer.writerow([dataset.name, f"{dataset.width}×{dataset.height}", dataset.crs or "", task.finished_at,
                     task.model_name, task.threshold, row.valid_pixel_count, row.flood_pixel_count, row.flood_ratio, row.risk_level])
    data = stream.getvalue().encode("utf-8-sig")
    return StreamingResponse(iter([data]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="result_{result_id}.csv"'})


@router.delete("/{result_id}")
def delete_result(result_id: str, db: Session = Depends(get_db)):
    row = find_result(result_id, db); task = row.task; output_dir = Path(row.comparison_path).parent
    if task.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="待处理或识别中的任务不能删除")
    if is_source_referenced(db, task_id=task.id, result_id=row.id):
        raise HTTPException(status_code=409, detail="该结果已被时空变化分析引用，请先删除相关变化分析记录。")
    db.delete(task); db.commit()
    if output_dir.is_dir(): shutil.rmtree(output_dir, ignore_errors=True)
    return ok(message="识别结果已删除")
