from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import ConstructionProject, RiskAssessment, WorkArea, WorkAreaImpact

IMPACT_RULE_VERSION = "work-area-impact-v1"
AREA_TYPE_LABELS = {"pit":"采坑", "construction_road":"施工道路区域", "low_lying_area":"低洼区域",
                   "work_face":"作业面", "material_area":"材料/堆场区域", "drainage_area":"排水关注区域", "other":"其他区域"}
PROJECT_TYPE_LABELS = {"open_pit_mine":"露天矿山", "earthwork":"土方施工", "infrastructure":"大型基础设施", "road_construction":"道路施工", "other":"其他"}


class SpatialAnalysisError(ValueError): pass


def _ring(ring: Any) -> None:
    if not isinstance(ring, list) or len(ring) < 4: raise SpatialAnalysisError("Polygon 环必须至少包含4个坐标点")
    for point in ring:
        if not isinstance(point, list) or len(point) < 2: raise SpatialAnalysisError("GeoJSON 坐标格式无效")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in point[:2]):
            raise SpatialAnalysisError("GeoJSON 坐标必须是有限数值")
    if ring[0][:2] != ring[-1][:2]: raise SpatialAnalysisError("Polygon 边界必须闭合")


def validate_geometry(raw: dict) -> dict:
    if not isinstance(raw, dict): raise SpatialAnalysisError("GeoJSON 格式无效")
    geometry = raw.get("geometry") if raw.get("type") == "Feature" else raw
    if not isinstance(geometry, dict): raise SpatialAnalysisError("GeoJSON geometry 缺失")
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind not in {"Polygon", "MultiPolygon"}: raise SpatialAnalysisError("仅支持 Polygon 或 MultiPolygon 施工区域")
    if not isinstance(coordinates, list) or not coordinates: raise SpatialAnalysisError("GeoJSON 坐标不能为空")
    polygons = [coordinates] if kind == "Polygon" else coordinates
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon: raise SpatialAnalysisError("Polygon 坐标不能为空")
        for ring in polygon: _ring(ring)
    return {"type": kind, "coordinates": coordinates}


def validate_crs(raw: str) -> str:
    try:
        return CRS.from_user_input(raw).to_string()
    except Exception as exc:
        raise SpatialAnalysisError("施工区域空间参考无法识别，请使用有效的 EPSG 编码或 WKT。") from exc


def serialize_project(row: ConstructionProject, area_count: int = 0) -> dict:
    return {"id":row.id,"project_name":row.project_name,"project_code":row.project_code,
            "project_type":row.project_type,"project_type_label":PROJECT_TYPE_LABELS.get(row.project_type,row.project_type),
            "description":row.description,"location_text":row.location_text,"status":row.status,
            "created_at":row.created_at,"updated_at":row.updated_at,"work_area_count":area_count}


def serialize_area(row: WorkArea, latest_impact: WorkAreaImpact | None = None) -> dict:
    return {"id":row.id,"project_id":row.project_id,"area_name":row.area_name,"area_code":row.area_code,
            "area_type":row.area_type,"area_type_label":AREA_TYPE_LABELS.get(row.area_type,row.area_type),
            "description":row.description,"geometry_type":row.geometry_type,
            "geometry_geojson":json.loads(row.geometry_geojson),"crs":row.crs,"status":row.status,
            "created_at":row.created_at,"updated_at":row.updated_at,
            "latest_impact":serialize_impact(latest_impact) if latest_impact else None}


def impact_advice(area: WorkArea, level: str) -> str:
    label = AREA_TYPE_LABELS.get(area.area_type, "施工区域")
    if level == "高水害影响": return f"该{label}与高水害关注区域存在空间重叠，建议优先现场核验积水、泥泞及实际作业条件。"
    if level == "中水害影响": return f"该{label}存在重点巡检或局部高关注重叠，建议列入现场复核范围。"
    return f"该{label}当前水害影响较低，仍不代表不存在地质、设备、人员或其他施工安全风险。"


def serialize_impact(row: WorkAreaImpact | None, area: WorkArea | None = None) -> dict | None:
    if row is None: return None
    return {"id":row.id,"risk_assessment_id":row.risk_assessment_id,"project_id":row.project_id,
            "work_area_id":row.work_area_id,"area_name":area.area_name if area else None,
            "area_type":area.area_type if area else None,"area_type_label":AREA_TYPE_LABELS.get(area.area_type,area.area_type) if area else None,
            "analysis_status":row.analysis_status,"total_valid_pixels":row.total_valid_pixels,"nodata_pixels":row.nodata_pixels,
            "general_pixels":row.general_pixels,"inspection_pixels":row.inspection_pixels,"high_attention_pixels":row.high_attention_pixels,
            "general_ratio":row.general_ratio,"inspection_ratio":row.inspection_ratio,"high_attention_ratio":row.high_attention_ratio,
            "affected_pixels":row.affected_pixels,"affected_ratio":row.affected_ratio,"dominant_class":row.dominant_class,
            "impact_level":row.impact_level,"advice":row.advice,"created_at":row.created_at,
            "algorithm_version":row.algorithm_version,"error_message":row.error_message}


def _impact_level(high_ratio: float, inspection_ratio: float, cfg) -> str:
    if high_ratio >= cfg.high_attention_ratio: return "高水害影响"
    if high_ratio > 0 or inspection_ratio >= cfg.inspection_ratio: return "中水害影响"
    return "低水害影响"


def analyze_area(risk: RiskAssessment, area: WorkArea, cfg=None) -> dict:
    if not risk.risk_geotiff_path: raise SpatialAnalysisError("风险研判 GeoTIFF 不存在")
    geometry = validate_geometry(json.loads(area.geometry_geojson))
    app_cfg = cfg or get_config(); rules = app_cfg.work_area_impact
    try:
        with rasterio.open(risk.risk_geotiff_path) as src:
            if not src.crs: raise SpatialAnalysisError("风险成果缺少空间参考")
            try: transformed = transform_geom(area.crs, src.crs, geometry, precision=12) if str(area.crs) != src.crs.to_string() else geometry
            except Exception as exc: raise SpatialAnalysisError("施工区域空间参考与风险成果不一致，且无法安全转换，无法执行空间关联分析。") from exc
            inside = geometry_mask([transformed], out_shape=(src.height, src.width), transform=src.transform, invert=True, all_touched=False)
            zone = src.read(1)
    except SpatialAnalysisError: raise
    except Exception as exc: raise SpatialAnalysisError("风险成果无法读取，无法执行空间关联分析") from exc
    selected = zone[inside]
    nodata = int((selected == 0).sum()); general = int((selected == 1).sum())
    inspection = int((selected == 2).sum()); high = int((selected == 3).sum())
    valid = general + inspection + high
    if valid == 0: raise SpatialAnalysisError("施工区域与风险成果无有效像素重叠")
    ratios = {"general_ratio":general/valid,"inspection_ratio":inspection/valid,"high_attention_ratio":high/valid}
    level = _impact_level(ratios["high_attention_ratio"], ratios["inspection_ratio"], rules)
    counts = {"total_valid_pixels":valid,"nodata_pixels":nodata,"general_pixels":general,
              "inspection_pixels":inspection,"high_attention_pixels":high,
              "affected_pixels":inspection+high,"affected_ratio":(inspection+high)/valid}
    dominant = max(((general,"general"),(inspection,"inspection"),(high,"high_attention")))[1]
    return {**counts, **ratios, "dominant_class":dominant, "impact_level":level,
            "advice":impact_advice(area, level), "algorithm_version":rules.version}


def run_project_impacts(db: Session, risk: RiskAssessment, project: ConstructionProject) -> list[WorkAreaImpact]:
    if risk.status != "completed": raise SpatialAnalysisError("仅已完成的风险研判可执行施工区域影响分析")
    areas = db.scalars(select(WorkArea).where(WorkArea.project_id == project.id, WorkArea.status == "active")).all()
    if not areas: raise SpatialAnalysisError("当前项目尚未配置施工区域边界。")
    risk.project_id = project.id
    results = []
    for area in areas:
        values = analyze_area(risk, area)
        row = db.scalar(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == risk.id,
                                                     WorkAreaImpact.work_area_id == area.id))
        if row is None:
            row = WorkAreaImpact(id=str(uuid.uuid4()), risk_assessment_id=risk.id, project_id=project.id, work_area_id=area.id,
                                 impact_level=values["impact_level"], advice=values["advice"])
            db.add(row)
        for key, value in values.items(): setattr(row, key, value)
        row.analysis_status, row.error_message, row.created_at = "completed", None, datetime.now(timezone.utc)
        results.append(row)
    db.commit()
    for row in results: db.refresh(row)
    return results
