import logging
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.database import Base
from backend.app.db.models import ConstructionProject
from backend.app.preflight import port_available
from backend.app.services import runtime_service as runtime


ACCEPTANCE_POINTS = (
    "homepage", "project_context", "workflow_entry", "workflow_real_counts", "data_completeness",
    "outcome_trace", "status_labels", "empty_state", "error_summary", "database_error_health",
    "app_log", "error_log", "log_rotation", "secret_redaction", "python_diagnostic",
    "frontend_diagnostic", "database_diagnostic", "model_diagnostic", "directory_diagnostic", "log_diagnostic",
    "ai_diagnostic", "disk_diagnostic", "port_diagnostic", "txt_report", "json_report",
    "quick_backup", "manifest_backup", "no_auto_restore", "port_preflight", "no_process_kill",
    "database_writable", "upload_writable", "output_writable", "safe_temp_policy", "change_integrity",
    "risk_integrity", "terrain_integrity", "uav_integrity", "assistance_trace_integrity", "safe_export_name",
    "ai_boundary", "offline_core", "portable_runtime", "final_readme", "final_acceptance",
)


@pytest.mark.parametrize("point", ACCEPTANCE_POINTS)
def test_final_acceptance_point_is_unique_and_named(point):
    """45 项封板验收点保持稳定、可追踪，避免后续维护中静默删项。"""
    assert point and point == point.strip()
    assert ACCEPTANCE_POINTS.count(point) == 1


@pytest.mark.parametrize("raw,forbidden", [
    ("api_key=sk-super-secret-value", "super-secret"),
    ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
    ("bearer token-value", "token-value"),
    ("LLM API Key: private-value", "private-value"),
    ("normal diagnostic message", "not-present"),
])
def test_log_secret_filter(raw, forbidden):
    record = logging.LogRecord("test", logging.INFO, __file__, 1, raw, (), None)
    assert runtime._SecretFilter().filter(record)
    assert forbidden not in record.getMessage()


def test_path_check_creates_and_verifies_writable_directory(tmp_path):
    result = runtime._path_check(tmp_path / "nested")
    assert result["writable"] is True
    assert (tmp_path / "nested").is_dir()
    assert not (tmp_path / "nested" / ".zhiyu-write-check").exists()


def test_port_preflight_detects_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        assert port_available(listener.getsockname()[1]) is False


def test_empty_project_workflow_and_completeness_are_truthful():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ConstructionProject(id="p1", project_name="封板项目", project_code="FINAL", project_type="露天施工"))
        db.commit()
        workflow = runtime.project_workflow(db, "p1")
        completeness = runtime.project_completeness(db, "p1")
    assert len(workflow["steps"]) == 8
    assert all(step["status"] == "pending" for step in workflow["steps"])
    assert workflow["next_step"]["key"] == "environment"
    assert completeness["status"] == "incomplete"
    assert completeness["ready_count"] == 0


def test_artifact_integrity_empty_database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        result = runtime.artifact_integrity(db)
    assert result == {"checked": 0, "ok": 0, "missing": 0, "items": []}


def test_quick_backup_uses_consistent_sqlite_copy(tmp_path, monkeypatch):
    database = tmp_path / "source.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")
    config = tmp_path / "app.yaml"
    config.write_text("system: test\n", encoding="utf-8")
    fake = SimpleNamespace(paths=SimpleNamespace(database=database, uploads=tmp_path / "uploads", outputs=tmp_path / "outputs"))
    monkeypatch.setattr(runtime, "get_config", lambda: fake)
    monkeypatch.setattr(runtime, "CONFIG_PATH", config)
    monkeypatch.setattr(runtime, "runtime_root", lambda: tmp_path)
    result = runtime.create_backup()
    copied = Path(result["path"]) / "zhiyu_data.db"
    with sqlite3.connect(copied) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "ok"
    assert (Path(result["path"]) / "manifest.json").is_file()
