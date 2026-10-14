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
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, reproject, transform_geom
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import (
    ConstructionEquipment, EquipmentPosition, EquipmentTerrainSnapshot, RiskAssessment,
    TerrainDataset, TerrainFusionAnalysis, WorkArea, WorkAreaTerrainProfile,
)
from .equipment_service import transform_point
from .spatial_service import validate_geometry

RULE_VERSION = "terrain-analysis-v1"


class TerrainError(ValueError): pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def inspect_dem(path: Path) -> dict:
    try:
        with rasterio.open(path) as src:
            if src.driver != "GTiff": raise TerrainError("仅支持 GeoTIFF DEM")
            if src.count != 1: raise TerrainError("DEM必须是单波段GeoTIFF")
            if not src.crs: raise TerrainError("DEM缺少CRS，不能进入正式空间分析")
            data = src.read(1, masked=True).astype("float64")
            raw = data.filled(np.nan); valid = np.isfinite(raw)
            if src.nodata is not None: valid &= raw != src.nodata
            values = raw[valid]
            if values.size == 0: raise TerrainError("DEM不包含有限的有效高程像素")
            return {"width":src.width,"height":src.height,"band_count":src.count,"dtype":src.dtypes[0],
                    "crs":src.crs.to_string(),"transform":json.dumps(list(src.transform)[:6]),
                    "bounds":json.dumps(list(src.bounds)),"nodata":None if src.nodata is None else str(src.nodata),
                    "min_elevation":float(values.min()),"max_elevation":float(values.max()),
                    "mean_elevation":float(values.mean()),"elevation_std":float(values.std()),
                    "valid_pixel_count":int(values.size),"nodata_ratio":float(1-values.size/raw.size)}
    except TerrainError: raise
    except Exception as exc: raise TerrainError("DEM GeoTIFF无法读取或格式无效") from exc


def _preview(data: np.ndarray, valid: np.ndarray, path: Path, cmap: str, title: str, colorbar: str) -> None:
    shown = np.ma.array(data, mask=~valid)
    fig, ax = plt.subplots(figsize=(8, 6)); image = ax.imshow(shown, cmap=cmap)
    ax.set_title(title); ax.axis("off"); bar = fig.colorbar(image, ax=ax, shrink=.82); bar.set_label(colorbar)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def ensure_derivatives(row: TerrainDataset) -> dict:
    cfg = get_config(); out = cfg.paths.outputs / "terrain" / row.id; out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    if summary_path.is_file() and row.slope_path and Path(row.slope_path).is_file() and row.low_mask_path and Path(row.low_mask_path).is_file():
        try:
            cached = json.loads(summary_path.read_text(encoding="utf-8"))
            if cached.get("file_hash") == row.file_hash and cached.get("algorithm_version") == cfg.terrain.version and cached.get("low_elevation_percentile") == cfg.terrain.low_elevation_percentile:
                return cached
        except (OSError, json.JSONDecodeError): pass
    with rasterio.open(row.file_path) as src:
        if src.crs.is_geographic: raise TerrainError("地理坐标DEM不能直接计算米制坡度，请使用可靠的米制投影DEM。")
        unit = (src.crs.linear_units or "").lower()
        if "metre" not in unit and "meter" not in unit: raise TerrainError("DEM水平单位不是米，无法可靠计算坡度。")
        raw = src.read(1).astype("float64"); valid = np.isfinite(raw)
        if src.nodata is not None: valid &= raw != src.nodata
        elevation = np.where(valid, raw, np.nan)
        xres, yres = abs(src.transform.a), abs(src.transform.e)
        gy, gx = np.gradient(elevation, yres, xres)
        slope = np.degrees(np.arctan(np.sqrt(gx * gx + gy * gy))); slope_valid = valid & np.isfinite(slope)
        threshold = float(np.nanpercentile(elevation, cfg.terrain.low_elevation_percentile))
        low = np.full(raw.shape, 255, dtype=np.uint8); low[valid] = (elevation[valid] <= threshold).astype(np.uint8)
        profile = src.profile.copy(); profile.update(dtype="float32",nodata=-9999.0,count=1)
        slope_path = out / "slope.tif"
        with rasterio.open(slope_path,"w",**profile) as dst: dst.write(np.where(slope_valid,slope,-9999).astype("float32"),1)
        low_profile = src.profile.copy(); low_profile.update(dtype="uint8",nodata=255,count=1)
        low_path = out / "low_elevation_mask.tif"
        with rasterio.open(low_path,"w",**low_profile) as dst: dst.write(low,1)
        elevation_png, slope_png, low_png = out/"elevation_preview.png", out/"slope.png", out/"low_elevation_mask.png"
        _preview(elevation,valid,elevation_png,"terrain","DEM高程","高程（m）")
        _preview(slope,slope_valid,slope_png,"magma","坡度","坡度（°）")
        _preview(low.astype(float),valid,low_png,"Blues","相对低高程范围","低高程掩膜")
    summary = {"dem_id":row.id,"file_hash":row.file_hash,"algorithm_version":cfg.terrain.version,
               "low_elevation_percentile":cfg.terrain.low_elevation_percentile,"low_elevation_threshold":threshold,
               "slope_unit":"degree","valid_slope_pixels":int(slope_valid.sum())}
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    row.elevation_preview_path=str(elevation_png); row.slope_path=str(slope_path); row.slope_preview_path=str(slope_png)
    row.low_mask_path=str(low_path); row.low_mask_preview_path=str(low_png); row.summary_json_path=str(summary_path)
    row.terrain_rule_version=cfg.terrain.version
    return summary


def serialize_dem(row: TerrainDataset) -> dict:
    def url(kind, path): return f"/api/environment-data/dem/{row.id}/files/{kind}" if path else None
    return {"id":row.id,"project_id":row.project_id,"name":row.name,"file_hash":row.file_hash,"data_type":row.data_type,
            "width":row.width,"height":row.height,"band_count":row.band_count,"dtype":row.dtype,"crs":row.crs,
            "transform":json.loads(row.transform),"bounds":json.loads(row.bounds),"nodata":row.nodata,
            "min_elevation":row.min_elevation,"max_elevation":row.max_elevation,"mean_elevation":row.mean_elevation,
            "elevation_std":row.elevation_std,"valid_pixel_count":row.valid_pixel_count,"nodata_ratio":row.nodata_ratio,
            "status":row.status,"source_name":row.source_name,"capture_date":row.capture_date,"resolution_note":row.resolution_note,
            "vertical_datum_note":row.vertical_datum_note,"description":row.description,"created_at":row.created_at,"updated_at":row.updated_at,
            "terrain_rule_version":row.terrain_rule_version,"files":{"elevation_preview":url("elevation-preview",row.elevation_preview_path),
            "slope":url("slope",row.slope_path),"slope_preview":url("slope-preview",row.slope_preview_path),
            "low_mask":url("low-mask",row.low_mask_path),"low_mask_preview":url("low-mask-preview",row.low_mask_preview_path),
            "summary":url("summary",row.summary_json_path)}}


def analyze_work_area(db: Session, area: WorkArea, dem: TerrainDataset) -> WorkAreaTerrainProfile:
    if dem.project_id and dem.project_id != area.project_id: raise TerrainError("DEM与施工区域不属于同一项目")
    ensure_derivatives(dem)
    with rasterio.open(dem.file_path) as src, rasterio.open(dem.slope_path) as slope_src, rasterio.open(dem.low_mask_path) as low_src:
        geometry=validate_geometry(json.loads(area.geometry_geojson))
        try: geometry=transform_geom(area.crs,src.crs,geometry,precision=12) if area.crs!=src.crs.to_string() else geometry
        except Exception as exc: raise TerrainError("施工区域无法与DEM安全转换") from exc
        inside=geometry_mask([geometry],out_shape=(src.height,src.width),transform=src.transform,invert=True,all_touched=False)
        elev=src.read(1).astype(float); slope=slope_src.read(1).astype(float); low=low_src.read(1)
        valid=inside & np.isfinite(elev) & np.isfinite(slope) & (slope!=-9999)
        if src.nodata is not None: valid &= elev!=src.nodata
        values=elev[valid]; slopes=slope[valid]
        if not values.size: raise TerrainError("施工区域与DEM无有效像素重叠")
        low_pixels=int(((low==1)&valid).sum())
    desc=[]
    ratio=low_pixels/int(values.size)
    if ratio>=.5: desc.append("该区域较多像素处于当前DEM覆盖范围的相对低高程位置。")
    if float(slopes.mean())>=15: desc.append("该区域地形起伏相对明显，坡度指标仅供地形辅助解释。")
    if not desc: desc.append("该区域地形统计已完成，低高程与坡度指标不直接表示积水或通行安全。")
    row=db.scalar(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.work_area_id==area.id,WorkAreaTerrainProfile.dem_id==dem.id))
    if row is None:
        row=WorkAreaTerrainProfile(id=str(uuid.uuid4()),project_id=area.project_id,work_area_id=area.id,dem_id=dem.id); db.add(row)
    values_map={"status":"completed","min_elevation":float(values.min()),"max_elevation":float(values.max()),"mean_elevation":float(values.mean()),
                "elevation_range":float(values.max()-values.min()),"mean_slope":float(slopes.mean()),"max_slope":float(slopes.max()),
                "low_elevation_pixels":low_pixels,"valid_pixels":int(values.size),"low_elevation_ratio":ratio,
                "terrain_description":" ".join(desc),"terrain_rule_version":get_config().terrain.version,"created_at":datetime.now(timezone.utc),"error_message":None}
    for key,value in values_map.items(): setattr(row,key,value)
    db.commit(); db.refresh(row); return row


def serialize_profile(row: WorkAreaTerrainProfile, dem: TerrainDataset | None=None) -> dict:
    return {"id":row.id,"project_id":row.project_id,"work_area_id":row.work_area_id,"dem_id":row.dem_id,"dem_name":dem.name if dem else None,
            "status":row.status,"min_elevation":row.min_elevation,"max_elevation":row.max_elevation,"mean_elevation":row.mean_elevation,
            "elevation_range":row.elevation_range,"mean_slope":row.mean_slope,"max_slope":row.max_slope,
            "low_elevation_pixels":row.low_elevation_pixels,"valid_pixels":row.valid_pixels,"low_elevation_ratio":row.low_elevation_ratio,
            "terrain_description":row.terrain_description,"terrain_rule_version":row.terrain_rule_version,"created_at":row.created_at,"error_message":row.error_message}


def _zone_stats(zone: np.ndarray, value: int, elevation: np.ndarray, slope: np.ndarray, low: np.ndarray) -> tuple[float|None,float|None,float|None]:
    valid=(zone==value)&np.isfinite(elevation)&np.isfinite(slope)&(low!=255)
    if not valid.any(): return None,None,None
    return float(elevation[valid].mean()),float(slope[valid].mean()),float((low[valid]==1).mean())


def analyze_fusion(db: Session, risk: RiskAssessment, dem: TerrainDataset) -> TerrainFusionAnalysis:
    if risk.status!="completed" or not risk.risk_geotiff_path: raise TerrainError("仅具备GeoTIFF成果的已完成风险研判可执行地形融合")
    if dem.project_id and dem.project_id!=risk.project_id: raise TerrainError("DEM与风险研判不属于同一项目")
    ensure_derivatives(dem)
    with rasterio.open(risk.risk_geotiff_path) as target, rasterio.open(dem.file_path) as elev_src, rasterio.open(dem.slope_path) as slope_src, rasterio.open(dem.low_mask_path) as low_src:
        zone=target.read(1); shape=(target.height,target.width)
        elev=np.full(shape,np.nan,dtype="float32"); slope=np.full(shape,np.nan,dtype="float32"); low=np.full(shape,255,dtype="uint8")
        reproject(elev_src.read(1),elev,src_transform=elev_src.transform,src_crs=elev_src.crs,src_nodata=elev_src.nodata,dst_transform=target.transform,dst_crs=target.crs,dst_nodata=np.nan,resampling=Resampling.bilinear)
        reproject(slope_src.read(1),slope,src_transform=slope_src.transform,src_crs=slope_src.crs,src_nodata=slope_src.nodata,dst_transform=target.transform,dst_crs=target.crs,dst_nodata=np.nan,resampling=Resampling.bilinear)
        reproject(low_src.read(1),low,src_transform=low_src.transform,src_crs=low_src.crs,src_nodata=255,dst_transform=target.transform,dst_crs=target.crs,dst_nodata=255,resampling=Resampling.nearest)
    high=_zone_stats(zone,3,elev,slope,low); inspection=_zone_stats(zone,2,elev,slope,low); general=_zone_stats(zone,1,elev,slope,low)
    row=db.scalar(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id==risk.id,TerrainFusionAnalysis.dem_id==dem.id))
    if row is None:
        row=TerrainFusionAnalysis(id=str(uuid.uuid4()),risk_assessment_id=risk.id,dem_id=dem.id,project_id=risk.project_id); db.add(row)
    values={"status":"completed","high_attention_mean_elevation":high[0],"high_attention_mean_slope":high[1],"high_attention_low_elevation_ratio":high[2],
            "inspection_mean_elevation":inspection[0],"inspection_mean_slope":inspection[1],"inspection_low_elevation_ratio":inspection[2],
            "general_mean_elevation":general[0],"general_mean_slope":general[1],"general_low_elevation_ratio":general[2],
            "algorithm_version":get_config().terrain.version,"created_at":datetime.now(timezone.utc),"error_message":None}
    out=get_config().paths.outputs/"terrain"/dem.id/f"fusion_{risk.id}.json"; out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({**values,"created_at":values["created_at"].isoformat()},ensure_ascii=False,indent=2),encoding="utf-8"); values["summary_json_path"]=str(out)
    for key,value in values.items(): setattr(row,key,value)
    db.commit(); db.refresh(row); return row


def serialize_fusion(row: TerrainFusionAnalysis, dem: TerrainDataset | None=None) -> dict:
    return {"id":row.id,"risk_assessment_id":row.risk_assessment_id,"dem_id":row.dem_id,"dem_name":dem.name if dem else None,
            "project_id":row.project_id,"status":row.status,"high_attention":{"mean_elevation":row.high_attention_mean_elevation,"mean_slope":row.high_attention_mean_slope,"low_elevation_ratio":row.high_attention_low_elevation_ratio},
            "inspection":{"mean_elevation":row.inspection_mean_elevation,"mean_slope":row.inspection_mean_slope,"low_elevation_ratio":row.inspection_low_elevation_ratio},
            "general":{"mean_elevation":row.general_mean_elevation,"mean_slope":row.general_mean_slope,"low_elevation_ratio":row.general_low_elevation_ratio},
            "algorithm_version":row.algorithm_version,"created_at":row.created_at,"error_message":row.error_message}


def analyze_equipment_terrain(db: Session, equipment: ConstructionEquipment, position: EquipmentPosition, dem: TerrainDataset) -> EquipmentTerrainSnapshot:
    if equipment.project_id!=position.project_id: raise TerrainError("设备与位置项目归属不一致")
    if dem.project_id and dem.project_id!=equipment.project_id: raise TerrainError("DEM与设备不属于同一项目")
    ensure_derivatives(dem)
    status="completed"; elevation=slope=None; is_low=None
    with rasterio.open(dem.file_path) as elev_src, rasterio.open(dem.slope_path) as slope_src, rasterio.open(dem.low_mask_path) as low_src:
        x,y=transform_point(position.x,position.y,position.crs,elev_src.crs.to_string())
        if not (elev_src.bounds.left<=x<elev_src.bounds.right and elev_src.bounds.bottom<y<=elev_src.bounds.top): status="outside_extent"
        else:
            row_i,col_i=elev_src.index(x,y); ev=float(elev_src.read(1,window=((row_i,row_i+1),(col_i,col_i+1)))[0,0])
            sv=float(slope_src.read(1,window=((row_i,row_i+1),(col_i,col_i+1)))[0,0]); lv=int(low_src.read(1,window=((row_i,row_i+1),(col_i,col_i+1)))[0,0])
            if not math.isfinite(ev) or (elev_src.nodata is not None and ev==elev_src.nodata) or lv==255: status="nodata"
            else: elevation=ev; slope=None if sv==-9999 or not math.isfinite(sv) else sv; is_low=lv==1
    note="当前位置超出DEM分析范围。" if status=="outside_extent" else "当前位置无有效DEM数据。" if status=="nodata" else ("该位置处于当前DEM相对低高程范围；该事实不表示必然积水。" if is_low else "该位置未处于当前DEM相对低高程范围。")
    snapshot=db.scalar(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.position_id==position.id,EquipmentTerrainSnapshot.dem_id==dem.id))
    if snapshot is None:
        snapshot=EquipmentTerrainSnapshot(id=str(uuid.uuid4()),equipment_id=equipment.id,position_id=position.id,dem_id=dem.id,project_id=equipment.project_id,status=status); db.add(snapshot)
    for key,value in {"status":status,"elevation":elevation,"slope":slope,"is_low_elevation":is_low,"terrain_note":note,"created_at":datetime.now(timezone.utc),"algorithm_version":get_config().terrain.version}.items(): setattr(snapshot,key,value)
    db.commit(); db.refresh(snapshot); return snapshot


def serialize_snapshot(row: EquipmentTerrainSnapshot, dem: TerrainDataset | None=None) -> dict:
    return {"id":row.id,"equipment_id":row.equipment_id,"position_id":row.position_id,"dem_id":row.dem_id,"dem_name":dem.name if dem else None,
            "project_id":row.project_id,"status":row.status,"elevation":row.elevation,"slope":row.slope,"is_low_elevation":row.is_low_elevation,
            "terrain_note":row.terrain_note,"created_at":row.created_at,"algorithm_version":row.algorithm_version}
