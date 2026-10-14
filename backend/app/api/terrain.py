import csv
import io
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.database import get_db
from ..db.models import (
    ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentTerrainSnapshot,
    RiskAssessment, TerrainDataset, TerrainFusionAnalysis, WorkArea, WorkAreaTerrainProfile,
)
from ..schemas.common import ok
from ..schemas.terrain import TerrainRunRequest
from ..services.equipment_service import latest_position
from ..services.terrain_service import (
    TerrainError, analyze_equipment_terrain, analyze_fusion, analyze_work_area, ensure_derivatives,
    inspect_dem, serialize_dem, serialize_fusion, serialize_profile, serialize_snapshot, sha256_file,
)

router=APIRouter(tags=["terrain"])


def _dem(dem_id:str,db:Session)->TerrainDataset:
    row=db.get(TerrainDataset,dem_id)
    if not row: raise HTTPException(status_code=404,detail="DEM数据不存在")
    return row


@router.post("/environment-data/dem")
def upload_dem(file:UploadFile=File(...),project_id:str|None=Form(None),source_name:str=Form(""),capture_date:str=Form(""),
               resolution_note:str=Form(""),vertical_datum_note:str=Form(""),description:str=Form(""),db:Session=Depends(get_db)):
    filename=Path(file.filename or "unnamed").name
    if Path(filename).suffix.lower() not in {".tif",".tiff"}: raise HTTPException(status_code=400,detail="仅支持 .tif 或 .tiff 格式的GeoTIFF DEM")
    if project_id and not db.get(ConstructionProject,project_id): raise HTTPException(status_code=404,detail="施工项目不存在")
    dem_id=str(uuid.uuid4()); directory=get_config().paths.uploads/"terrain"; directory.mkdir(parents=True,exist_ok=True); target=directory/f"{dem_id}{Path(filename).suffix.lower()}"
    try:
        with target.open("wb") as output: shutil.copyfileobj(file.file,output)
        digest=sha256_file(target); duplicate=db.scalar(select(TerrainDataset).where(TerrainDataset.file_hash==digest))
        if duplicate: raise HTTPException(status_code=409,detail="该DEM数据已存在。")
        metadata=inspect_dem(target)
        row=TerrainDataset(id=dem_id,project_id=project_id,name=filename,file_path=str(target),file_hash=digest,
                           source_name=source_name.strip(),capture_date=capture_date.strip(),resolution_note=resolution_note.strip(),
                           vertical_datum_note=vertical_datum_note.strip(),description=description.strip(),**metadata)
        db.add(row); db.commit(); db.refresh(row)
        return ok(serialize_dem(row),"DEM数据导入成功")
    except HTTPException:
        db.rollback(); target.unlink(missing_ok=True); raise
    except TerrainError as exc:
        db.rollback(); target.unlink(missing_ok=True); raise HTTPException(status_code=400,detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback(); target.unlink(missing_ok=True); raise HTTPException(status_code=409,detail="该DEM数据已存在。") from exc


@router.get("/environment-data/dem")
def list_dem(project_id:str|None=None,db:Session=Depends(get_db)):
    statement=select(TerrainDataset)
    if project_id: statement=statement.where(TerrainDataset.project_id==project_id)
    return ok([serialize_dem(row) for row in db.scalars(statement.order_by(TerrainDataset.created_at.desc())).all()])


@router.get("/environment-data/dem/{dem_id}")
def dem_detail(dem_id:str,db:Session=Depends(get_db)):
    row=_dem(dem_id,db); data=serialize_dem(row)
    data["profile_count"]=len(db.scalars(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.dem_id==dem_id)).all())
    data["fusion_count"]=len(db.scalars(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.dem_id==dem_id)).all())
    data["equipment_snapshot_count"]=len(db.scalars(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.dem_id==dem_id)).all())
    return ok(data)


@router.post("/environment-data/dem/{dem_id}/derive")
def derive_dem(dem_id:str,db:Session=Depends(get_db)):
    row=_dem(dem_id,db)
    try: summary=ensure_derivatives(row); db.commit()
    except TerrainError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok({"dataset":serialize_dem(row),"summary":summary},"DEM地形衍生成果已生成或复用")


@router.get("/environment-data/dem/{dem_id}/files/{kind}")
def dem_file(dem_id:str,kind:str,db:Session=Depends(get_db)):
    row=_dem(dem_id,db); mapping={"elevation-preview":(row.elevation_preview_path,"image/png"),"slope":(row.slope_path,"image/tiff"),"slope-preview":(row.slope_preview_path,"image/png"),"low-mask":(row.low_mask_path,"image/tiff"),"low-mask-preview":(row.low_mask_preview_path,"image/png"),"summary":(row.summary_json_path,"application/json")}
    if kind not in mapping: raise HTTPException(status_code=404,detail="DEM成果类型不存在")
    path,media=mapping[kind]; target=Path(path) if path else None
    if not target or not target.is_file(): raise HTTPException(status_code=404,detail="DEM成果尚未生成")
    return FileResponse(target,media_type=media,filename=target.name)


@router.delete("/environment-data/dem/{dem_id}")
def delete_dem(dem_id:str,db:Session=Depends(get_db)):
    row=_dem(dem_id,db)
    referenced=any((db.scalar(select(model.id).where(model.dem_id==dem_id).limit(1)) for model in (WorkAreaTerrainProfile,TerrainFusionAnalysis,EquipmentTerrainSnapshot)))
    if referenced: raise HTTPException(status_code=409,detail="该DEM已被地形分析结果引用，请先清理相关分析记录。")
    paths=[row.file_path,row.elevation_preview_path,row.slope_path,row.slope_preview_path,row.low_mask_path,row.low_mask_preview_path,row.summary_json_path]
    db.delete(row); db.commit()
    for value in paths:
        if value: Path(value).unlink(missing_ok=True)
    return ok(message="DEM数据已删除")


@router.post("/work-areas/{area_id}/terrain-analysis")
def run_area_terrain(area_id:str,payload:TerrainRunRequest,db:Session=Depends(get_db)):
    area=db.get(WorkArea,area_id)
    if not area: raise HTTPException(status_code=404,detail="施工区域不存在")
    dem=_dem(payload.dem_id,db)
    try: row=analyze_work_area(db,area,dem)
    except TerrainError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok(serialize_profile(row,dem),"施工区域地形分析已完成")


@router.get("/work-areas/{area_id}/terrain-analysis")
def area_terrain(area_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.work_area_id==area_id).order_by(WorkAreaTerrainProfile.created_at.desc())).all()
    return ok([serialize_profile(row,db.get(TerrainDataset,row.dem_id)) for row in rows])


@router.get("/work-areas/{area_id}/terrain-analysis.csv")
def area_terrain_csv(area_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(WorkAreaTerrainProfile).where(WorkAreaTerrainProfile.work_area_id==area_id)).all(); stream=io.StringIO(); writer=csv.writer(stream)
    writer.writerow(["work_area_id","dem_id","dem_name","min_elevation_m","max_elevation_m","mean_elevation_m","elevation_range_m","mean_slope_degree","max_slope_degree","low_elevation_ratio","valid_pixels","rule_version"])
    for row in rows: writer.writerow([area_id,row.dem_id,db.get(TerrainDataset,row.dem_id).name,row.min_elevation,row.max_elevation,row.mean_elevation,row.elevation_range,row.mean_slope,row.max_slope,row.low_elevation_ratio,row.valid_pixels,row.terrain_rule_version])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv")


@router.post("/risk-assessments/{assessment_id}/terrain-fusion")
def run_fusion(assessment_id:str,payload:TerrainRunRequest,db:Session=Depends(get_db)):
    risk=db.get(RiskAssessment,assessment_id)
    if not risk: raise HTTPException(status_code=404,detail="风险研判记录不存在")
    dem=_dem(payload.dem_id,db)
    try: row=analyze_fusion(db,risk,dem)
    except TerrainError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok(serialize_fusion(row,dem),"风险与地形融合分析已完成")


@router.get("/risk-assessments/{assessment_id}/terrain-fusion")
def risk_fusions(assessment_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id==assessment_id).order_by(TerrainFusionAnalysis.created_at.desc())).all()
    return ok([serialize_fusion(row,db.get(TerrainDataset,row.dem_id)) for row in rows])


@router.get("/risk-assessments/{assessment_id}/terrain-fusion.csv")
def fusion_csv(assessment_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(TerrainFusionAnalysis).where(TerrainFusionAnalysis.risk_assessment_id==assessment_id)).all(); stream=io.StringIO(); writer=csv.writer(stream)
    writer.writerow(["risk_assessment_id","dem_id","zone","mean_elevation_m","mean_slope_degree","low_elevation_ratio","rule_version"])
    for row in rows:
        for zone,prefix in (("high_attention","high_attention"),("inspection","inspection"),("general","general")):
            writer.writerow([assessment_id,row.dem_id,zone,getattr(row,f"{prefix}_mean_elevation"),getattr(row,f"{prefix}_mean_slope"),getattr(row,f"{prefix}_low_elevation_ratio"),row.algorithm_version])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv")


@router.post("/equipment/{equipment_id}/terrain-analysis")
def run_equipment_terrain(equipment_id:str,payload:TerrainRunRequest,db:Session=Depends(get_db)):
    equipment=db.get(ConstructionEquipment,equipment_id)
    if not equipment: raise HTTPException(status_code=404,detail="工程机械不存在")
    position=db.get(EquipmentPosition,payload.position_id) if payload.position_id else latest_position(db,equipment_id)
    if not position or position.equipment_id!=equipment_id: raise HTTPException(status_code=400,detail="设备暂无可用位置快照")
    dem=_dem(payload.dem_id,db)
    try: row=analyze_equipment_terrain(db,equipment,position,dem)
    except TerrainError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return ok(serialize_snapshot(row,dem),"设备位置地形分析已完成")


@router.get("/equipment/{equipment_id}/terrain-analysis")
def equipment_terrain(equipment_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.equipment_id==equipment_id).order_by(EquipmentTerrainSnapshot.created_at.desc())).all()
    return ok([serialize_snapshot(row,db.get(TerrainDataset,row.dem_id)) for row in rows])


@router.get("/equipment/{equipment_id}/terrain-analysis.csv")
def equipment_terrain_csv(equipment_id:str,db:Session=Depends(get_db)):
    rows=db.scalars(select(EquipmentTerrainSnapshot).where(EquipmentTerrainSnapshot.equipment_id==equipment_id)).all(); stream=io.StringIO(); writer=csv.writer(stream)
    writer.writerow(["equipment_id","position_id","dem_id","dem_name","status","elevation_m","slope_degree","is_low_elevation","analysis_time","rule_version"])
    for row in rows: writer.writerow([equipment_id,row.position_id,row.dem_id,db.get(TerrainDataset,row.dem_id).name,row.status,row.elevation,row.slope,row.is_low_elevation,row.created_at,row.algorithm_version])
    return StreamingResponse(iter([stream.getvalue().encode("utf-8-sig")]),media_type="text/csv")
