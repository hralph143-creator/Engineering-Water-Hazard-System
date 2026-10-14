import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..schemas.common import ok
from ..services.runtime_service import artifact_integrity, create_backup, diagnostics, project_completeness, project_workflow

router = APIRouter(tags=["system-delivery"])
logger = logging.getLogger(__name__)


def _diagnostics(request: Request, db: Session):
    return diagnostics(db, database_status=getattr(request.app.state, "database_status", "ok"), database_error=getattr(request.app.state, "database_error", ""))


@router.get("/system/diagnostics")
def get_diagnostics(request: Request, db: Session = Depends(get_db)):
    payload = _diagnostics(request, db)
    logger.info("系统诊断完成 status=%s", payload["status"])
    return ok(payload)


@router.get("/system/diagnostics/report.{format}")
def diagnostics_report(format: str, request: Request, db: Session = Depends(get_db)):
    payload = _diagnostics(request, db)
    if format == "json":
        body, media, suffix = json.dumps(payload, ensure_ascii=False, indent=2), "application/json", "json"
    elif format == "txt":
        lines = [f"工程水害管理系统诊断报告", f"生成时间：{payload['generated_at']}", f"总体状态：{payload['status']}"]
        lines.extend(f"{key}：{value.get('status', value)}" for key, value in payload["checks"].items())
        body, media, suffix = "\n".join(lines), "text/plain", "txt"
    else:
        raise HTTPException(404, "仅支持 TXT 或 JSON 诊断报告")
    return Response(body, media_type=f"{media}; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="zhiyu_diagnostics.{suffix}"'})


@router.post("/system/backups/quick")
def quick_backup():
    payload = create_backup()
    logger.info("快速备份完成 path=%s", payload["path"])
    return ok(payload, "快速备份已完成")


@router.post("/system/backups/manifest")
def manifest_backup():
    return ok(create_backup(include_manifest=True), "完整备份清单已生成")


@router.get("/system/artifacts/integrity")
def get_artifact_integrity(db: Session = Depends(get_db)):
    return ok(artifact_integrity(db))


@router.get("/construction-projects/{project_id}/workflow-status")
def workflow_status(project_id: str, db: Session = Depends(get_db)):
    try:
        return ok(project_workflow(db, project_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/construction-projects/{project_id}/data-completeness")
def data_completeness(project_id: str, db: Session = Depends(get_db)):
    try:
        return ok(project_completeness(db, project_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
