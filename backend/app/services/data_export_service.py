from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import json
import random
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.engine import Engine


EXPORT_GROUPS = (
    ("projects", "项目与区域", (("construction_projects", "施工项目"), ("work_areas", "作业区域"))),
    ("remote_sensing", "遥感识别", (("datasets", "遥感数据"), ("inference_tasks", "识别任务"), ("inference_results", "识别结果"))),
    ("change", "时空变化", (("change_analyses", "变化分析"), ("change_rainfall_contexts", "变化降雨关联"))),
    ("risk", "风险研判", (("risk_assessments", "风险研判"), ("risk_rainfall_contexts", "风险降雨关联"), ("work_area_impacts", "区域影响"))),
    ("equipment", "工程机械", (("construction_equipment", "设备台账"), ("equipment_positions", "设备位置"), ("equipment_risk_links", "设备风险关联"))),
    ("terrain", "地形数据", (("terrain_datasets", "DEM 数据"), ("work_area_terrain_profiles", "区域地形分析"), ("equipment_terrain_snapshots", "设备地形快照"), ("terrain_fusion_analyses", "地形融合分析"))),
    ("rainfall", "降雨数据", (("rainfall_stations", "雨量站"), ("rainfall_observations", "降雨观测"), ("rainfall_events", "降雨事件"))),
    ("uav", "无人机数据", (("uav_datasets", "无人机影像"), ("uav_work_area_coverages", "区域覆盖"), ("uav_risk_overlays", "风险叠加"), ("uav_verification_records", "协同复核"))),
    ("assistance", "安全作业", (("operation_assistance_records", "作业辅助记录"), ("assistance_verifications", "作业复核记录"))),
)

TABLE_LABELS = {
    table_name: table_label
    for _group_key, _group_label, tables in EXPORT_GROUPS
    for table_name, table_label in tables
}
TABLE_GROUPS = {
    table_name: group_label
    for _group_key, group_label, tables in EXPORT_GROUPS
    for table_name, _table_label in tables
}
ALLOWED_TABLES = frozenset(TABLE_LABELS)

EXACT_FIELD_LABELS = {
    "id": "记录ID", "name": "名称", "description": "说明", "status": "状态",
    "created_at": "创建时间", "updated_at": "更新时间", "started_at": "开始时间",
    "finished_at": "完成时间", "completed_at": "完成时间", "project_id": "项目ID",
    "dataset_id": "数据ID", "task_id": "任务ID", "risk_assessment_id": "风险研判ID",
    "work_area_id": "作业区域ID", "equipment_id": "设备ID", "position_id": "位置记录ID",
    "dem_id": "DEM数据ID", "uav_dataset_id": "无人机数据ID", "station_id": "雨量站ID",
    "path": "文件路径", "file_hash": "文件哈希", "source_type": "来源类型",
    "source_name": "来源名称", "source_note": "来源说明", "error_message": "错误信息",
    "project_name": "项目名称", "project_code": "项目编号", "project_type": "项目类型",
    "area_name": "区域名称", "area_code": "区域编号", "area_type": "区域类型",
    "equipment_name": "设备名称", "equipment_code": "设备编号", "equipment_type": "设备类型",
    "recorded_at": "记录时间", "observed_at": "观测时间", "capture_time": "采集时间",
    "crs": "坐标参考系", "x": "X坐标", "y": "Y坐标", "width": "宽度",
    "height": "高度", "dtype": "数据类型", "nodata": "无效值", "bounds": "空间范围",
    "transform": "仿射变换", "threshold": "识别阈值", "model_name": "模型名称",
    "flood_ratio": "积水占比", "risk_level": "风险等级", "attention_level": "关注等级",
    "rainfall_mm": "降雨量(mm)", "rainfall_rate_mm_h": "降雨强度(mm/h)",
    "rule_version": "规则版本", "algorithm_version": "算法版本",
}

TOKEN_LABELS = {
    "min": "最小", "max": "最大", "mean": "平均", "median": "中位", "total": "总计",
    "count": "数量", "ratio": "占比", "time": "时间", "date": "日期", "hours": "小时",
    "pixels": "像素数", "pixel": "像素", "valid": "有效", "high": "高", "low": "低",
    "confidence": "置信度", "water": "积水", "rainfall": "降雨", "event": "事件",
    "source": "来源", "type": "类型", "note": "说明", "version": "版本", "code": "编号",
    "level": "等级", "geometry": "几何", "result": "结果", "analysis": "分析",
}


def field_label(name: str) -> str:
    if name in EXACT_FIELD_LABELS:
        return EXACT_FIELD_LABELS[name]
    words = [TOKEN_LABELS.get(token, token) for token in name.split("_")]
    translated = "".join(words)
    return translated if translated != name.replace("_", "") else name


def _type_name(column: dict[str, Any]) -> str:
    return str(column["type"])


def build_export_catalog(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    available = set(inspector.get_table_names())
    groups: list[dict[str, Any]] = []
    total_fields = 0
    total_records = 0
    with engine.connect() as connection:
        for group_key, group_label, definitions in EXPORT_GROUPS:
            tables: list[dict[str, Any]] = []
            for table_name, table_label in definitions:
                if table_name not in available:
                    continue
                columns = inspector.get_columns(table_name)
                row_count = int(connection.execute(select(func.count()).select_from(Table(table_name, MetaData(), autoload_with=engine))).scalar_one())
                fields = [
                    {
                        "name": column["name"],
                        "label": field_label(column["name"]),
                        "type": _type_name(column),
                        "nullable": bool(column.get("nullable", True)),
                    }
                    for column in columns
                ]
                tables.append({
                    "name": table_name,
                    "label": table_label,
                    "row_count": row_count,
                    "field_count": len(fields),
                    "fields": fields,
                })
                total_fields += len(fields)
                total_records += row_count
            groups.append({"key": group_key, "label": group_label, "tables": tables})
    return {
        "groups": groups,
        "table_count": sum(len(group["tables"]) for group in groups),
        "field_count": total_fields,
        "record_count": total_records,
    }


def _safe_cell_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if value is None or isinstance(value, (str, int, float, bool, date)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _style_header(ws, row: int, color: str = "173F5F") -> None:
    fill = PatternFill("solid", fgColor=color)
    side = Side(style="thin", color="B8C5D1")
    for cell in ws[row]:
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = Border(bottom=side)
    ws.row_dimensions[row].height = 24


def _finish_sheet(ws, *, freeze: str | None = None, filter_ref: str | None = None) -> None:
    ws.sheet_view.showGridLines = False
    if freeze:
        ws.freeze_panes = freeze
    if filter_ref:
        ws.auto_filter.ref = filter_ref
    for index, column_cells in enumerate(ws.columns, start=1):
        width = 10
        for cell in list(column_cells)[:300]:
            value = "" if cell.value is None else str(cell.value)
            width = max(width, min(40, len(value) + 2))
            cell.alignment = Alignment(vertical="top", wrap_text=len(value) > 36)
        ws.column_dimensions[get_column_letter(index)].width = width


def _unique_sheet_name(base: str, used: set[str]) -> str:
    name = base[:31]
    suffix = 2
    while name in used:
        tail = f"_{suffix}"
        name = f"{base[:31-len(tail)]}{tail}"
        suffix += 1
    used.add(name)
    return name


def build_xlsx_export(
    engine: Engine,
    table_names: list[str],
    *,
    include_data: bool = True,
    include_field_dictionary: bool = True,
) -> BytesIO:
    selected = list(dict.fromkeys(table_names))
    invalid = [name for name in selected if name not in ALLOWED_TABLES]
    if invalid:
        raise ValueError(f"不允许导出的数据表：{', '.join(invalid)}")
    if not selected:
        raise ValueError("请至少选择一张数据表")
    if not include_data and not include_field_dictionary:
        raise ValueError("字段字典和数据记录不能同时关闭")

    inspector = inspect(engine)
    available = set(inspector.get_table_names())
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(f"数据库中缺少数据表：{', '.join(missing)}")

    wb = Workbook()
    wb.remove(wb.active)
    used_names: set[str] = set()
    generated_at = datetime.now().astimezone()

    intro = wb.create_sheet(_unique_sheet_name("导出说明", used_names))
    intro.append(["工程水害管理系统 · 数据导出"])
    intro.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    intro["A1"].font = Font(size=16, bold=True, color="173F5F")
    intro["A1"].alignment = Alignment(vertical="center")
    intro.row_dimensions[1].height = 32
    intro.append(["导出时间", generated_at.replace(tzinfo=None), "数据表数量", len(selected)])
    intro.append(["导出内容", "字段字典与数据" if include_data and include_field_dictionary else ("仅数据" if include_data else "仅字段字典"), "数据来源", "当前系统数据库"])
    intro.append([])
    intro.append(["业务分区", "数据表", "数据库表名", "记录数"])
    _style_header(intro, 5)

    metadata = MetaData()
    reflected: dict[str, Table] = {}
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table_name in selected:
            table = Table(table_name, metadata, autoload_with=engine)
            reflected[table_name] = table
            count = int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            if count > 1_048_573:
                raise ValueError(f"{TABLE_LABELS[table_name]}超过 Excel 单表行数限制")
            counts[table_name] = count
            intro.append([TABLE_GROUPS[table_name], TABLE_LABELS[table_name], table_name, count])
    _finish_sheet(intro, freeze="A6", filter_ref=f"A5:D{5 + len(selected)}")
    intro.column_dimensions["A"].width = 18
    intro.column_dimensions["B"].width = 20
    intro.column_dimensions["C"].width = 32
    intro.column_dimensions["D"].width = 14
    intro["B2"].number_format = "yyyy-mm-dd hh:mm:ss"

    if include_field_dictionary:
        dictionary = wb.create_sheet(_unique_sheet_name("字段字典", used_names))
        dictionary.append(["业务分区", "数据表", "数据库表名", "字段名称", "字段说明", "字段类型", "允许为空"])
        _style_header(dictionary, 1)
        for table_name in selected:
            for column in inspector.get_columns(table_name):
                dictionary.append([
                    TABLE_GROUPS[table_name], TABLE_LABELS[table_name], table_name,
                    column["name"], field_label(column["name"]), _type_name(column),
                    "是" if column.get("nullable", True) else "否",
                ])
        _finish_sheet(dictionary, freeze="A2", filter_ref=dictionary.dimensions)

    if include_data:
        with engine.connect() as connection:
            for table_name in selected:
                table = reflected[table_name]
                ws = wb.create_sheet(_unique_sheet_name(TABLE_LABELS[table_name], used_names))
                columns = list(table.columns)
                ws.append([field_label(column.name) for column in columns])
                ws.append([column.name for column in columns])
                _style_header(ws, 1)
                _style_header(ws, 2, "52758F")
                for row in connection.execute(select(table)).mappings():
                    ws.append([_safe_cell_value(row[column.name]) for column in columns])
                last_row = max(2, 2 + counts[table_name])
                _finish_sheet(ws, freeze="A3", filter_ref=f"A2:{get_column_letter(len(columns))}{last_row}")

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


SIMULATED_PARENT_TABLES = {
    "project_id": "construction_projects",
    "work_area_id": "work_areas",
    "equipment_id": "construction_equipment",
    "position_id": "equipment_positions",
    "dataset_id": "datasets",
    "task_id": "inference_tasks",
    "result_id": "inference_results",
    "risk_assessment_id": "risk_assessments",
    "dem_id": "terrain_datasets",
    "station_id": "rainfall_stations",
    "uav_dataset_id": "uav_datasets",
    "assistance_record_id": "operation_assistance_records",
}


def _simulated_id(table_name: str, index: int) -> str:
    prefix = "".join(part[0] for part in table_name.split("_"))[:8]
    return f"SIM-{prefix.upper()}-{index + 1:05d}"


def _simulated_text(table_name: str, name: str, index: int, rng: random.Random) -> str:
    row_no = index + 1
    if name == "id":
        return _simulated_id(table_name, index)
    if name in SIMULATED_PARENT_TABLES:
        return _simulated_id(SIMULATED_PARENT_TABLES[name], index)
    if name.endswith("_id"):
        return f"SIM-REF-{name[:-3].upper()}-{row_no:05d}"
    if name.endswith("_ids") or name in {"reason_codes", "inspection_advice", "inspection_checklist", "checklist"}:
        return json.dumps([f"SIM-{row_no:03d}"], ensure_ascii=False)
    if name in {"geometry_geojson", "footprint_geojson"}:
        x, y = 116.3 + index * .002, 39.8 + index * .002
        return json.dumps({"type": "Polygon", "coordinates": [[[x, y], [x+.01, y], [x+.01, y+.01], [x, y+.01], [x, y]]]}, ensure_ascii=False)
    if "json" in name or name in {"evidence", "transform", "bounds", "vv_stats", "vh_stats", "zone_counts", "class_counts"}:
        return json.dumps({"simulated": True, "sequence": row_no}, ensure_ascii=False)
    if "path" in name:
        return f"simulated://{table_name}/{row_no:05d}/{name}"
    if "hash" in name:
        return f"simulated-{row_no:056d}"[:64]
    if "code" in name:
        return f"SIM-{row_no:04d}"
    if name in {"status", "analysis_status", "event_status", "business_status", "lifecycle_status"}:
        return "simulated"
    if "risk_level" in name or name == "attention_level":
        return rng.choice(["低关注", "中关注", "高关注"])
    if name.endswith("_type"):
        return "simulated"
    if name.endswith("_name") or name == "name":
        return f"模拟{TABLE_LABELS.get(table_name, table_name)}{row_no:03d}"
    if name in {"crs", "source_crs"}:
        return "EPSG:4326"
    if name in {"dtype"}:
        return "float32"
    if "version" in name:
        return "simulation-v1"
    if "source" in name:
        return "系统模拟数据"
    if "note" in name or "description" in name or "text" in name or "message" in name:
        return f"模拟记录 {row_no}，仅用于功能验证"
    return f"模拟{name}-{row_no:03d}"


def _simulated_value(table_name: str, column, index: int, rng: random.Random) -> Any:
    name = column.name
    type_name = str(column.type).upper()
    if "BOOL" in type_name:
        return bool(index % 2)
    if "DATE" in type_name or "TIME" in type_name:
        return datetime(2026, 1, 1, 8, 0, 0).replace(day=1 + index % 28)
    if "INT" in type_name:
        if "width" in name or "height" in name:
            return 1024
        if "interval" in name:
            return 60
        return row_count_value(name, index, rng)
    if any(token in type_name for token in ("FLOAT", "REAL", "NUMERIC", "DOUBLE", "DECIMAL")):
        if name == "x" or "longitude" in name:
            return round(116.3 + index * .002, 6)
        if name == "y" or "latitude" in name:
            return round(39.8 + index * .002, 6)
        if "ratio" in name or "confidence" in name:
            return round(rng.uniform(.02, .95), 4)
        if "rainfall" in name:
            return round(rng.uniform(0, 65), 2)
        if "elevation" in name:
            return round(rng.uniform(20, 180), 2)
        if "slope" in name:
            return round(rng.uniform(0, 25), 2)
        if "seconds" in name or "hours" in name:
            return round(rng.uniform(.5, 24), 2)
        return round(rng.uniform(1, 100), 3)
    return _simulated_text(table_name, name, index, rng)


def row_count_value(name: str, index: int, rng: random.Random) -> int:
    if "count" in name or "pixels" in name:
        return rng.randint(10, 10000)
    if "band" in name:
        return 2
    return index + 1


def build_simulated_xlsx(engine: Engine, table_names: list[str], rows_per_table: int, seed: int) -> BytesIO:
    selected = list(dict.fromkeys(table_names))
    invalid = [name for name in selected if name not in ALLOWED_TABLES]
    if invalid:
        raise ValueError(f"不允许模拟的数据表：{', '.join(invalid)}")
    if not selected:
        raise ValueError("请至少选择一张数据表")
    if not 1 <= rows_per_table <= 1000:
        raise ValueError("每张表的模拟行数必须在 1 到 1000 之间")

    inspector = inspect(engine)
    available = set(inspector.get_table_names())
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(f"数据库中缺少数据表：{', '.join(missing)}")

    rng = random.Random(seed)
    wb = Workbook()
    wb.remove(wb.active)
    used_names: set[str] = set()
    intro = wb.create_sheet(_unique_sheet_name("模拟说明", used_names))
    intro.append(["工程水害管理系统 · 模拟数据"])
    intro.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    intro["A1"].font = Font(size=16, bold=True, color="9C2F2F")
    intro["A1"].alignment = Alignment(vertical="center")
    intro.row_dimensions[1].height = 32
    intro.append(["数据性质", "模拟数据，不得作为现场观测或工程判断依据", "生成时间", datetime.now()])
    intro.append(["随机种子", seed, "每表行数", rows_per_table])
    intro.append(["选择表数", len(selected), "模拟记录数", len(selected) * rows_per_table])
    intro.append([])
    intro.append(["业务分区", "数据表", "数据库表名", "字段数"])
    _style_header(intro, 6, "9C2F2F")
    for table_name in selected:
        intro.append([TABLE_GROUPS[table_name], TABLE_LABELS[table_name], table_name, len(inspector.get_columns(table_name))])
    _finish_sheet(intro, freeze="A7", filter_ref=f"A6:D{6 + len(selected)}")

    dictionary = wb.create_sheet(_unique_sheet_name("字段字典", used_names))
    dictionary.append(["业务分区", "数据表", "字段名称", "字段说明", "字段类型", "模拟规则"])
    _style_header(dictionary, 1, "9C2F2F")
    for table_name in selected:
        for column in inspector.get_columns(table_name):
            dictionary.append([TABLE_GROUPS[table_name], TABLE_LABELS[table_name], column["name"], field_label(column["name"]), _type_name(column), "确定性随机模拟"])
    _finish_sheet(dictionary, freeze="A2", filter_ref=dictionary.dimensions)

    metadata = MetaData()
    for table_name in selected:
        table = Table(table_name, metadata, autoload_with=engine)
        columns = list(table.columns)
        ws = wb.create_sheet(_unique_sheet_name(TABLE_LABELS[table_name], used_names))
        ws.append([f"【模拟】{field_label(column.name)}" for column in columns])
        ws.append([column.name for column in columns])
        _style_header(ws, 1, "9C2F2F")
        _style_header(ws, 2, "7A4A4A")
        for index in range(rows_per_table):
            ws.append([_simulated_value(table_name, column, index, rng) for column in columns])
        _finish_sheet(ws, freeze="A3", filter_ref=f"A2:{get_column_letter(len(columns))}{rows_per_table + 2}")

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
