from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

import rasterio
from rasterio.warp import transform
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import (
    ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentRiskLink,
    RiskAssessment, WorkArea, WorkAreaImpact,
)
from .spatial_service import AREA_TYPE_LABELS, SpatialAnalysisError, validate_crs, validate_geometry

RULE_VERSION = "equipment-water-link-v1"
EQUIPMENT_TYPE_LABELS = {"excavator":"挖掘机", "loader":"装载机", "dump_truck":"自卸车",
                         "mining_truck":"矿卡", "bulldozer":"推土机", "roller":"压路机",
                         "crane":"起重机", "other":"其他"}
BUSINESS_STATUS_LABELS = {"idle":"待命", "working":"作业中", "paused":"暂停作业",
                          "maintenance":"维护", "offline":"离线/未更新"}
SOURCE_LABELS = {"manual":"人工录入", "csv_import":"CSV导入", "external_reserved":"外部接口预留"}
RISK_LABELS = {"OUTSIDE_ANALYSIS_EXTENT":"超出当前分析范围", "NO_VALID_WATER_DATA":"无有效水害数据",
               "GENERAL_WATER_AREA":"一般水害区域", "INSPECTION_AREA":"重点巡检区域",
               "HIGH_ATTENTION_AREA":"高水害关注区域"}


class EquipmentError(ValueError): pass


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def position_age_hours(recorded_at: datetime, now: datetime | None = None) -> float:
    return max(0.0, (_aware(now or datetime.now(timezone.utc)) - _aware(recorded_at)).total_seconds() / 3600)


def validate_position(x: float, y: float, crs: str) -> str:
    if not math.isfinite(x) or not math.isfinite(y): raise EquipmentError("设备位置坐标必须是有限数值")
    try: normalized = validate_crs(crs)
    except SpatialAnalysisError as exc: raise EquipmentError(str(exc).replace("施工区域", "设备位置")) from exc
    if normalized == "EPSG:4326" and not (-180 <= x <= 180 and -90 <= y <= 90):
        raise EquipmentError("EPSG:4326 坐标范围无效：经度应为 -180～180，纬度应为 -90～90")
    return normalized


def transform_point(x: float, y: float, source_crs: str, target_crs: str) -> tuple[float, float]:
    try:
        if validate_crs(source_crs) == validate_crs(target_crs): return float(x), float(y)
        xs, ys = transform(source_crs, target_crs, [x], [y])
        if not math.isfinite(xs[0]) or not math.isfinite(ys[0]): raise ValueError("non-finite")
        return float(xs[0]), float(ys[0])
    except Exception as exc:
        raise EquipmentError("设备位置空间参考无法与目标成果安全转换。") from exc


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = float(ring[i][0]), float(ring[i][1]); x2, y2 = float(ring[i + 1][0]), float(ring[i + 1][1])
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) <= 1e-10 and min(x1, x2) - 1e-10 <= x <= max(x1, x2) + 1e-10 and min(y1, y2) - 1e-10 <= y <= max(y1, y2) + 1e-10:
            return True
        if (y1 > y) != (y2 > y):
            at_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < at_x: inside = not inside
    return inside


def point_in_geometry(x: float, y: float, raw: dict) -> bool:
    geometry = validate_geometry(raw)
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        if _point_in_ring(x, y, polygon[0]) and not any(_point_in_ring(x, y, hole) for hole in polygon[1:]): return True
    return False


def areas_for_position(db: Session, position: EquipmentPosition, include_archived: bool = False) -> list[WorkArea]:
    statement = select(WorkArea).where(WorkArea.project_id == position.project_id)
    if not include_archived: statement = statement.where(WorkArea.status == "active")
    matched = []
    for area in db.scalars(statement).all():
        x, y = transform_point(position.x, position.y, position.crs, area.crs)
        if point_in_geometry(x, y, json.loads(area.geometry_geojson)): matched.append(area)
    return sorted(matched, key=lambda row: (row.area_code, row.id))


def sample_risk_zone(risk: RiskAssessment, position: EquipmentPosition) -> tuple[str, int | None]:
    if not risk.risk_geotiff_path: raise EquipmentError("风险研判 GeoTIFF 不存在")
    try:
        with rasterio.open(risk.risk_geotiff_path) as src:
            if not src.crs: raise EquipmentError("风险成果缺少空间参考")
            x, y = transform_point(position.x, position.y, position.crs, src.crs.to_string())
            if not (src.bounds.left <= x < src.bounds.right and src.bounds.bottom < y <= src.bounds.top):
                return "OUTSIDE_ANALYSIS_EXTENT", None
            row, col = src.index(x, y)
            if row < 0 or col < 0 or row >= src.height or col >= src.width: return "OUTSIDE_ANALYSIS_EXTENT", None
            value = int(src.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
    except EquipmentError: raise
    except Exception as exc: raise EquipmentError("风险成果无法读取，无法执行设备水害关联") from exc
    return {0:"NO_VALID_WATER_DATA", 1:"GENERAL_WATER_AREA", 2:"INSPECTION_AREA", 3:"HIGH_ATTENTION_AREA"}.get(value, "NO_VALID_WATER_DATA"), value


def latest_position(db: Session, equipment_id: str) -> EquipmentPosition | None:
    return db.scalar(select(EquipmentPosition).where(EquipmentPosition.equipment_id == equipment_id)
                     .order_by(EquipmentPosition.recorded_at.desc(), EquipmentPosition.created_at.desc()).limit(1))


def latest_link(db: Session, equipment_id: str) -> EquipmentRiskLink | None:
    return db.scalar(select(EquipmentRiskLink).where(EquipmentRiskLink.equipment_id == equipment_id)
                     .order_by(EquipmentRiskLink.created_at.desc()).limit(1))


def serialize_position(row: EquipmentPosition | None) -> dict | None:
    if not row: return None
    return {"id":row.id,"equipment_id":row.equipment_id,"project_id":row.project_id,"x":row.x,"y":row.y,
            "crs":row.crs,"position_source":row.position_source,"position_source_label":SOURCE_LABELS.get(row.position_source,row.position_source),
            "recorded_at":row.recorded_at,"created_at":row.created_at,"accuracy_note":row.accuracy_note,
            "source_note":row.source_note,"position_age_hours":position_age_hours(row.recorded_at)}


def serialize_equipment(row: ConstructionEquipment, project: ConstructionProject | None = None,
                        position: EquipmentPosition | None = None, link: EquipmentRiskLink | None = None) -> dict:
    return {"id":row.id,"project_id":row.project_id,"project_name":project.project_name if project else None,
            "equipment_code":row.equipment_code,"equipment_name":row.equipment_name,"equipment_type":row.equipment_type,
            "equipment_type_label":EQUIPMENT_TYPE_LABELS.get(row.equipment_type,row.equipment_type),
            "manufacturer":row.manufacturer,"model":row.model,"business_status":row.business_status,
            "business_status_label":BUSINESS_STATUS_LABELS.get(row.business_status,row.business_status),
            "lifecycle_status":row.lifecycle_status,"description":row.description,"created_at":row.created_at,"updated_at":row.updated_at,
            "latest_position":serialize_position(position),"latest_risk_link":serialize_link(link, row, position) if link else None}


def serialize_link(row: EquipmentRiskLink | None, equipment: ConstructionEquipment | None = None,
                   position: EquipmentPosition | None = None, areas: list[WorkArea] | None = None) -> dict | None:
    if not row: return None
    area_names = [x.area_name for x in (areas or [])]
    return {"id":row.id,"equipment_id":row.equipment_id,"equipment_code":equipment.equipment_code if equipment else None,
            "equipment_name":equipment.equipment_name if equipment else None,"equipment_type":equipment.equipment_type if equipment else None,
            "equipment_type_label":EQUIPMENT_TYPE_LABELS.get(equipment.equipment_type,equipment.equipment_type) if equipment else None,
            "position_id":row.position_id,"project_id":row.project_id,"risk_assessment_id":row.risk_assessment_id,
            "recorded_at":row.recorded_at,"risk_analysis_at":row.risk_analysis_at,"time_difference_hours":row.time_difference_hours,
            "position_age_hours":row.position_age_hours,"position_status":row.position_status,"risk_class":row.risk_class,
            "risk_zone_value":row.risk_zone_value,"risk_label":row.risk_label,"work_area_ids":json.loads(row.work_area_ids or "[]"),
            "work_area_names":area_names,"primary_work_area_id":row.primary_work_area_id,
            "primary_work_area_name":area_names[0] if area_names else None,"work_area_impact_level":row.work_area_impact_level,
            "reason_text":row.reason_text,"advice_text":row.advice_text,"created_at":row.created_at,"rule_version":row.rule_version,
            "position":serialize_position(position)}


def _advice(risk_class: str, stale: bool, matched: bool) -> tuple[str, str]:
    if risk_class == "HIGH_ATTENTION_AREA": reason, advice = "设备最近位置对应高水害关注区域。", "建议优先核查该设备周边积水、泥泞及实际作业条件。"
    elif risk_class == "INSPECTION_AREA": reason, advice = "设备最近位置对应重点巡检区域。", "建议继续作业或恢复作业前现场核验。"
    elif risk_class == "GENERAL_WATER_AREA": reason, advice = "设备最近位置对应一般水害区域。", "当前遥感水害结果未发现明显积水异常，不代表不存在其他施工风险。"
    elif risk_class == "OUTSIDE_ANALYSIS_EXTENT": reason, advice = "设备最近位置超出当前水害分析范围。", "请结合其他资料或扩大分析范围后再核验。"
    else: reason, advice = "设备最近位置无有效水害栅格数据。", "当前位置超出当前有效水害数据或对应像元为 NoData，请现场核验。"
    if not matched: reason += " 该位置未落入所属项目已配置施工区域。"
    if stale: advice += " 设备位置更新时间较早，请先确认设备当前实际位置；结果仅供历史关联参考。"
    return reason, advice


def create_risk_link(db: Session, equipment: ConstructionEquipment, risk: RiskAssessment,
                     position: EquipmentPosition | None = None) -> EquipmentRiskLink:
    if risk.status != "completed": raise EquipmentError("仅已完成的风险研判可执行设备水害关联")
    if risk.project_id != equipment.project_id: raise EquipmentError("设备与风险研判不属于同一施工项目")
    position = position or latest_position(db, equipment.id)
    if not position: raise EquipmentError("设备暂无位置记录")
    if position.project_id != equipment.project_id: raise EquipmentError("设备位置项目归属不一致")
    risk_class, zone_value = sample_risk_zone(risk, position)
    areas = areas_for_position(db, position); primary = areas[0] if areas else None
    impact = db.scalar(select(WorkAreaImpact).where(WorkAreaImpact.risk_assessment_id == risk.id,
                       WorkAreaImpact.work_area_id == primary.id)) if primary else None
    cfg = get_config().equipment_position; age = position_age_hours(position.recorded_at); stale = age > cfg.position_stale_hours
    analysis_at = risk.completed_at or risk.created_at
    delta = abs((_aware(analysis_at) - _aware(position.recorded_at)).total_seconds()) / 3600 if analysis_at else None
    reason, advice = _advice(risk_class, stale, bool(areas))
    row = db.scalar(select(EquipmentRiskLink).where(EquipmentRiskLink.risk_assessment_id == risk.id,
                                                    EquipmentRiskLink.equipment_id == equipment.id))
    if row is None:
        row = EquipmentRiskLink(id=str(uuid.uuid4()), equipment_id=equipment.id, position_id=position.id,
                                project_id=equipment.project_id, risk_assessment_id=risk.id, recorded_at=position.recorded_at,
                                position_age_hours=age, position_status="stale" if stale else "valid",
                                risk_class=risk_class, risk_label=RISK_LABELS[risk_class], reason_text=reason, advice_text=advice)
        db.add(row)
    values = {"position_id":position.id,"recorded_at":position.recorded_at,"risk_analysis_at":analysis_at,
              "time_difference_hours":delta,"position_age_hours":age,"position_status":"stale" if stale else "valid",
              "risk_class":risk_class,"risk_zone_value":zone_value,"risk_label":RISK_LABELS[risk_class],
              "work_area_ids":json.dumps([x.id for x in areas]),"primary_work_area_id":primary.id if primary else None,
              "work_area_impact_level":impact.impact_level if impact else None,"reason_text":reason,"advice_text":advice,
              "rule_version":cfg.version,"created_at":datetime.now(timezone.utc)}
    for key, value in values.items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return row


def run_project_equipment_links(db: Session, risk: RiskAssessment) -> list[EquipmentRiskLink]:
    if not risk.project_id: raise EquipmentError("风险研判尚未关联施工项目")
    project = db.get(ConstructionProject, risk.project_id)
    if not project or project.status != "active": raise EquipmentError("施工项目不存在或已归档，不能创建新的设备水害关联")
    equipment = db.scalars(select(ConstructionEquipment).where(ConstructionEquipment.project_id == risk.project_id,
                           ConstructionEquipment.lifecycle_status == "active").order_by(ConstructionEquipment.equipment_code)).all()
    results = []
    for item in equipment:
        position = latest_position(db, item.id)
        if position: results.append(create_risk_link(db, item, risk, position))
    return results
