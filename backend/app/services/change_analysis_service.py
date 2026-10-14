from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import ChangeAnalysis, ConstructionProject, InferenceResult
from .compare_service import SpatialMismatchError, change_analysis, spatial_consistent


class ChangeAnalysisError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _result(db: Session, result_id: str) -> InferenceResult:
    result = db.get(InferenceResult, result_id)
    if not result:
        raise ChangeAnalysisError("识别结果不存在")
    if result.task.status != "completed":
        raise ChangeAnalysisError("仅已完成的识别结果可用于变化分析")
    if not Path(result.mask_path).is_file():
        raise ChangeAnalysisError("识别结果的积水分布文件不存在")
    return result


def create_change_analysis(
    db: Session, *, result_t1_id: str, result_t2_id: str, name: str | None = None,
    project_id: str | None = None,
) -> ChangeAnalysis:
    if result_t1_id == result_t2_id:
        raise ChangeAnalysisError("请选择两个不同的识别结果")
    t1 = _result(db, result_t1_id)
    t2 = _result(db, result_t2_id)
    task1, task2 = t1.task, t2.task
    dataset1, dataset2 = task1.dataset, task2.dataset
    if project_id and not db.get(ConstructionProject, project_id):
        raise ChangeAnalysisError("施工项目不存在")
    for dataset in (dataset1, dataset2):
        if project_id and dataset.project_id and dataset.project_id != project_id:
            raise ChangeAnalysisError("施工项目与遥感数据项目归属不一致")
    analysis_id = str(uuid.uuid4())
    default_name = f"{dataset1.name} → {dataset2.name}"
    row = ChangeAnalysis(
        id=analysis_id,
        name=(name or default_name).strip() or default_name,
        task_t1_id=task1.id,
        task_t2_id=task2.id,
        result_t1_id=t1.id,
        result_t2_id=t2.id,
        dataset_t1_id=dataset1.id,
        dataset_t2_id=dataset2.id,
        dataset_t1_name=dataset1.name,
        dataset_t2_name=dataset2.name,
        finished_t1_at=task1.finished_at,
        finished_t2_at=task2.finished_at,
        model_t1_name=task1.model_name,
        model_t2_name=task2.model_name,
        threshold_t1=task1.threshold,
        threshold_t2=task2.threshold,
        status="running",
        started_at=_now(),
        project_id=project_id,
    )
    db.add(row)
    db.commit()

    out_dir = get_config().paths.outputs / "changes" / analysis_id
    try:
        if not spatial_consistent(t1, t2):
            raise SpatialMismatchError(
                "两个识别结果空间参考或栅格范围不一致，无法执行像素级变化分析。"
            )
        output = change_analysis(
            t1, t2, out_dir, tif_name="change.tif", png_name="change.png"
        )
        counts, ratios = output["counts"], output["ratios"]
        row.spatial_consistent = True
        row.new_water_pixels = counts["new_flood_pixels"]
        row.receded_water_pixels = counts["recession_pixels"]
        row.persistent_water_pixels = counts["persistent_pixels"]
        row.stable_non_water_pixels = counts["non_flood_pixels"]
        row.valid_pixels = output["valid_pixels"]
        row.new_water_ratio = ratios["new_flood_ratio"]
        row.receded_water_ratio = ratios["recession_ratio"]
        row.persistent_water_ratio = ratios["persistent_ratio"]
        row.stable_non_water_ratio = ratios["non_flood_ratio"]
        row.t1_water_ratio = t1.flood_ratio
        row.t2_water_ratio = t2.flood_ratio
        row.net_change_ratio = t2.flood_ratio - t1.flood_ratio
        row.change_png_path = output["change_mask_png"]
        row.change_geotiff_path = output["change_mask_tif"]
        row.status = "completed"
        row.completed_at = _now()
        summary = serialize_change_analysis(row)
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        row.status = "failed"
        row.spatial_consistent = False if isinstance(exc, SpatialMismatchError) else None
        row.error_message = str(exc)
        row.completed_at = _now()
        db.commit()
        raise ChangeAnalysisError(str(exc)) from exc


def serialize_change_analysis(row: ChangeAnalysis) -> dict:
    counts = {
        "new_water": row.new_water_pixels,
        "persistent_water": row.persistent_water_pixels,
        "receded_water": row.receded_water_pixels,
        "stable_non_water": row.stable_non_water_pixels,
    }
    ratios = {
        "new_water": row.new_water_ratio,
        "persistent_water": row.persistent_water_ratio,
        "receded_water": row.receded_water_ratio,
        "stable_non_water": row.stable_non_water_ratio,
    }
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "status": row.status,
        "spatial_consistent": row.spatial_consistent,
        "error_message": row.error_message,
        "algorithm_version": row.algorithm_version,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "t1": {
            "task_id": row.task_t1_id, "result_id": row.result_t1_id,
            "dataset_id": row.dataset_t1_id, "dataset_name": row.dataset_t1_name,
            "finished_at": row.finished_t1_at, "model_name": row.model_t1_name,
            "threshold": row.threshold_t1, "water_ratio": row.t1_water_ratio,
        },
        "t2": {
            "task_id": row.task_t2_id, "result_id": row.result_t2_id,
            "dataset_id": row.dataset_t2_id, "dataset_name": row.dataset_t2_name,
            "finished_at": row.finished_t2_at, "model_name": row.model_t2_name,
            "threshold": row.threshold_t2, "water_ratio": row.t2_water_ratio,
        },
        "counts": counts,
        "ratios": ratios,
        "valid_pixels": row.valid_pixels,
        "net_change_ratio": row.net_change_ratio,
        "image_url": f"/api/change-analyses/{row.id}/image" if row.change_png_path else None,
        "geotiff_url": f"/api/change-analyses/{row.id}/geotiff" if row.change_geotiff_path else None,
    }


def is_source_referenced(db: Session, *, task_id: str | None = None, result_id: str | None = None) -> bool:
    conditions = []
    if task_id:
        conditions.extend((ChangeAnalysis.task_t1_id == task_id, ChangeAnalysis.task_t2_id == task_id))
    if result_id:
        conditions.extend((ChangeAnalysis.result_t1_id == result_id, ChangeAnalysis.result_t2_id == result_id))
    return bool(conditions and db.scalar(select(ChangeAnalysis.id).where(or_(*conditions)).limit(1)))


def delete_change_analysis(db: Session, row: ChangeAnalysis) -> None:
    output_root = (get_config().paths.outputs / "changes").resolve()
    target = (output_root / row.id).resolve()
    if target.parent != output_root:
        raise ChangeAnalysisError("变化分析文件目录无效")
    if target.exists():
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise ChangeAnalysisError("变化分析文件删除失败，记录未删除") from exc
    db.delete(row)
    db.commit()
