from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db.models import InferenceTask, RainfallObservation


FORECAST_HOURS = (6, 12, 24)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_of(task: InferenceTask) -> datetime | None:
    return task.dataset.capture_time or task.finished_at or (task.result.created_at if task.result else None)


def _linear_fit(points: list[tuple[datetime, float]]) -> tuple[float, float]:
    """Return slope per hour and R² for a small, ordered observation series."""
    origin = _utc(points[0][0])
    xs = [(_utc(moment) - origin).total_seconds() / 3600 for moment, _ in points]
    ys = [value for _, value in points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator <= 1e-9:
        return 0.0, 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    fitted = [y_mean + slope * (x - x_mean) for x in xs]
    residual = sum((y - estimate) ** 2 for y, estimate in zip(ys, fitted))
    total = sum((y - y_mean) ** 2 for y in ys)
    return slope, max(0.0, min(1.0, 1 - residual / total)) if total > 1e-9 else 1.0


def build_forecast(db: Session, project_id: str | None = None) -> dict:
    statement = (
        select(InferenceTask)
        .options(joinedload(InferenceTask.dataset), joinedload(InferenceTask.result))
        .where(InferenceTask.status == "completed", InferenceTask.result.has())
    )
    if project_id:
        statement = statement.where(InferenceTask.project_id == project_id)
    tasks = db.scalars(statement).unique().all()
    usable = [task for task in tasks if task.result and _time_of(task)]
    usable.sort(key=lambda task: _utc(_time_of(task)))
    # 未指定项目时，以最新成果所属项目为范围，避免把不同工地的序列拼接成一条趋势。
    effective_project_id = project_id or (usable[-1].project_id if usable else None)
    if not project_id and effective_project_id:
        usable = [task for task in usable if task.project_id == effective_project_id]
    usable = usable[-12:]

    base = {
        "mode": "TREND_EXTRAPOLATION",
        "project_id": effective_project_id,
        "sample_count": len(usable),
        "minimum_samples": 2,
        "ready": len(usable) >= 2,
        "generated_at": datetime.now(timezone.utc),
        "disclaimer": "本结果是基于历史识别序列的短时趋势外推，不是天气预报或水动力模拟，不能替代现场巡查与安全决策。",
    }
    if not usable:
        return {**base, "status": "NO_DATA", "message": "暂无已完成的积水识别成果。", "series": [], "projections": [], "source": None, "rainfall": None}

    latest = usable[-1]
    latest_time = _utc(_time_of(latest))
    source = {
        "task_id": latest.id,
        "result_id": latest.result.id,
        "dataset_name": latest.dataset.name,
        "observed_at": latest_time,
        "flood_ratio": latest.result.flood_ratio,
        "risk_level": latest.result.risk_level,
        "image_url": f"/api/results/{latest.result.id}/files/vv_preview",
        "mask_url": f"/api/results/{latest.result.id}/files/flow_mask",
    }
    series = [{"observed_at": _utc(_time_of(task)), "flood_ratio": task.result.flood_ratio, "task_id": task.id} for task in usable]

    rain_statement = select(RainfallObservation)
    if effective_project_id:
        rain_statement = rain_statement.where(RainfallObservation.project_id == effective_project_id)
    rainfall_rows = db.scalars(rain_statement.order_by(RainfallObservation.observed_at.desc()).limit(5000)).all()
    rain_end = max((_utc(row.observed_at) for row in rainfall_rows), default=None)
    rain_start = rain_end - timedelta(hours=72) if rain_end else None
    recent_rain = [row for row in rainfall_rows if rain_start and _utc(row.observed_at) >= rain_start]
    rainfall = {
        "has_data": bool(rainfall_rows),
        "latest_observation_at": rain_end,
        "rainfall_72h_mm": round(sum(row.rainfall_mm for row in recent_rain), 2) if rain_end else None,
        "observation_count_72h": len(recent_rain),
        "note": "仅作为已观测降雨背景，不包含未来降雨预报。",
    }

    if len(usable) < 2:
        return {**base, "status": "INSUFFICIENT_DATA", "message": "至少需要两个不同时间的识别成果才能进行趋势推演。", "series": series, "projections": [], "source": source, "rainfall": rainfall}

    points = [(_time_of(task), float(task.result.flood_ratio)) for task in usable]
    span_hours = (_utc(points[-1][0]) - _utc(points[0][0])).total_seconds() / 3600
    if span_hours <= 0:
        return {**base, "ready": False, "status": "INSUFFICIENT_TIME_SPAN", "message": "识别成果缺少有效时间跨度，无法进行趋势推演。", "series": series, "projections": [], "source": source, "rainfall": rainfall}

    slope, fit = _linear_fit(points)
    deltas = [points[index][1] - points[index - 1][1] for index in range(1, len(points))]
    volatility = sqrt(sum((delta - sum(deltas) / len(deltas)) ** 2 for delta in deltas) / len(deltas)) if deltas else 0.0
    confidence_score = max(0.15, min(0.95, 0.35 + min(len(points), 8) * 0.055 + fit * 0.18 + min(span_hours, 72) / 72 * 0.08 - min(volatility, 0.25)))
    confidence = "HIGH" if confidence_score >= 0.75 else "MEDIUM" if confidence_score >= 0.5 else "LOW"
    direction = "RISING" if slope > 0.0002 else "FALLING" if slope < -0.0002 else "STABLE"
    uncertainty_per_hour = max(0.0025, volatility / max(1, len(deltas)))
    projections = []
    for hours in FORECAST_HOURS:
        center = max(0.0, min(1.0, points[-1][1] + slope * hours))
        spread = min(0.25, uncertainty_per_hour * sqrt(hours) + (1 - confidence_score) * 0.025)
        projections.append({
            "hours": hours,
            "at": latest_time + timedelta(hours=hours),
            "flood_ratio": round(center, 6),
            "lower": round(max(0.0, center - spread), 6),
            "upper": round(min(1.0, center + spread), 6),
        })
    return {
        **base,
        "status": "READY",
        "message": "趋势推演已生成。",
        "direction": direction,
        "slope_per_hour": round(slope, 8),
        "confidence": confidence,
        "confidence_score": round(confidence_score, 3),
        "fit_r2": round(fit, 3),
        "time_span_hours": round(span_hours, 1),
        "series": series,
        "projections": projections,
        "source": source,
        "rainfall": rainfall,
    }
