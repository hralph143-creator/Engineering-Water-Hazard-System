import json
from pathlib import Path

from fastapi import HTTPException

from ..db.models import DatasetRecord, InferenceTask


def dataset_to_dict(row: DatasetRecord) -> dict:
    return {
        "id": row.id, "project_id": getattr(row, "project_id", None), "filename": row.name, "name": row.name,
        "source_type": row.source_type, "width": row.width, "height": row.height,
        "bands": row.bands, "dtype": row.dtype, "crs": row.crs,
        "transform": json.loads(row.transform or "[]"),
        "bounds": json.loads(row.bounds or "[]"), "nodata": row.nodata,
        "vv_stats": json.loads(row.vv_stats or "{}"),
        "vh_stats": json.loads(row.vh_stats or "{}"),
        "created_at": row.created_at, "capture_time": row.capture_time,
        "status": row.status, "valid": row.status == "valid",
        "file_hash": row.file_hash,
    }


def task_to_dict(row: InferenceTask) -> dict:
    data = {
        "id": row.id, "project_id": getattr(row, "project_id", None), "dataset_id": row.dataset_id,
        "dataset_name": row.dataset.name if row.dataset else None,
        "status": row.status, "threshold": row.threshold,
        "created_at": row.created_at, "batch_id": row.batch_id,
        "retry_of_task_id": row.retry_of_task_id,
        "started_at": row.started_at, "finished_at": row.finished_at,
        "checkpoint": Path(row.checkpoint).name, "model_name": row.model_name,
        "error_message": row.error_message, "result": None,
    }
    if row.result:
        result = row.result
        data["result"] = {
            "id": result.id, "flood_pixel_count": result.flood_pixel_count,
            "valid_pixel_count": result.valid_pixel_count, "flood_ratio": result.flood_ratio,
            "mean_confidence": result.mean_confidence, "risk_level": result.risk_level,
            "inference_seconds": result.inference_seconds, "created_at": result.created_at,
            "probability_min": result.probability_min,
            "probability_max": result.probability_max,
            "probability_mean": result.probability_mean,
            "probability_median": result.probability_median,
            "high_confidence_flood_pixel_count": result.high_confidence_flood_pixel_count,
            "high_confidence_flood_ratio": result.high_confidence_flood_ratio,
            "file_status": result_file_status(result),
            "files": {
                "probability": f"/api/results/{result.id}/files/probability",
                "mask": f"/api/results/{result.id}/files/mask",
                "comparison": f"/api/results/{result.id}/files/comparison",
                "vv_preview": f"/api/results/{result.id}/files/vv_preview",
                "vh_preview": f"/api/results/{result.id}/files/vh_preview",
                "probability_preview": f"/api/results/{result.id}/files/probability_preview",
                "mask_preview": f"/api/results/{result.id}/files/mask_preview",
                "flow_mask": f"/api/results/{result.id}/files/flow_mask",
                "csv": f"/api/results/{result.id}/export.csv",
            },
        }
    return data


RESULT_EXPECTED_FILES = [
    ("probability", "probability.tif"),
    ("mask", "mask.tif"),
    ("comparison", "comparison.png"),
    ("vv_preview", "vv.png"),
    ("vh_preview", "vh.png"),
    ("probability_preview", "probability.png"),
    ("mask_preview", "mask.png"),
]


def result_file_status(result) -> dict:
    """检查结果文件完整性：complete / partial / missing，并给出缺失清单（不伪造）。"""
    base = Path(result.comparison_path).parent
    missing = [filename for _label, filename in RESULT_EXPECTED_FILES if not (base / filename).is_file()]
    present = len(RESULT_EXPECTED_FILES) - len(missing)
    if not missing:
        status = "complete"
    elif present == 0:
        status = "missing"
    else:
        status = "partial"
    return {"status": status, "missing_files": missing, "present": present, "total": len(RESULT_EXPECTED_FILES)}


def require_file(path: str, label: str) -> Path:
    target = Path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"{label}文件丢失：{target.name}")
    return target
