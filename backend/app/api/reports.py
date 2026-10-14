"""遥感识别专业报告 API：仅使用持久化结果，不接入 AI 对话内容。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import InferenceResult
from ..services.report_service import generate_report_html, generate_report_pdf

router = APIRouter(prefix="/results", tags=["results"])


def _find_result(result_id: str, db: Session) -> InferenceResult:
    row = db.get(InferenceResult, result_id)
    if not row:
        raise HTTPException(status_code=404, detail="识别结果不存在")
    return row


@router.get("/{result_id}/report.html", response_class=HTMLResponse)
def result_report(result_id: str, db: Session = Depends(get_db)):
    return HTMLResponse(content=generate_report_html(_find_result(result_id, db)))


@router.get("/{result_id}/report.pdf")
def result_report_pdf(result_id: str, db: Session = Depends(get_db)):
    """下载无浏览器页眉页脚的 A4 PDF 报告。"""
    row = _find_result(result_id, db)
    pdf = generate_report_pdf(row)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report_{result_id[:8]}.pdf"'
        },
    )
