from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import CONFIG_PATH, PROJECT_ROOT, get_config, get_llm_config
from ..db.models import (
    AssistanceVerification, ChangeAnalysis, ConstructionEquipment, ConstructionProject, DatasetRecord,
    InferenceTask, OperationAssistanceRecord, RainfallObservation,
    RiskAssessment, TerrainDataset, UavDataset, WorkArea,
)


def runtime_root() -> Path:
    return Path(os.environ.get("FLOODRISK_DATA_DIR", PROJECT_ROOT)).resolve()


class _SecretFilter(logging.Filter):
    _patterns = (
        re.compile(r"(?i)(api[_ -]?key)(\s*[:=]\s*)[^\s,;]+"),
        re.compile(r"(?i)(authorization)(\s*:\s*)(?:bearer\s+)?[^\s,;]+"),
        re.compile(r"(?i)(bearer)(\s+)[^\s,;]+"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self._patterns:
            message = pattern.sub(
                lambda match: f"{match.group(1)}{match.group(2)}***" if match.lastindex else "***",
                message,
            )
        record.msg, record.args = message, ()
        return True


def configure_logging() -> Path:
    log_dir = runtime_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    secret_filter = _SecretFilter()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_zhiyu_handler", False) for h in root.handlers):
        for filename, level in (("app.log", logging.INFO), ("error.log", logging.ERROR)):
            handler = RotatingFileHandler(
                log_dir / filename, maxBytes=10 * 1024 * 1024, backupCount=5,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(formatter)
            handler.addFilter(secret_filter)
            handler._zhiyu_handler = True  # type: ignore[attr-defined]
            root.addHandler(handler)
    return log_dir


def _path_check(path: Path, *, file_path: bool = False) -> dict[str, Any]:
    target = path.parent if file_path else path
    result: dict[str, Any] = {"path": str(path), "exists": path.exists(), "writable": False}
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".zhiyu-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        result["writable"] = True
    except OSError as exc:
        result["message"] = f"目录不可写：{type(exc).__name__}"
    return result


def diagnostics(db: Session | None = None, *, database_status: str = "ok", database_error: str = "") -> dict[str, Any]:
    cfg = get_config()
    llm = get_llm_config()
    frontend = Path(os.environ.get("FLOODRISK_FRONTEND_DIR", PROJECT_ROOT / "frontend" / "dist"))
    paths = {
        "uploads": _path_check(cfg.paths.uploads),
        "outputs": _path_check(cfg.paths.outputs),
        "database": _path_check(cfg.paths.database, file_path=True),
        "logs": _path_check(runtime_root() / "logs"),
    }
    database_message = database_error
    if db is not None and database_status == "ok":
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:  # diagnostic boundary
            database_status, database_message = "database_error", type(exc).__name__
    disk = shutil.disk_usage(runtime_root())
    checks = {
        "python": {"status": "ok", "version": platform.python_version(), "executable": sys.executable},
        "frontend": {"status": "ok" if (frontend / "index.html").is_file() else "missing", "path": str(frontend)},
        "database": {"status": database_status, "path": str(cfg.paths.database), "message": database_message},
        "model": {
            "status": "ok" if cfg.model.checkpoint.is_file() else "missing",
            "checkpoint": str(cfg.model.checkpoint), "stats": str(cfg.model.sar_stats),
        },
        "paths": paths,
        "logs": {"status": "ok" if (runtime_root() / "logs").is_dir() else "missing", "path": str(runtime_root() / "logs")},
        "ai": {"status": "configured" if llm.configured else "offline", "enabled": llm.enabled, "model": llm.model},
        "disk": {"status": "ok" if disk.free >= 512 * 1024 * 1024 else "low", "free_bytes": disk.free, "total_bytes": disk.total},
        "ports": {"backend": int(os.environ.get("FLOODRISK_BACKEND_PORT", "8001")), "frontend": int(os.environ.get("FLOODRISK_FRONTEND_PORT", "5173"))},
    }
    overall = "ok" if checks["database"]["status"] == "ok" and all(v["writable"] for v in paths.values()) else "degraded"
    return {"generated_at": datetime.now().astimezone().isoformat(), "system": "工程水害管理系统", "version": "正式版", "status": overall, "checks": checks}


def project_workflow(db: Session, project_id: str) -> dict[str, Any]:
    project = db.get(ConstructionProject, project_id)
    if not project:
        raise LookupError("施工项目不存在")
    areas = db.scalar(select(func.count()).select_from(WorkArea).where(WorkArea.project_id == project_id, WorkArea.status == "active")) or 0
    equipment = db.scalar(select(func.count()).select_from(ConstructionEquipment).where(ConstructionEquipment.project_id == project_id)) or 0
    dem = db.scalar(select(func.count()).select_from(TerrainDataset).where(TerrainDataset.project_id == project_id)) or 0
    rain = db.scalar(select(func.count()).select_from(RainfallObservation).where(RainfallObservation.project_id == project_id)) or 0
    uav = db.scalar(select(func.count()).select_from(UavDataset).where(UavDataset.project_id == project_id)) or 0
    specs = [
        ("environment", "环境数据", db.scalar(select(func.count()).select_from(DatasetRecord).where(DatasetRecord.project_id == project_id)) or 0, "/datasets"),
        ("inference", "积水识别", db.scalar(select(func.count()).select_from(InferenceTask).where(InferenceTask.project_id == project_id, InferenceTask.status == "completed")) or 0, "/inference"),
        ("change", "时空变化", db.scalar(select(func.count()).select_from(ChangeAnalysis).where(ChangeAnalysis.project_id == project_id, ChangeAnalysis.status == "completed")) or 0, "/compare"),
        ("risk", "风险研判", db.scalar(select(func.count()).select_from(RiskAssessment).where(RiskAssessment.project_id == project_id, RiskAssessment.status == "completed")) or 0, "/risk"),
        ("objects", "区域与设备关联", min(areas, equipment), "/areas"),
        ("multisource", "多源辅助分析", dem + rain + uav, "/datasets"),
        ("assistance", "作业辅助", db.scalar(select(func.count()).select_from(OperationAssistanceRecord).where(OperationAssistanceRecord.project_id == project_id)) or 0, "/assistance"),
        ("verification", "人工核查", db.scalar(select(func.count()).select_from(AssistanceVerification).join(OperationAssistanceRecord, AssistanceVerification.assistance_record_id == OperationAssistanceRecord.id).where(OperationAssistanceRecord.project_id == project_id)) or 0, "/assistance"),
    ]
    steps = [{"key": key, "label": label, "count": count, "status": "completed" if count else "pending", "route": route} for key, label, count, route in specs]
    first_pending = next((item for item in steps if item["status"] == "pending"), None)
    return {"project": {"id": project.id, "name": project.project_name, "code": project.project_code}, "steps": steps, "next_step": first_pending, "completion_percent": round(100 * sum(x["status"] == "completed" for x in steps) / len(steps))}


def project_completeness(db: Session, project_id: str) -> dict[str, Any]:
    if not db.get(ConstructionProject, project_id):
        raise LookupError("施工项目不存在")
    counts = {
        "work_areas": db.scalar(select(func.count()).select_from(WorkArea).where(WorkArea.project_id == project_id, WorkArea.status == "active")) or 0,
        "equipment": db.scalar(select(func.count()).select_from(ConstructionEquipment).where(ConstructionEquipment.project_id == project_id)) or 0,
        "sar": db.scalar(select(func.count()).select_from(DatasetRecord).where(DatasetRecord.project_id == project_id)) or 0,
        "inference": db.scalar(select(func.count()).select_from(InferenceTask).where(InferenceTask.project_id == project_id, InferenceTask.status == "completed")) or 0,
        "change": db.scalar(select(func.count()).select_from(ChangeAnalysis).where(ChangeAnalysis.project_id == project_id, ChangeAnalysis.status == "completed")) or 0,
        "risk": db.scalar(select(func.count()).select_from(RiskAssessment).where(RiskAssessment.project_id == project_id, RiskAssessment.status == "completed")) or 0,
        "dem": db.scalar(select(func.count()).select_from(TerrainDataset).where(TerrainDataset.project_id == project_id)) or 0,
        "rainfall": db.scalar(select(func.count()).select_from(RainfallObservation).where(RainfallObservation.project_id == project_id)) or 0,
        "uav": db.scalar(select(func.count()).select_from(UavDataset).where(UavDataset.project_id == project_id)) or 0,
        "assistance": db.scalar(select(func.count()).select_from(OperationAssistanceRecord).where(OperationAssistanceRecord.project_id == project_id)) or 0,
    }
    labels = {"work_areas": "施工区域", "equipment": "工程机械", "sar": "SAR影像", "inference": "积水识别", "change": "时空变化", "risk": "风险研判", "dem": "DEM地形", "rainfall": "降雨观测", "uav": "无人机影像", "assistance": "作业辅助事项"}
    optional = {"dem", "rainfall", "uav"}
    items = [{"key": key, "label": labels[key], "count": count, "status": "ready" if count else "optional_missing" if key in optional else "missing", "required": key not in optional} for key, count in counts.items()]
    required_ready = all(x["status"] == "ready" for x in items if x["required"])
    return {"project_id": project_id, "status": "ready" if required_ready else "incomplete", "items": items, "ready_count": sum(x["status"] == "ready" for x in items), "total_count": len(items)}


def create_backup(*, include_manifest: bool = False) -> dict[str, Any]:
    cfg = get_config()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = runtime_root() / "backups" / stamp
    target.mkdir(parents=True, exist_ok=False)
    copied: list[dict[str, Any]] = []
    if cfg.paths.database.is_file():
        destination = target / "zhiyu_data.db"
        with sqlite3.connect(cfg.paths.database) as source, sqlite3.connect(destination) as dest:
            source.backup(dest)
        copied.append({"type": "database", "name": destination.name, "size": destination.stat().st_size})
    for source, name in ((CONFIG_PATH, "app.yaml"), (CONFIG_PATH.with_name("llm.local.json"), "llm.local.json")):
        if source.is_file():
            shutil.copy2(source, target / name)
            copied.append({"type": "config", "name": name, "size": (target / name).stat().st_size})
    manifest = {"created_at": datetime.now().astimezone().isoformat(), "backup_type": "complete_manifest" if include_manifest else "quick", "files": copied, "external_data": []}
    if include_manifest:
        for label, root in (("uploads", cfg.paths.uploads), ("outputs", cfg.paths.outputs)):
            files = [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []
            manifest["external_data"].append({"type": label, "path": str(root), "file_count": len(files), "total_bytes": sum(p.stat().st_size for p in files)})
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(target), **manifest}


def artifact_integrity(db: Session) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    models = (
        (ChangeAnalysis, ("change_png_path", "change_geotiff_path")),
        (RiskAssessment, ("risk_png_path", "risk_geotiff_path", "summary_json_path", "report_path")),
        (TerrainDataset, ("elevation_preview_path", "slope_path", "low_mask_path", "summary_json_path")),
        (UavDataset, ("file_path", "preview_path")),
    )
    for model, fields in models:
        for row in db.scalars(select(model)).all():
            for field in fields:
                value = getattr(row, field, None)
                if value:
                    path = Path(value)
                    entries.append({"entity": model.__tablename__, "id": row.id, "field": field, "path": str(path), "status": "ok" if path.is_file() else "missing"})
    return {"checked": len(entries), "ok": sum(x["status"] == "ok" for x in entries), "missing": sum(x["status"] == "missing" for x in entries), "items": entries}
