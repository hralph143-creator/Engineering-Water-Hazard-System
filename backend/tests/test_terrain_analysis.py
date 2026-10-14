import asyncio
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException, UploadFile
from rasterio.warp import transform
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.api import dashboard as dashboard_api
from backend.app.api import terrain as terrain_api
from backend.app.db.database import Base
from backend.app.db.models import (
    ConstructionEquipment, ConstructionProject, EquipmentPosition, EquipmentTerrainSnapshot,
    RiskAssessment, TerrainDataset, TerrainFusionAnalysis, WorkArea, WorkAreaTerrainProfile,
)
from backend.app.services import risk_report_service, terrain_service
from backend.app.services.ai_context import build_risk_assessment_context
from backend.app.services.ai_prompts import RISK_ASSESSMENT_GUIDE


POLYGON={"type":"Polygon","coordinates":[[[0,40],[40,40],[40,0],[0,0],[0,40]]]}
MULTI={"type":"MultiPolygon","coordinates":[[[[0,40],[20,40],[20,20],[0,20],[0,40]]],[[[20,20],[40,20],[40,0],[20,0],[20,20]]]]}


def _engine(tmp_path):
    engine=create_engine(f"sqlite:///{(tmp_path/'terrain.db').as_posix()}")
    @event.listens_for(engine,"connect")
    def _fk(connection,_): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine); return engine


def _cfg(tmp_path,percentile=20):
    outputs=tmp_path/"outputs"; uploads=tmp_path/"uploads"; outputs.mkdir(exist_ok=True); uploads.mkdir(exist_ok=True)
    return SimpleNamespace(paths=SimpleNamespace(outputs=outputs,uploads=uploads),terrain=SimpleNamespace(version="terrain-analysis-v1",low_elevation_percentile=percentile),equipment_position=SimpleNamespace(version="equipment-water-link-v1",position_stale_hours=24))


def _write_dem(path,values=None,crs="EPSG:3857",count=1,nodata=-9999):
    values=np.array(values if values is not None else [[100,101,102,103],[99,100,101,102],[98,99,100,101],[97,98,99,100]],dtype=np.float32)
    with rasterio.open(path,"w",driver="GTiff",width=4,height=4,count=count,dtype="float32",crs=crs,transform=rasterio.Affine(10,0,0,0,-10,40),nodata=nodata) as dst:
        for band in range(1,count+1): dst.write(values,band)
    return path


def _project(db,status="active"):
    row=ConstructionProject(id="p1",project_name="矿区",project_code="P1",project_type="open_pit_mine",status=status); db.add(row); db.commit(); return row


def _dem(db,tmp_path,values=None,crs="EPSG:3857"):
    path=_write_dem(tmp_path/"dem.tif",values,crs); meta=terrain_service.inspect_dem(path)
    row=TerrainDataset(id="dem1",project_id="p1",name="矿区DEM",file_path=str(path),file_hash=terrain_service.sha256_file(path),source_name="用户提供",**meta)
    db.add(row); db.commit(); return row


def _area(db,geometry=POLYGON):
    row=WorkArea(id="a1",project_id="p1",area_name="西侧采坑",area_code="A1",area_type="pit",geometry_type=geometry["type"],geometry_geojson=json.dumps(geometry),crs="EPSG:3857",status="active"); db.add(row); db.commit(); return row


def _risk(db,tmp_path):
    path=tmp_path/"risk.tif"; values=np.array([[3,3,2,1],[3,2,2,1],[1,1,1,1],[0,1,2,3]],dtype=np.uint8)
    with rasterio.open(path,"w",driver="GTiff",width=4,height=4,count=1,dtype="uint8",crs="EPSG:3857",transform=rasterio.Affine(10,0,0,0,-10,40),nodata=0) as dst: dst.write(values,1)
    row=RiskAssessment(id="r1",project_id="p1",assessment_name="研判",source_type="change_analysis",source_t1_name="T1",source_t2_name="T2",status="completed",attention_level="中水害关注",rule_version="water-risk-v1",operation_note="辅助",risk_reason_codes="[]",risk_reason_text="[]",inspection_advice="[]",risk_geotiff_path=str(path),completed_at=datetime.now(timezone.utc)); db.add(row); db.commit(); return row


def _equipment_position(db,x=5,y=35):
    equipment=ConstructionEquipment(id="e1",project_id="p1",equipment_code="TRUCK-03",equipment_name="矿卡03",equipment_type="mining_truck",business_status="idle"); db.add(equipment)
    position=EquipmentPosition(id="pos1",equipment_id="e1",project_id="p1",x=x,y=y,crs="EPSG:3857",position_source="manual",recorded_at=datetime.now(timezone.utc)); db.add(position); db.commit(); return equipment,position


@pytest.mark.parametrize("table",["terrain_datasets","work_area_terrain_profiles","terrain_fusion_analyses","equipment_terrain_snapshots"])
def test_terrain_tables_created(tmp_path,table): assert table in inspect(_engine(tmp_path)).get_table_names()


def test_dem_inspection_statistics(tmp_path):
    meta=terrain_service.inspect_dem(_write_dem(tmp_path/"d.tif")); assert meta["band_count"]==1 and meta["min_elevation"]==97 and meta["max_elevation"]==103 and meta["valid_pixel_count"]==16


def test_non_geotiff_rejected(tmp_path):
    path=tmp_path/"x.txt"; path.write_text("not raster")
    with pytest.raises(terrain_service.TerrainError): terrain_service.inspect_dem(path)


def test_multiband_dem_rejected(tmp_path):
    with pytest.raises(terrain_service.TerrainError,match="单波段"): terrain_service.inspect_dem(_write_dem(tmp_path/"d.tif",count=2))


def test_dem_without_crs_rejected(tmp_path):
    with pytest.raises(terrain_service.TerrainError,match="CRS"): terrain_service.inspect_dem(_write_dem(tmp_path/"d.tif",crs=None))


def test_nodata_nan_inf_excluded(tmp_path):
    values=[[100,101,np.nan,103],[99,np.inf,101,102],[98,99,-9999,101],[97,98,99,100]]
    meta=terrain_service.inspect_dem(_write_dem(tmp_path/"d.tif",values)); assert meta["valid_pixel_count"]==13 and meta["nodata_ratio"]==pytest.approx(3/16)


def test_all_invalid_dem_rejected(tmp_path):
    with pytest.raises(terrain_service.TerrainError,match="有效高程"): terrain_service.inspect_dem(_write_dem(tmp_path/"d.tif",np.full((4,4),-9999)))


def test_sha256_same_content_matches(tmp_path):
    a=_write_dem(tmp_path/"a.tif"); b=tmp_path/"b.tif"; b.write_bytes(a.read_bytes()); assert terrain_service.sha256_file(a)==terrain_service.sha256_file(b)


def test_dem_project_relationship(tmp_path):
    with Session(_engine(tmp_path)) as db: _project(db); assert _dem(db,tmp_path).project_id=="p1"


def test_upload_dem_and_duplicate(tmp_path,monkeypatch):
    cfg=_cfg(tmp_path); monkeypatch.setattr(terrain_api,"get_config",lambda:cfg)
    with Session(_engine(tmp_path)) as db:
        _project(db); source=_write_dem(tmp_path/"source.tif")
        first=UploadFile(file=io.BytesIO(source.read_bytes()),filename="mine.tif")
        assert terrain_api.upload_dem(first,"p1","source","","","","",db)["data"]["name"]=="mine.tif"
        duplicate=UploadFile(file=io.BytesIO(source.read_bytes()),filename="copy.tif")
        with pytest.raises(HTTPException) as exc: terrain_api.upload_dem(duplicate,"p1","","","","","",db)
        assert exc.value.status_code==409 and "已存在" in exc.value.detail


@pytest.mark.parametrize("percentile",[10,20,50])
def test_low_elevation_percentile_and_derivatives(tmp_path,monkeypatch,percentile):
    cfg=_cfg(tmp_path,percentile); monkeypatch.setattr(terrain_service,"get_config",lambda:cfg)
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); summary=terrain_service.ensure_derivatives(dem); db.commit()
        assert summary["low_elevation_percentile"]==percentile and Path(dem.slope_path).is_file() and Path(dem.low_mask_path).is_file()


def test_dem_preview_outputs(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); terrain_service.ensure_derivatives(dem)
        assert all(Path(x).is_file() for x in (dem.elevation_preview_path,dem.slope_preview_path,dem.low_mask_preview_path))


def test_slope_is_degrees_and_positive(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); terrain_service.ensure_derivatives(dem)
        with rasterio.open(dem.slope_path) as src:
            values=src.read(1); valid=values!=src.nodata; assert values[valid].mean()>0 and values[valid].max()<90


def test_geographic_dem_slope_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path,crs="EPSG:4326")
        with pytest.raises(terrain_service.TerrainError,match="地理坐标"): terrain_service.ensure_derivatives(dem)


def test_derivative_cache_reused(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); first=terrain_service.ensure_derivatives(dem); mtime=Path(dem.slope_path).stat().st_mtime_ns; second=terrain_service.ensure_derivatives(dem)
        assert first==second and Path(dem.slope_path).stat().st_mtime_ns==mtime


@pytest.mark.parametrize("geometry",[POLYGON,MULTI])
def test_polygon_and_multipolygon_profile(tmp_path,monkeypatch,geometry):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); area=_area(db,geometry); row=terrain_service.analyze_work_area(db,area,dem)
        assert row.valid_pixels>0 and row.mean_elevation is not None and row.mean_slope is not None and 0<=row.low_elevation_ratio<=1


def test_profile_statistics_and_persistence(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); area=_area(db); row=terrain_service.analyze_work_area(db,area,dem)
        assert row.elevation_range==pytest.approx(row.max_elevation-row.min_elevation) and row.terrain_rule_version=="terrain-analysis-v1" and db.get(WorkAreaTerrainProfile,row.id)


def test_profile_repeat_updates_same_record(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); area=_area(db); a=terrain_service.analyze_work_area(db,area,dem); b=terrain_service.analyze_work_area(db,area,dem); assert a.id==b.id


def test_profile_nodata_is_excluded(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    values=[[100,101,102,103],[99,-9999,101,102],[98,99,100,101],[97,98,99,100]]
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path,values); area=_area(db); row=terrain_service.analyze_work_area(db,area,dem); assert row.valid_pixels<16


def test_risk_dem_fusion_all_zone_statistics(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); risk=_risk(db,tmp_path); row=terrain_service.analyze_fusion(db,risk,dem)
        assert row.high_attention_mean_elevation is not None and row.high_attention_mean_slope is not None
        assert row.inspection_mean_elevation is not None and row.general_mean_elevation is not None
        assert 0<=row.high_attention_low_elevation_ratio<=1 and Path(row.summary_json_path).is_file()


def test_fusion_persists_and_repeats(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); risk=_risk(db,tmp_path); a=terrain_service.analyze_fusion(db,risk,dem); b=terrain_service.analyze_fusion(db,risk,dem)
        assert a.id==b.id and a.algorithm_version=="terrain-analysis-v1" and db.get(TerrainFusionAnalysis,a.id)


@pytest.mark.parametrize("x,y,status",[(5,35,"completed"),(100,100,"outside_extent")])
def test_equipment_point_terrain_sampling(tmp_path,monkeypatch,x,y,status):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); equipment,position=_equipment_position(db,x,y); row=terrain_service.analyze_equipment_terrain(db,equipment,position,dem)
        assert row.status==status and ((row.elevation is not None and row.slope is not None and row.is_low_elevation is not None) if status=="completed" else row.elevation is None)


def test_equipment_point_nodata(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path)); values=[[-9999,101,102,103],[99,100,101,102],[98,99,100,101],[97,98,99,100]]
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path,values); equipment,position=_equipment_position(db); row=terrain_service.analyze_equipment_terrain(db,equipment,position,dem); assert row.status=="nodata"


def test_equipment_point_is_transformed_to_dem_crs(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    longitude,latitude=transform("EPSG:3857","EPSG:4326",[5],[35])
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); equipment,position=_equipment_position(db)
        position.x=longitude[0]; position.y=latitude[0]; position.crs="EPSG:4326"; db.commit()
        row=terrain_service.analyze_equipment_terrain(db,equipment,position,dem)
        assert row.status=="completed" and row.elevation==pytest.approx(100)


def test_equipment_snapshot_persists_and_version(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); equipment,position=_equipment_position(db); row=terrain_service.analyze_equipment_terrain(db,equipment,position,dem)
        assert db.get(EquipmentTerrainSnapshot,row.id) and row.algorithm_version=="terrain-analysis-v1"


@pytest.mark.parametrize("reference_model",[WorkAreaTerrainProfile,TerrainFusionAnalysis,EquipmentTerrainSnapshot])
def test_dem_delete_reference_protection(tmp_path,monkeypatch,reference_model):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path)
        if reference_model is WorkAreaTerrainProfile: row=WorkAreaTerrainProfile(id="ref",project_id="p1",work_area_id=_area(db).id,dem_id=dem.id,status="completed")
        elif reference_model is TerrainFusionAnalysis: row=TerrainFusionAnalysis(id="ref",risk_assessment_id=_risk(db,tmp_path).id,dem_id=dem.id,project_id="p1",status="completed")
        else:
            equipment,position=_equipment_position(db); row=EquipmentTerrainSnapshot(id="ref",equipment_id=equipment.id,position_id=position.id,dem_id=dem.id,project_id="p1",status="completed")
        db.add(row); db.commit()
        with pytest.raises(HTTPException) as exc: terrain_api.delete_dem(dem.id,db)
        assert exc.value.status_code==409


def _read_stream(response):
    async def read():
        chunks=[]
        async for chunk in response.body_iterator: chunks.append(chunk if isinstance(chunk,bytes) else chunk.encode())
        return b"".join(chunks).decode("utf-8-sig")
    return asyncio.run(read())


def test_all_terrain_csv_exports(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); area=_area(db); risk=_risk(db,tmp_path); equipment,position=_equipment_position(db)
        terrain_service.analyze_work_area(db,area,dem); terrain_service.analyze_fusion(db,risk,dem); terrain_service.analyze_equipment_terrain(db,equipment,position,dem)
        assert "terrain-analysis-v1" in _read_stream(terrain_api.area_terrain_csv(area.id,db))
        assert "high_attention" in _read_stream(terrain_api.fusion_csv(risk.id,db))
        equipment_csv=_read_stream(terrain_api.equipment_terrain_csv(equipment.id,db))
        assert "equipment_id" in equipment_csv and "terrain-analysis-v1" in equipment_csv


@pytest.mark.parametrize("with_terrain",[False,True])
def test_html_and_pdf_report_optional_terrain(tmp_path,with_terrain,monkeypatch):
    with Session(_engine(tmp_path)) as db:
        _project(db); risk=_risk(db,tmp_path)
        terrain={"dem_name":"矿区DEM","source_name":"用户提供","fusion":{"algorithm_version":"terrain-analysis-v1","high_attention":{"mean_elevation":98,"mean_slope":6,"low_elevation_ratio":.4},"inspection":{},"general":{}},"profiles":[],"equipment":[]} if with_terrain else None
        html=risk_report_service.risk_report_html(risk,terrain=terrain); assert ("地形辅助分析" in html) is with_terrain
        monkeypatch.setattr(risk_report_service,"render_html_pdf",lambda document: b"%PDF-terrain" if (("地形辅助分析" in document) is with_terrain) else b"")
        assert risk_report_service.risk_report_pdf(risk,terrain=terrain)==b"%PDF-terrain"


def test_ai_context_uses_real_dem(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); risk=_risk(db,tmp_path); terrain_service.analyze_fusion(db,risk,dem)
        raw=json.dumps(build_risk_assessment_context(db,risk.id),ensure_ascii=False); assert "矿区DEM" in raw and "terrain-analysis-v1" in raw


@pytest.mark.parametrize("forbidden",["预测积水深度","边坡稳定","道路承载","设备通行安全"])
def test_ai_prompt_prohibits_overclaiming(forbidden): assert forbidden in RISK_ASSESSMENT_GUIDE


def test_dashboard_terrain_counts(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain_service,"get_config",lambda:_cfg(tmp_path)); monkeypatch.setattr(dashboard_api,"get_config",lambda:_cfg(tmp_path)); monkeypatch.setattr(dashboard_api,"get_model_service",lambda:SimpleNamespace(status=lambda:{}))
    with Session(_engine(tmp_path)) as db:
        _project(db); dem=_dem(db,tmp_path); area=_area(db); risk=_risk(db,tmp_path); terrain_service.analyze_work_area(db,area,dem); terrain_service.analyze_fusion(db,risk,dem)
        data=dashboard_api.dashboard_summary(db)["data"]["terrain"]; assert data["dem_count"]==1 and data["profiled_area_count"]==1 and data["latest_high_attention_low_elevation_ratio"] is not None


def test_legacy_database_create_all_preserves_existing_data(tmp_path):
    engine=create_engine(f"sqlite:///{(tmp_path/'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_business (id VARCHAR PRIMARY KEY, value VARCHAR)")); connection.execute(text("INSERT INTO legacy_business VALUES ('1','kept')"))
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM legacy_business WHERE id='1'"))=="kept"
        assert {"terrain_datasets","work_area_terrain_profiles","terrain_fusion_analyses","equipment_terrain_snapshots"}.issubset(set(inspect(connection).get_table_names()))


def test_visible_ui_never_equates_low_elevation_with_certain_flooding():
    paths=["frontend/src/views/DatasetsView.vue","frontend/src/views/RiskView.vue","frontend/src/views/AreasView.vue","frontend/src/views/EquipmentView.vue"]
    source="".join(open(path,encoding="utf-8").read() for path in paths); assert "必然积水" in source and "低高程=必然积水" not in source
