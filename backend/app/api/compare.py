"""结果统计对比与像素级积水变化分析 API。"""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.database import get_db
from ..db.models import InferenceResult
from ..schemas.common import ok
from ..services.compare_service import SpatialMismatchError, change_analysis, spatial_consistent

router = APIRouter(prefix="/compare", tags=["compare"])


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_ids: list[str]


def _result_side(result: InferenceResult) -> dict:
    task = result.task
    return {
        "result_id": result.id,
        "dataset_name": task.dataset.name if task.dataset else None,
        "finished_at": task.finished_at,
        "model_name": task.model_name,
        "threshold": task.threshold,
        "flood_ratio": result.flood_ratio,
        "mean_confidence": result.mean_confidence,
        "inference_seconds": result.inference_seconds,
        "risk_level": result.risk_level,
        "valid_pixel_count": result.valid_pixel_count,
        "flood_pixel_count": result.flood_pixel_count,
        "probability_mean": result.probability_mean,
    }


@router.post("")
def compare_results(payload: CompareRequest, db: Session = Depends(get_db)):
    ids = payload.result_ids
    if len(ids) != 2:
        raise HTTPException(status_code=400, detail="请选择两个识别结果进行对比")
    if ids[0] == ids[1]:
        raise HTTPException(status_code=400, detail="请选择两个不同的识别结果")
    result_a = db.get(InferenceResult, ids[0])
    result_b = db.get(InferenceResult, ids[1])
    if not result_a or not result_b:
        raise HTTPException(status_code=404, detail="识别结果不存在")

    side_a, side_b = _result_side(result_a), _result_side(result_b)
    stats = {
        "a": side_a,
        "b": side_b,
        "flood_ratio_diff": side_b["flood_ratio"] - side_a["flood_ratio"],
        "mean_confidence_diff": side_b["mean_confidence"] - side_a["mean_confidence"],
    }

    consistent = False
    change = None
    try:
        consistent = spatial_consistent(result_a, result_b)
        if consistent:
            out_dir = get_config().paths.outputs / f"compare_{ids[0]}__{ids[1]}"
            analysis = change_analysis(result_a, result_b, out_dir)
            png = Path(analysis["change_mask_png"])
            png_data_url = "data:image/png;base64," + base64.b64encode(
                png.read_bytes()
            ).decode("ascii") if png.is_file() else None
            change = {
                "counts": analysis["counts"],
                "ratios": analysis["ratios"],
                "valid_pixels": analysis["valid_pixels"],
                "png_data_url": png_data_url,
                "tif_download": f"/api/compare/{ids[0]}/{ids[1]}/change_mask.tif",
                "color_note": analysis["color_note"],
            }
    except SpatialMismatchError as exc:
        consistent = False
        change = None

    message = (
        "T1 与 T2 空间范围及栅格网格一致，已生成像素级积水变化分析。"
        if consistent
        else "T1 与 T2 空间范围或栅格网格不一致，不支持像素级积水变化分析，仅提供统计对比。"
    )
    return ok(
        {"consistent": consistent, "stats": stats, "change": change},
        message,
    )


@router.get("/{result_id_a}/{result_id_b}/change_mask.tif")
def change_mask_file(result_id_a: str, result_id_b: str):
    path = (
        get_config().paths.outputs
        / f"compare_{result_id_a}__{result_id_b}"
        / "change_mask.tif"
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="变化分析文件不存在")
    return FileResponse(path, media_type="image/tiff", filename=path.name)
