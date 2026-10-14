from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_llm_config
from ..db.database import get_db
from ..schemas.ai import AIAssistanceRequest, AIChangeAnalysisRequest, AICompareRequest, AIRiskAssessmentRequest, AITaskRequest
from ..schemas.common import ok
from ..services.ai_context import (
    AIContextError, build_change_analysis_context, build_compare_context,
    build_assistance_context, build_risk_assessment_context, build_task_context,
)
from ..services.ai_prompts import (
    AI_SYSTEM_PROMPT, ASSISTANCE_GUIDE, CHANGE_ANALYSIS_GUIDE, COMPARE_ANALYSIS_GUIDE, RISK_ASSESSMENT_GUIDE,
    TASK_ANALYSIS_GUIDE, TASK_CHAT_GUIDE,
)
from ..services.ai_service import AIService, AIServiceError, public_status

router = APIRouter(prefix="/ai", tags=["ai"])


def get_ai_service() -> AIService:
    return AIService(get_llm_config())


def _context_message(context: dict, guide: str) -> str:
    return (
        "以下 JSON 是系统从数据库和既有分析服务提取的只读结构化上下文。"
        "JSON 中未出现的业务事实均视为未知，不得推测。\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n\n{guide}"
    )


def _task_messages(payload: AITaskRequest, context: dict) -> list[dict[str, str]]:
    guide = TASK_CHAT_GUIDE if payload.mode == "chat" else TASK_ANALYSIS_GUIDE
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "system", "content": _context_message(context, guide)},
    ]
    if payload.mode == "chat" and payload.existing_analysis:
        messages.append({
            "role": "system",
            "content": (
                "以下内容是当前页面已经向用户展示过的专业分析，仅用于避免重复。"
                "不得复制其句子、结构或大段结论；请针对用户的新问题提供筛选后的直接回答。\n"
                "<existing_analysis>\n"
                f"{payload.existing_analysis.strip()}\n"
                "</existing_analysis>"
            ),
        })
    messages.extend(item.model_dump() for item in payload.messages)
    messages.append({"role": "user", "content": payload.question.strip()})
    return messages


def _compare_messages(payload: AICompareRequest, context: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "system", "content": _context_message(context, COMPARE_ANALYSIS_GUIDE)},
        {"role": "user", "content": payload.question.strip()},
    ]


def _context_error(exc: AIContextError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _service_error(exc: AIServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_response(service: AIService, messages: list[dict[str, str]]) -> AsyncIterator[str]:
    try:
        async for chunk in service.stream(messages):
            yield _sse("delta", {"content": chunk})
        yield _sse("done", {"finish_reason": "stop"})
    except AIServiceError as exc:
        yield _sse("error", {"code": exc.code, "message": str(exc)})


@router.get("/status")
def ai_status():
    return ok(public_status(get_llm_config()))


@router.post("/analyze")
async def analyze_task(payload: AITaskRequest, db: Session = Depends(get_db)):
    try:
        context = build_task_context(db, payload.task_id)
        content = await get_ai_service().complete(_task_messages(payload, context))
    except AIContextError as exc:
        raise _context_error(exc) from exc
    except AIServiceError as exc:
        raise _service_error(exc) from exc
    return ok({"content": content, "task_id": payload.task_id})


@router.post("/analyze/stream")
def analyze_task_stream(payload: AITaskRequest, db: Session = Depends(get_db)):
    try:
        context = build_task_context(db, payload.task_id)
        service = get_ai_service()
        service.ensure_available()
    except AIContextError as exc:
        raise _context_error(exc) from exc
    except AIServiceError as exc:
        raise _service_error(exc) from exc
    return StreamingResponse(
        _stream_response(service, _task_messages(payload, context)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/compare/stream")
def compare_stream(payload: AICompareRequest, db: Session = Depends(get_db)):
    try:
        context = build_compare_context(db, payload.result_ids)
        service = get_ai_service()
        service.ensure_available()
    except AIContextError as exc:
        raise _context_error(exc) from exc
    except AIServiceError as exc:
        raise _service_error(exc) from exc
    return StreamingResponse(
        _stream_response(service, _compare_messages(payload, context)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/change-analysis/stream")
def change_analysis_stream(payload: AIChangeAnalysisRequest, db: Session = Depends(get_db)):
    try:
        context = build_change_analysis_context(db, payload.analysis_id)
        service = get_ai_service()
        service.ensure_available()
    except AIContextError as exc:
        raise _context_error(exc) from exc
    except AIServiceError as exc:
        raise _service_error(exc) from exc
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "system", "content": _context_message(context, CHANGE_ANALYSIS_GUIDE)},
        {"role": "user", "content": payload.question.strip()},
    ]
    return StreamingResponse(
        _stream_response(service, messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/risk-assessment/stream")
def risk_assessment_stream(payload: AIRiskAssessmentRequest, db: Session = Depends(get_db)):
    try:
        context = build_risk_assessment_context(db, payload.assessment_id)
        service = get_ai_service(); service.ensure_available()
    except AIContextError as exc: raise _context_error(exc) from exc
    except AIServiceError as exc: raise _service_error(exc) from exc
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "system", "content": _context_message(context, RISK_ASSESSMENT_GUIDE)},
        {"role": "user", "content": payload.question.strip()},
    ]
    return StreamingResponse(_stream_response(service, messages), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/operation-assistance/stream")
def assistance_stream(payload: AIAssistanceRequest, db: Session = Depends(get_db)):
    try:
        context=build_assistance_context(db,payload.assistance_record_id,payload.project_id)
        service=get_ai_service(); service.ensure_available()
    except AIContextError as exc: raise _context_error(exc) from exc
    except AIServiceError as exc: raise _service_error(exc) from exc
    messages=[{"role":"system","content":AI_SYSTEM_PROMPT},{"role":"system","content":_context_message(context,ASSISTANCE_GUIDE)},{"role":"user","content":payload.question.strip()}]
    return StreamingResponse(_stream_response(service,messages),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
