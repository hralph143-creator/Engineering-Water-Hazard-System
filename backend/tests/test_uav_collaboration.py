import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from rasterio import Affine
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.db.models import (
    ConstructionProject, RiskAssessment, UavDataset, UavRiskOverlay,
    UavVerificationRecord, UavWorkAreaCoverage, WorkArea,
)
from backend.app.schemas.uav import UavVerificationCreate
from backend.app.services import risk_report_service, uav_service
from backend.app.services.ai_context import build_risk_assessment_context
from backend.app.services.ai_prompts import AI_SYSTEM_PROMPT, RISK_ASSESSMENT_GUIDE
from backend.app.services.risk_service import RiskAssessmentError, delete_risk_assessment
from backend.app.api import uav as uav_api


POLYGON = {"type":"Polygon","coordinates":[[[0,40],[40,40],[40,0],[0,0],[0,40]]]}
MULTI = {"type":"MultiPolygon","coordinates":[[[[0,40],[20,40],[20,20],[0,20],[0,40]]],[[[20,20],[40,20],[40,0],[20,0],[20,20]]]]}


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path/'uav.db').as_posix()}")
    @event.listens_for(engine, "connect")
    def _fk(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine


def _cfg(tmp_path):
    uploads, outputs = tmp_path/"uploads", tmp_path/"outputs"
    uploads.mkdir(exist_ok=True); outputs.mkdir(exist_ok=True)
    return SimpleNamespace(paths=SimpleNamespace(uploads=uploads, outputs=outputs))


def _write_uav(path, count=3, crs="EPSG:3857", transform=None, nodata=0, size=4, alpha=True):
    transform = transform if transform is not None else Affine(10,0,0,0,-10,40)
    with rasterio.open(path,"w",driver="GTiff",width=size,height=size,count=count,dtype="uint8",crs=crs,transform=transform,nodata=nodata) as dst:
        for band in range(1, count+1):
            data=np.full((size,size),band*50,dtype="uint8")
            if count==4 and band==4: data[:]=255 if alpha else 0
            dst.write(data,band)
    return path


def _project(db):
    row=ConstructionProject(id="p1",project_name="测试项目",project_code="P1",project_type="open_pit_mine",status="active")
    db.add(row); db.commit(); return row


def _uav(db,tmp_path,count=3,capture=True):
    path=_write_uav(tmp_path/f"uav{count}.tif",count=count); metadata=uav_service.inspect_uav(path)
    row=UavDataset(id=f"u{count}",project_id="p1",name="航飞正射.tif",file_path=str(path),file_hash=uav_service.sha256_file(path),capture_time=datetime.now(timezone.utc) if capture else None,source_name="现场航飞",**metadata)
    db.add(row); db.commit(); return row


def _area(db, geometry=POLYGON, area_id="a1"):
    row=WorkArea(id=area_id,project_id="p1",area_name="东侧道路",area_code=area_id,area_type="haul_road",geometry_type=geometry["type"],geometry_geojson=json.dumps(geometry),crs="EPSG:3857",status="active")
    db.add(row); db.commit(); return row


def _risk(db,tmp_path):
    path=tmp_path/"risk.tif"; data=np.array([[3,3,2,1],[3,2,2,1],[1,1,1,1],[0,1,2,3]],dtype="uint8")
    with rasterio.open(path,"w",driver="GTiff",width=4,height=4,count=1,dtype="uint8",crs="EPSG:3857",transform=Affine(10,0,0,0,-10,40),nodata=0) as dst: dst.write(data,1)
    row=RiskAssessment(id="r1",project_id="p1",assessment_name="研判",source_type="change_analysis",source_t1_name="T1",source_t2_name="T2",status="completed",attention_level="中水害关注",rule_version="water-risk-v1",operation_note="辅助",risk_reason_codes="[]",risk_reason_text="[]",inspection_advice="[]",risk_geotiff_path=str(path),completed_at=datetime.now(timezone.utc))
    db.add(row); db.commit(); return row


@pytest.mark.parametrize("table",["uav_datasets","uav_work_area_coverages","uav_risk_overlays","uav_verification_records"])
def test_uav_tables_created(tmp_path,table): assert table in inspect(_engine(tmp_path)).get_table_names()


@pytest.mark.parametrize("bands",[3,4])
def test_rgb_and_rgba_uav_inspection(tmp_path,bands):
    meta=uav_service.inspect_uav(_write_uav(tmp_path/f"{bands}.tif",count=bands)); assert meta["band_count"]==bands and meta["valid_pixel_count"]==16


def test_uav_without_crs_rejected(tmp_path):
    with pytest.raises(uav_service.UavError,match="缺少空间参考"): uav_service.inspect_uav(_write_uav(tmp_path/"x.tif",crs=None))


@pytest.mark.parametrize("bands",[1,2,5])
def test_unsupported_band_structure_rejected(tmp_path,bands):
    with pytest.raises(uav_service.UavError,match="RGB"): uav_service.inspect_uav(_write_uav(tmp_path/f"{bands}.tif",count=bands))


def test_invalid_transform_rejected(tmp_path):
    with pytest.raises(uav_service.UavError,match="Transform|Bounds"): uav_service.inspect_uav(_write_uav(tmp_path/"x.tif",transform=Affine(0,0,0,0,0,0)))


def test_empty_alpha_rejected(tmp_path):
    with pytest.raises(uav_service.UavError,match="有效像素"): uav_service.inspect_uav(_write_uav(tmp_path/"x.tif",count=4,alpha=False))


def test_sha256_is_stable(tmp_path):
    path=_write_uav(tmp_path/"x.tif"); assert uav_service.sha256_file(path)==uav_service.sha256_file(path) and len(uav_service.sha256_file(path))==64


def test_file_hash_unique_constraint(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); one=_uav(db,tmp_path); db.add(UavDataset(id="other",project_id="p1",name="重复",file_path=str(tmp_path/"other.tif"),file_hash=one.file_hash,width=4,height=4,band_count=3,dtype="uint8",crs="EPSG:3857",transform="[]",bounds="[]",pixel_size_x=10,pixel_size_y=10,valid_pixel_count=16,nodata_ratio=0))
        with pytest.raises(IntegrityError): db.commit()


def test_multiple_flights_are_allowed(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); one=_uav(db,tmp_path); path=_write_uav(tmp_path/"later.tif",size=5); meta=uav_service.inspect_uav(path)
        db.add(UavDataset(id="u2",project_id="p1",name="第二航次",file_path=str(path),file_hash=uav_service.sha256_file(path),**meta)); db.commit()
        assert db.query(UavDataset).count()==2 and one.project_id=="p1"


def test_preview_generated_and_cached(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); row=_uav(db,tmp_path); path=uav_service.ensure_preview(row); stamp=path.stat().st_mtime_ns
        assert path.is_file() and uav_service.ensure_preview(row).stat().st_mtime_ns==stamp


def test_large_preview_is_downsampled(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path)); path=_write_uav(tmp_path/"large.tif",size=1700); meta=uav_service.inspect_uav(path)
    with Session(_engine(tmp_path)) as db:
        _project(db); row=UavDataset(id="large",project_id="p1",name="large",file_path=str(path),file_hash=uav_service.sha256_file(path),**meta); db.add(row); db.commit(); preview=uav_service.ensure_preview(row)
        with rasterio.open(preview) as src: assert max(src.width,src.height)<=uav_service.MAX_PREVIEW_SIDE


@pytest.mark.parametrize("geometry",[POLYGON,MULTI])
def test_polygon_and_multipolygon_full_coverage(tmp_path,monkeypatch,geometry):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); area=_area(db,geometry); row=uav_service.analyze_work_area(db,uav,area)
        assert row.coverage_status=="FULL" and row.coverage_ratio==pytest.approx(1)


def test_partial_and_none_coverage(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    partial={"type":"Polygon","coordinates":[[[20,40],[60,40],[60,0],[20,0],[20,40]]]}; none={"type":"Polygon","coordinates":[[[100,40],[140,40],[140,0],[100,0],[100,40]]]}
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); p=_area(db,partial,"p"); n=_area(db,none,"n")
        assert uav_service.analyze_work_area(db,uav,p).coverage_status=="PARTIAL"
        assert uav_service.analyze_work_area(db,uav,n).coverage_status=="NONE"


def test_nodata_reduces_coverage(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path)); path=_write_uav(tmp_path/"nodata.tif")
    with rasterio.open(path,"r+") as dst:
        for band in range(1,4):
            data=dst.read(band); data[:2,:]=0; dst.write(data,band)
    meta=uav_service.inspect_uav(path)
    with Session(_engine(tmp_path)) as db:
        _project(db); row=UavDataset(id="u",project_id="p1",name="n",file_path=str(path),file_hash=uav_service.sha256_file(path),**meta); db.add(row); db.commit()
        coverage=uav_service.analyze_work_area(db,row,_area(db)); assert coverage.coverage_status=="PARTIAL" and coverage.coverage_ratio==pytest.approx(.5)


def test_cross_project_area_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); db.add(ConstructionProject(id="p2",project_name="二",project_code="P2",project_type="road")); db.commit()
        area=WorkArea(id="a",project_id="p2",area_name="a",area_code="a",area_type="other",geometry_type="Polygon",geometry_geojson=json.dumps(POLYGON),crs="EPSG:3857"); db.add(area); db.commit()
        with pytest.raises(uav_service.UavError,match="同一项目"): uav_service.analyze_work_area(db,uav,area)


def test_risk_overlay_zone_statistics_and_crops(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); before=risk.attention_level
        row=uav_service.analyze_risk_overlay(db,risk,uav)
        assert row.high_attention_coverage_ratio==pytest.approx(1) and row.inspection_coverage_ratio==pytest.approx(1)
        assert Path(row.high_attention_crop_path).is_file() and risk.attention_level==before and row.analysis_version=="uav-spatial-v1"


def test_overlay_time_reference_limited_without_capture(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path,capture=False); row=uav_service.analyze_risk_overlay(db,_risk(db,tmp_path),uav)
        assert row.time_reference_status=="TIME_REFERENCE_LIMITED" and row.time_difference_hours is None


def test_overlay_time_difference(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); uav.capture_time=risk.completed_at+timedelta(hours=3.2); db.commit()
        assert uav_service.analyze_risk_overlay(db,risk,uav).time_difference_hours==pytest.approx(3.2)


@pytest.mark.parametrize("status",["PENDING","REVIEWED","INSUFFICIENT_IMAGE"])
def test_verification_statuses(status):
    assert UavVerificationCreate(project_id="p",uav_dataset_id="u",risk_assessment_id="r",verification_status=status).verification_status==status


@pytest.mark.parametrize("kind",["VISIBLE_WATER","POSSIBLE_WATER","MUDDY_OR_WET_SURFACE","NO_OBVIOUS_WATER","IMAGE_UNCLEAR","OTHER"])
def test_manual_observation_types(kind):
    assert UavVerificationCreate(project_id="p",uav_dataset_id="u",risk_assessment_id="r",observation_type=kind).observation_type==kind


def test_invalid_verification_choices_rejected():
    with pytest.raises(ValidationError): UavVerificationCreate(project_id="p",uav_dataset_id="u",risk_assessment_id="r",verification_status="SAFE")


def test_verification_serialization_is_explicitly_manual(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); area=_area(db)
        row=UavVerificationRecord(id="v",project_id="p1",uav_dataset_id=uav.id,risk_assessment_id=risk.id,work_area_id=area.id,verification_status="REVIEWED",observation_type="VISIBLE_WATER",finding_text="道路北侧可见连续水面",structure_version="uav-verification-v1")
        db.add(row); db.commit(); data=uav_service.serialize_verification(row,uav,area)
        assert data["observation_type_label"]=="可见积水" and data["structure_version"]=="uav-verification-v1"


def test_ai_context_reads_uav_manual_records(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); overlay=uav_service.analyze_risk_overlay(db,risk,uav)
        db.add(UavVerificationRecord(id="v",project_id="p1",uav_dataset_id=uav.id,risk_assessment_id=risk.id,verification_status="REVIEWED",observation_type="VISIBLE_WATER",finding_text="人工记录")); db.commit()
        context=build_risk_assessment_context(db,risk.id)["uav_manual_verification"]
        assert context["available"] and context["risk_overlays"][0]["id"]==overlay.id and context["manual_verifications"][0]["finding_text"]=="人工记录"


def test_ai_prohibits_fake_uav_model_and_road_decision():
    combined=AI_SYSTEM_PROMPT+RISK_ASSESSMENT_GUIDE
    for phrase in ("无人机自动积水识别模型","修改人工复核记录","可安全通行","不得声称无人机正在监控","根据无人机人工复核记录"):
        assert phrase in combined


def test_risk_report_contains_uav_section_and_boundary(tmp_path):
    row=RiskAssessment(id="r",assessment_name="研判",source_type="change_analysis",source_t1_name="T1",source_t2_name="T2",status="completed",attention_level="中水害关注",rule_version="water-risk-v1",operation_note="辅助",risk_reason_codes="[]",risk_reason_text="[]",inspection_advice="[]")
    html=risk_report_service.risk_report_html(row,uav={"overlays":[{"uav_name":"航飞A","high_attention_coverage_ratio":.72,"inspection_coverage_ratio":.5,"analysis_version":"uav-spatial-v1"}],"verifications":[{"verification_status_label":"已复核","observation_type_label":"可见积水","finding_text":"人工记录"}]})
    assert "无人机精细复核" in html and "不使用无人机影像自动生成正式水害等级" in html and "72.00%" in html


def test_uav_serialization_does_not_invent_metadata(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); row=_uav(db,tmp_path,capture=False); data=uav_service.serialize_uav(row)
        assert data["capture_time"] is None and data["flight_name"]=="" and "camera_model" not in data


def test_archived_dataset_remains_referentially_valid(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); area=_area(db)
        db.add(UavWorkAreaCoverage(id="c",uav_dataset_id=uav.id,project_id="p1",work_area_id=area.id,coverage_status="FULL")); db.commit(); uav.status="archived"; db.commit()
        assert db.get(UavWorkAreaCoverage,"c") is not None and db.get(UavDataset,uav.id).status=="archived"


def _upload_file(path): return UploadFile(filename=Path(path).name,file=io.BytesIO(Path(path).read_bytes()))


def test_upload_api_and_list_real_metadata(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_api,"get_config",lambda:_cfg(tmp_path)); monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path)); path=_write_uav(tmp_path/"upload.tif")
    with Session(_engine(tmp_path)) as db:
        _project(db); result=uav_api.upload_uav(_upload_file(path),"p1",None,"航飞A","B1","现场","记录","设备未填写","2cm",db)
        listed=uav_api.list_uav("p1",False,db)["data"]
        assert result["data"]["flight_name"]=="航飞A" and listed[0]["width"]==4 and listed[0]["preview_url"]


def test_upload_api_rejects_duplicate_hash(tmp_path,monkeypatch):
    monkeypatch.setattr(uav_api,"get_config",lambda:_cfg(tmp_path)); monkeypatch.setattr(uav_service,"get_config",lambda:_cfg(tmp_path)); path=_write_uav(tmp_path/"upload.tif")
    with Session(_engine(tmp_path)) as db:
        _project(db); uav_api.upload_uav(_upload_file(path),"p1",None,"","","","","","",db)
        with pytest.raises(HTTPException) as error: uav_api.upload_uav(_upload_file(path),"p1",None,"","","","","","",db)
        assert error.value.status_code==409


def test_upload_api_rejects_plain_image_extension(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db)
        with pytest.raises(HTTPException,match="GeoTIFF"): uav_api.upload_uav(UploadFile(filename="photo.jpg",file=io.BytesIO(b"x")),"p1",None,"","","","","","",db)


def test_delete_api_physically_deletes_unreferenced_dataset(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); row=_uav(db,tmp_path); path=Path(row.file_path); result=uav_api.delete_uav(row.id,db)
        assert "已删除" in result["message"] and db.get(UavDataset,row.id) is None and not path.exists()


def test_delete_api_archives_referenced_dataset(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); row=_uav(db,tmp_path); area=_area(db); db.add(UavWorkAreaCoverage(id="c",uav_dataset_id=row.id,project_id="p1",work_area_id=area.id,coverage_status="FULL")); db.commit()
        result=uav_api.delete_uav(row.id,db); assert "归档" in result["message"] and db.get(UavDataset,row.id).status=="archived"


def test_verification_create_and_edit_api(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); area=_area(db)
        payload=UavVerificationCreate(project_id="p1",uav_dataset_id=uav.id,risk_assessment_id=risk.id,work_area_id=area.id,observation_type="POSSIBLE_WATER",finding_text="疑似水面")
        created=uav_api.create_verification(payload,db)["data"]
        updated=uav_api.update_verification(created["id"],__import__("backend.app.schemas.uav",fromlist=["UavVerificationUpdate"]).UavVerificationUpdate(verification_status="REVIEWED"),db)["data"]
        assert created["verification_status"]=="PENDING" and updated["verification_status"]=="REVIEWED" and updated["verified_at"]


def test_verification_cross_project_is_rejected(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); db.add(ConstructionProject(id="p2",project_name="二",project_code="P2",project_type="road")); db.commit()
        payload=UavVerificationCreate(project_id="p2",uav_dataset_id=uav.id,risk_assessment_id=risk.id)
        with pytest.raises(HTTPException,match="同一施工项目"): uav_api.create_verification(payload,db)


def test_project_uav_summary_uses_database_counts(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path); area=_area(db)
        db.add(UavWorkAreaCoverage(id="c",uav_dataset_id=uav.id,project_id="p1",work_area_id=area.id,coverage_status="FULL",coverage_ratio=1)); db.add(UavVerificationRecord(id="v",project_id="p1",uav_dataset_id=uav.id,risk_assessment_id=risk.id,work_area_id=area.id,verification_status="REVIEWED",observation_type="VISIBLE_WATER")); db.commit()
        data=uav_api.project_uav_summary("p1",db)["data"]
        assert data["dataset_count"]==1 and data["covered_work_area_count"]==1 and data["reviewed_count"]==1


def test_multisource_timeline_only_uses_recorded_times(tmp_path):
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path,capture=False); risk=_risk(db,tmp_path); db.add(UavVerificationRecord(id="v",project_id="p1",uav_dataset_id=uav.id,risk_assessment_id=risk.id,verification_status="PENDING",observation_type="OTHER")); db.commit()
        items=uav_api.multisource_timeline("p1",db)["data"]
        uav_item=next(x for x in items if x["type"]=="UAV_CAPTURE"); verification=next(x for x in items if x["type"]=="UAV_VERIFICATION")
        assert uav_item["time"] is None and uav_item["time_recorded"] is False and verification["time"] is None


async def _response_bytes(response):
    chunks=[]
    async for chunk in response.body_iterator: chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize("exporter,header",[(uav_api.uav_csv,"capture_time"),(uav_api.coverage_csv,"coverage_ratio"),(uav_api.overlay_csv,"analysis_version"),(uav_api.verification_csv,"structure_version")])
def test_uav_csv_exports_have_traceability_fields(tmp_path,exporter,header):
    with Session(_engine(tmp_path)) as db:
        _project(db); response=exporter(None,db); content=asyncio.run(_response_bytes(response)).decode("utf-8-sig")
        assert header in content


def test_uav_schema_creation_is_idempotent(tmp_path):
    engine=_engine(tmp_path); Base.metadata.create_all(engine); Base.metadata.create_all(engine)
    assert "uav_datasets" in inspect(engine).get_table_names()


def test_risk_with_uav_history_cannot_be_deleted(tmp_path,monkeypatch):
    monkeypatch.setattr("backend.app.services.risk_service.get_config",lambda:_cfg(tmp_path))
    with Session(_engine(tmp_path)) as db:
        _project(db); uav=_uav(db,tmp_path); risk=_risk(db,tmp_path)
        db.add(UavRiskOverlay(id="o",uav_dataset_id=uav.id,risk_assessment_id=risk.id,project_id="p1")); db.commit()
        with pytest.raises(RiskAssessmentError,match="历史可追溯"): delete_risk_assessment(db,risk)
