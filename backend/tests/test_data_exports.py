from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.db.models import ConstructionProject
from backend.app.services.data_export_service import (
    ALLOWED_TABLES,
    build_export_catalog,
    build_simulated_xlsx,
    build_xlsx_export,
)


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'export.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _add_project(engine):
    with Session(engine) as db:
        db.add(ConstructionProject(
            id="project-1",
            project_name="东区施工项目",
            project_code="P-001",
            project_type="earthwork",
            description="测试项目",
            location_text="东区",
            created_at=datetime(2026, 10, 14, 8, tzinfo=timezone.utc),
            updated_at=datetime(2026, 10, 14, 8, tzinfo=timezone.utc),
        ))
        db.commit()


def test_catalog_groups_all_business_tables(tmp_path):
    engine = _engine(tmp_path)
    catalog = build_export_catalog(engine)

    names = {
        table["name"]
        for group in catalog["groups"]
        for table in group["tables"]
    }
    assert len(catalog["groups"]) == 9
    assert catalog["table_count"] == 26
    assert names == ALLOWED_TABLES
    assert catalog["field_count"] > 400


def test_xlsx_contains_dictionary_and_typed_data(tmp_path):
    engine = _engine(tmp_path)
    _add_project(engine)

    content = build_xlsx_export(
        engine,
        ["construction_projects"],
        include_data=True,
        include_field_dictionary=True,
    )
    assert content.read(2) == b"PK"
    content.seek(0)
    workbook = load_workbook(content, read_only=True)

    assert workbook.sheetnames == ["导出说明", "字段字典", "施工项目"]
    sheet = workbook["施工项目"]
    assert sheet["A1"].value == "记录ID"
    assert sheet["A2"].value == "id"
    assert sheet["B3"].value == "东区施工项目"
    assert isinstance(sheet["H3"].value, datetime)


def test_dictionary_only_and_invalid_table_validation(tmp_path):
    engine = _engine(tmp_path)
    content = build_xlsx_export(
        engine,
        ["rainfall_stations"],
        include_data=False,
        include_field_dictionary=True,
    )
    workbook = load_workbook(content, read_only=True)
    assert workbook.sheetnames == ["导出说明", "字段字典"]

    with pytest.raises(ValueError, match="不允许导出"):
        build_xlsx_export(engine, ["sqlite_master"])

    with pytest.raises(ValueError, match="不能同时关闭"):
        build_xlsx_export(
            engine,
            ["rainfall_stations"],
            include_data=False,
            include_field_dictionary=False,
        )


def test_simulated_workbook_supports_any_business_table(tmp_path):
    engine = _engine(tmp_path)
    content = build_simulated_xlsx(
        engine,
        ["construction_projects", "rainfall_observations", "uav_datasets"],
        rows_per_table=3,
        seed=42,
    )
    workbook = load_workbook(content, read_only=True)

    assert workbook.sheetnames == ["模拟说明", "字段字典", "施工项目", "降雨观测", "无人机影像"]
    assert workbook["施工项目"].max_row == 5
    assert workbook["施工项目"]["A3"].value.startswith("SIM-")
    assert workbook["降雨观测"]["A1"].value.startswith("【模拟】")


def test_simulated_workbook_is_deterministic_for_seed(tmp_path):
    engine = _engine(tmp_path)
    first = load_workbook(build_simulated_xlsx(engine, ["rainfall_observations"], 2, 7), read_only=True)
    second = load_workbook(build_simulated_xlsx(engine, ["rainfall_observations"], 2, 7), read_only=True)

    first_values = list(first["降雨观测"].values)[2:]
    second_values = list(second["降雨观测"].values)[2:]
    assert first_values == second_values
