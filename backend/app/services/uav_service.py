from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds, transform as window_transform
from rasterio.vrt import WarpedVRT
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import (
    ConstructionProject, RiskAssessment, UavDataset, UavRiskOverlay,
    UavVerificationRecord, UavWorkAreaCoverage, WorkArea,
)
from .spatial_service import validate_geometry

SPATIAL_VERSION = "uav-spatial-v1"
VERIFICATION_VERSION = "uav-verification-v1"
MAX_PREVIEW_SIDE = 1600
MAX_ANALYSIS_PIXELS = 20_000_000

COVERAGE_LABELS = {"FULL": "完整覆盖", "PARTIAL": "部分覆盖", "NONE": "未覆盖", "UNKNOWN": "无法确定"}
STATUS_LABELS = {"PENDING": "待复核", "REVIEWED": "已复核", "INSUFFICIENT_IMAGE": "影像不足"}
OBSERVATION_LABELS = {"VISIBLE_WATER": "可见积水", "POSSIBLE_WATER": "疑似积水", "MUDDY_OR_WET_SURFACE": "湿润/泥泞迹象", "NO_OBVIOUS_WATER": "未见明显积水", "IMAGE_UNCLEAR": "影像不清晰", "OTHER": "其他"}


class UavError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_uav(path: Path) -> dict:
    try:
        with rasterio.open(path) as src:
            if src.driver != "GTiff":
                raise UavError("仅支持带空间参考的GeoTIFF无人机正射影像")
            if not src.crs:
                raise UavError("无人机正射影像缺少空间参考，无法执行施工区域和风险成果空间协同分析。")
            if src.count not in {3, 4}:
                raise UavError("当前版本仅支持明确的RGB三波段或RGBA四波段无人机正射影像。")
            if src.width <= 0 or src.height <= 0:
                raise UavError("无人机正射影像宽高无效")
            values = list(src.transform)[:6]
            if not all(math.isfinite(float(x)) for x in values) or abs(src.transform.determinant) < 1e-15:
                raise UavError("无人机正射影像Transform无效")
            bounds = list(src.bounds)
            if not all(math.isfinite(float(x)) for x in bounds) or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                raise UavError("无人机正射影像Bounds无效")
            valid = 0
            total = src.width * src.height
            for _, window in src.block_windows(1):
                mask = src.read_masks(1, window=window) > 0
                if src.count == 4:
                    mask &= src.read(4, window=window) > 0
                valid += int(mask.sum())
            if valid <= 0:
                raise UavError("无人机正射影像不包含有效像素")
            return {
                "width": src.width, "height": src.height, "band_count": src.count,
                "dtype": src.dtypes[0], "crs": src.crs.to_string(),
                "transform": json.dumps(values), "bounds": json.dumps(bounds),
                "nodata": None if src.nodata is None else str(src.nodata),
                "pixel_size_x": abs(float(src.transform.a)), "pixel_size_y": abs(float(src.transform.e)),
                "valid_pixel_count": valid, "nodata_ratio": float(1 - valid / total),
            }
    except UavError:
        raise
    except Exception as exc:
        raise UavError("无人机GeoTIFF无法读取或影像数据无效") from exc


def _rgb_uint8(data: np.ndarray, valid: np.ndarray) -> np.ndarray:
    output = np.zeros(data.shape, dtype=np.uint8)
    for index in range(3):
        band = data[index].astype("float64")
        values = band[valid & np.isfinite(band)]
        if not values.size:
            continue
        low, high = np.percentile(values, [2, 98])
        if high <= low:
            high = low + 1
        output[index] = np.clip((band - low) * 255 / (high - low), 0, 255).astype("uint8")
    return np.moveaxis(output, 0, -1)


def _save_preview(src, target: Path, window: Window | None = None) -> None:
    width = int(window.width) if window else src.width
    height = int(window.height) if window else src.height
    scale = min(1.0, MAX_PREVIEW_SIDE / max(width, height))
    out_width, out_height = max(1, round(width * scale)), max(1, round(height * scale))
    data = src.read([1, 2, 3], window=window, boundless=window is not None,
                    out_shape=(3, out_height, out_width), resampling=Resampling.bilinear)
    mask = src.read_masks(1, window=window, boundless=window is not None,
                          out_shape=(out_height, out_width), resampling=Resampling.nearest) > 0
    if src.count == 4:
        alpha = src.read(4, window=window, boundless=window is not None,
                         out_shape=(out_height, out_width), resampling=Resampling.nearest)
        mask &= alpha > 0
    rgb = _rgb_uint8(data, mask)
    rgba = np.dstack((rgb, np.where(mask, 255, 0).astype("uint8")))
    target.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(target, rgba)


def ensure_preview(row: UavDataset) -> Path:
    target = Path(row.preview_path) if row.preview_path else get_config().paths.outputs / "uav" / row.id / "preview.png"
    if target.is_file():
        return target
    with rasterio.open(row.file_path) as src:
        _save_preview(src, target)
    row.preview_path = str(target)
    return target


def serialize_uav(row: UavDataset, project: ConstructionProject | None = None) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "project_name": project.project_name if project else None,
        "name": row.name, "file_hash": row.file_hash, "capture_time": row.capture_time,
        "flight_name": row.flight_name, "flight_batch": row.flight_batch,
        "source_name": row.source_name, "source_note": row.source_note, "device_note": row.device_note,
        "width": row.width, "height": row.height, "band_count": row.band_count, "dtype": row.dtype,
        "crs": row.crs, "transform": json.loads(row.transform), "bounds": json.loads(row.bounds),
        "nodata": row.nodata, "pixel_size_x": row.pixel_size_x, "pixel_size_y": row.pixel_size_y,
        "valid_pixel_count": row.valid_pixel_count, "nodata_ratio": row.nodata_ratio,
        "resolution_note": row.resolution_note, "status": row.status,
        "preview_url": f"/api/uav-datasets/{row.id}/preview" if row.preview_path else None,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "spatial_version": SPATIAL_VERSION,
    }


def _coords_bounds(geometry: dict) -> tuple[float, float, float, float]:
    points = []
    def collect(value):
        if value and isinstance(value[0], (int, float)):
            points.append(value)
        else:
            for item in value:
                collect(item)
    collect(geometry["coordinates"])
    xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bounded_window(src, bounds) -> tuple[Window, tuple[int, int], object]:
    window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
    width, height = max(1, int(window.width)), max(1, int(window.height))
    scale = min(1.0, math.sqrt(MAX_ANALYSIS_PIXELS / (width * height)))
    shape = (max(1, round(height * scale)), max(1, round(width * scale)))
    transform = window_transform(window, src.transform) * rasterio.Affine.scale(width / shape[1], height / shape[0])
    return window, shape, transform


def analyze_work_area(db: Session, uav: UavDataset, area: WorkArea) -> UavWorkAreaCoverage:
    if uav.project_id != area.project_id:
        raise UavError("无人机影像与施工区域不属于同一项目")
    geometry = validate_geometry(json.loads(area.geometry_geojson))
    try:
        with rasterio.open(uav.file_path) as src:
            geometry = transform_geom(area.crs, src.crs, geometry, precision=12) if area.crs != src.crs.to_string() else geometry
            window, shape, transform = _bounded_window(src, _coords_bounds(geometry))
            inside = geometry_mask([geometry], out_shape=shape, transform=transform, invert=True, all_touched=False)
            mask = src.read_masks(1, window=window, boundless=True, out_shape=shape, resampling=Resampling.nearest) > 0
            if src.count == 4:
                mask &= src.read(4, window=window, boundless=True, out_shape=shape, resampling=Resampling.nearest) > 0
            denominator = int(inside.sum())
            covered = int((inside & mask).sum())
            ratio = covered / denominator if denominator else 0.0
            crop = get_config().paths.outputs / "uav" / uav.id / "work_areas" / f"{area.id}.png"
            if covered:
                _save_preview(src, crop, window)
    except Exception as exc:
        if isinstance(exc, UavError): raise
        raise UavError("施工区域无法与无人机影像执行可靠空间覆盖分析") from exc
    status = "NONE" if ratio <= 0 else "FULL" if ratio >= 0.995 else "PARTIAL"
    row = db.scalar(select(UavWorkAreaCoverage).where(UavWorkAreaCoverage.uav_dataset_id == uav.id,
                    UavWorkAreaCoverage.work_area_id == area.id))
    if row is None:
        row = UavWorkAreaCoverage(id=str(uuid.uuid4()), uav_dataset_id=uav.id, project_id=uav.project_id, work_area_id=area.id, coverage_status=status)
        db.add(row)
    row.coverage_status, row.covered_pixels, row.valid_area_pixels = status, covered, denominator
    row.coverage_ratio, row.crop_path = ratio, str(crop) if covered else None
    row.analysis_version, row.created_at, row.error_message = SPATIAL_VERSION, datetime.now(timezone.utc), None
    db.commit(); db.refresh(row)
    return row


def serialize_coverage(row: UavWorkAreaCoverage, uav: UavDataset | None = None, area: WorkArea | None = None) -> dict:
    return {"id": row.id, "uav_dataset_id": row.uav_dataset_id, "uav_name": uav.name if uav else None,
            "project_id": row.project_id, "work_area_id": row.work_area_id, "work_area_name": area.area_name if area else None,
            "coverage_status": row.coverage_status, "coverage_label": COVERAGE_LABELS.get(row.coverage_status, row.coverage_status),
            "covered_pixels": row.covered_pixels, "valid_area_pixels": row.valid_area_pixels,
            "coverage_ratio": row.coverage_ratio, "crop_url": f"/api/uav-coverages/{row.id}/crop" if row.crop_path else None,
            "analysis_version": row.analysis_version, "created_at": row.created_at, "error_message": row.error_message}


def _risk_crop(src, risk_src, zone: np.ndarray, value: int, target: Path) -> str | None:
    rows, cols = np.where(zone == value)
    if not rows.size:
        return None
    left, bottom, right, top = rasterio.transform.array_bounds(rows.max()-rows.min()+1, cols.max()-cols.min()+1,
        risk_src.window_transform(Window(cols.min(), rows.min(), cols.max()-cols.min()+1, rows.max()-rows.min()+1)))
    try:
        left, bottom, right, top = transform_bounds(risk_src.crs, src.crs, left, bottom, right, top, densify_pts=21)
        window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()
        _save_preview(src, target, window)
        return str(target)
    except Exception:
        return None


def analyze_risk_overlay(db: Session, risk: RiskAssessment, uav: UavDataset) -> UavRiskOverlay:
    if risk.status != "completed" or not risk.risk_geotiff_path:
        raise UavError("仅具备GeoTIFF成果的已完成风险研判可执行无人机协同分析")
    if not risk.project_id or risk.project_id != uav.project_id:
        raise UavError("无人机影像与风险研判不属于同一项目")
    with rasterio.open(risk.risk_geotiff_path) as risk_src, rasterio.open(uav.file_path) as src:
        zone = risk_src.read(1)
        with WarpedVRT(src, crs=risk_src.crs, transform=risk_src.transform,
                       width=risk_src.width, height=risk_src.height, resampling=Resampling.nearest) as vrt:
            valid = vrt.dataset_mask() > 0
        values = {}
        for label, code in (("general", 1), ("inspection", 2), ("high_attention", 3)):
            total = int((zone == code).sum()); covered = int(((zone == code) & valid).sum())
            values[f"{label}_covered_pixels"] = covered
            values[f"{label}_coverage_ratio"] = covered / total if total else 0.0
        overlay_id = str(uuid.uuid4())
        existing = db.scalar(select(UavRiskOverlay).where(UavRiskOverlay.uav_dataset_id == uav.id,
                             UavRiskOverlay.risk_assessment_id == risk.id))
        if existing: overlay_id = existing.id
        directory = get_config().paths.outputs / "uav_overlays" / overlay_id
        high_crop = _risk_crop(src, risk_src, zone, 3, directory / "high_attention_crop.png")
        inspection_crop = _risk_crop(src, risk_src, zone, 2, directory / "inspection_crop.png")
    difference = None
    if uav.capture_time and risk.completed_at:
        a = uav.capture_time if uav.capture_time.tzinfo else uav.capture_time.replace(tzinfo=timezone.utc)
        b = risk.completed_at if risk.completed_at.tzinfo else risk.completed_at.replace(tzinfo=timezone.utc)
        difference = (a - b).total_seconds() / 3600
    row = existing or UavRiskOverlay(id=overlay_id, uav_dataset_id=uav.id, risk_assessment_id=risk.id, project_id=uav.project_id)
    if not existing: db.add(row)
    for key, value in values.items(): setattr(row, key, value)
    row.high_attention_crop_path, row.inspection_crop_path = high_crop, inspection_crop
    row.time_difference_hours = difference
    row.time_reference_status = "COMPLETE" if difference is not None else "TIME_REFERENCE_LIMITED"
    row.analysis_version, row.created_at = SPATIAL_VERSION, datetime.now(timezone.utc)
    directory.mkdir(parents=True, exist_ok=True); summary = directory / "summary.json"
    summary.write_text(json.dumps({**values, "uav_dataset_id": uav.id, "risk_assessment_id": risk.id,
                      "time_difference_hours": difference, "analysis_version": SPATIAL_VERSION}, ensure_ascii=False, indent=2), encoding="utf-8")
    row.summary_json_path = str(summary)
    db.commit(); db.refresh(row); return row


def serialize_overlay(row: UavRiskOverlay, uav: UavDataset | None = None) -> dict:
    return {"id": row.id, "uav_dataset_id": row.uav_dataset_id, "uav_name": uav.name if uav else None,
            "uav_capture_time": uav.capture_time if uav else None, "risk_assessment_id": row.risk_assessment_id,
            "project_id": row.project_id, "high_attention_covered_pixels": row.high_attention_covered_pixels,
            "inspection_covered_pixels": row.inspection_covered_pixels, "general_covered_pixels": row.general_covered_pixels,
            "high_attention_coverage_ratio": row.high_attention_coverage_ratio,
            "inspection_coverage_ratio": row.inspection_coverage_ratio, "general_coverage_ratio": row.general_coverage_ratio,
            "high_attention_crop_url": f"/api/uav-overlays/{row.id}/high-attention-crop" if row.high_attention_crop_path else None,
            "inspection_crop_url": f"/api/uav-overlays/{row.id}/inspection-crop" if row.inspection_crop_path else None,
            "time_difference_hours": row.time_difference_hours, "time_reference_status": row.time_reference_status,
            "analysis_version": row.analysis_version, "created_at": row.created_at}


def serialize_verification(row: UavVerificationRecord, uav: UavDataset | None = None,
                           area: WorkArea | None = None) -> dict:
    return {"id": row.id, "project_id": row.project_id, "uav_dataset_id": row.uav_dataset_id,
            "uav_name": uav.name if uav else None, "uav_capture_time": uav.capture_time if uav else None,
            "risk_assessment_id": row.risk_assessment_id, "work_area_id": row.work_area_id,
            "work_area_name": area.area_name if area else None, "verification_status": row.verification_status,
            "verification_status_label": STATUS_LABELS.get(row.verification_status, row.verification_status),
            "observation_type": row.observation_type,
            "observation_type_label": OBSERVATION_LABELS.get(row.observation_type, row.observation_type),
            "finding_text": row.finding_text, "recommendation_text": row.recommendation_text,
            "verified_at": row.verified_at, "image_crop_url": f"/api/uav-verifications/{row.id}/image" if row.image_crop_path else None,
            "structure_version": row.structure_version, "created_at": row.created_at, "updated_at": row.updated_at}
