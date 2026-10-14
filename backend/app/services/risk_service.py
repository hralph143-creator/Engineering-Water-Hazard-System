from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.models import ChangeAnalysis, InferenceResult, RiskAssessment, UavRiskOverlay, UavVerificationRecord

RISK_RULE_VERSION = "water-risk-v1"
OPERATION_NOTE = "用于辅助现场巡检和安全核验，不替代正式安全检查和施工决策。"
BOUNDARY_NOTE = "本结果仅反映遥感积水与时空变化特征，用于施工区域水害辅助研判，不替代现场安全检查、道路承载能力评价、边坡稳定性评价及正式停复工决策。"

REASON_TEXT = {
    "CURRENT_WATER_PRESENT": "当前识别结果存在积水",
    "CURRENT_WATER_HIGH": "当前积水占比较高",
    "NEW_WATER_DETECTED": "发现新增积水",
    "NEW_WATER_EXPANDING": "新增积水比例达到高关注阈值",
    "PERSISTENT_WATER_PRESENT": "存在持续积水",
    "PERSISTENT_WATER_HIGH": "持续积水范围较大",
    "WATER_RECEDING": "存在积水消退区域",
    "NET_EXPANSION": "整体积水呈扩大趋势",
    "NET_REDUCTION": "整体积水呈缩小趋势",
    "SPATIAL_ANALYSIS_AVAILABLE": "具备空间一致的像素级变化成果",
}


class RiskAssessmentError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_rules(change: ChangeAnalysis, cfg=None) -> dict:
    """透明的触发式规则；不计算未经标定的风险分数。"""
    app_cfg = cfg or get_config()
    rules = app_cfg.risk_rules
    current = float(change.t2_water_ratio or 0)
    new = float(change.new_water_ratio or 0)
    persistent = float(change.persistent_water_ratio or 0)
    receded = float(change.receded_water_ratio or 0)
    net = float(change.net_change_ratio or 0)
    codes: list[str] = ["SPATIAL_ANALYSIS_AVAILABLE"]
    if current > 0: codes.append("CURRENT_WATER_PRESENT")
    if current >= app_cfg.risk.high: codes.append("CURRENT_WATER_HIGH")
    if new > 0: codes.append("NEW_WATER_DETECTED")
    if new >= rules.new_high: codes.append("NEW_WATER_EXPANDING")
    if persistent > 0: codes.append("PERSISTENT_WATER_PRESENT")
    if persistent >= rules.persistent_high: codes.append("PERSISTENT_WATER_HIGH")
    if receded > 0: codes.append("WATER_RECEDING")
    if net >= rules.net_expansion: codes.append("NET_EXPANSION")
    if net <= rules.net_reduction: codes.append("NET_REDUCTION")

    high = (
        current >= app_cfg.risk.high
        or new >= rules.new_high
        or persistent >= rules.persistent_high
        or (current >= app_cfg.risk.medium and new >= rules.new_attention and net >= rules.net_expansion)
    )
    medium = (
        current >= app_cfg.risk.medium
        or new >= rules.new_attention
        or persistent >= rules.persistent_attention
        or net >= rules.net_expansion
    )
    level = "高水害关注" if high else "中水害关注" if medium else "低水害关注"
    advice = []
    if "NEW_WATER_DETECTED" in codes:
        advice.append("建议优先核查新增积水区域及其周边施工条件。")
    if "PERSISTENT_WATER_PRESENT" in codes:
        advice.append("建议将持续积水区域列入重点巡检范围，并核实施工排水情况。")
    if "NET_EXPANSION" in codes:
        advice.append("当前积水范围较前期扩大，建议提高现场巡查频次。")
    if "WATER_RECEDING" in codes:
        advice.append("消退区域建议在恢复机械作业前核查地面泥泞、承载及排水条件。")
    if not advice:
        advice.append("建议结合现场巡检确认一般区域实际状况及其他施工风险。")
    return {
        "attention_level": level,
        "reason_codes": codes,
        "reason_text": [REASON_TEXT[code] for code in codes],
        "inspection_advice": advice,
        "rule_version": rules.version,
    }


def _generate_zone(change_path: Path, tif_path: Path, png_path: Path) -> dict:
    with rasterio.open(change_path) as src:
        change = src.read(1)
        profile = src.profile.copy()
    zone = np.zeros(change.shape, dtype=np.uint8)
    zone[change == 0] = 1  # 持续非积水 → 一般区域
    zone[change == 2] = 2  # 消退积水 → 重点巡检区域
    zone[(change == 1) | (change == 3)] = 3  # 新增/持续积水 → 高水害关注区域
    profile.update(count=1, dtype="uint8", nodata=0, compress="deflate")
    with rasterio.open(tif_path, "w", **profile) as dst:
        dst.write(zone, 1)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(zone, cmap=ListedColormap(["#d7dde0", "#eef2f3", "#d89a3d", "#d84d4d"]), vmin=0, vmax=3)
    ax.set_title("施工区域水害关注区域图")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(png_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return {
        "nodata_pixels": int((zone == 0).sum()),
        "general_pixels": int((zone == 1).sum()),
        "inspection_pixels": int((zone == 2).sum()),
        "high_attention_pixels": int((zone == 3).sum()),
    }


def serialize_risk(row: RiskAssessment) -> dict:
    def values(raw: str) -> list:
        try: return json.loads(raw)
        except (TypeError, json.JSONDecodeError): return []
    return {
        "id": row.id, "project_id": row.project_id, "assessment_name": row.assessment_name,
        "source_type": row.source_type,
        "source_change_analysis_id": row.source_change_analysis_id,
        "source_result_id": row.source_result_id,
        "source_t1_name": row.source_t1_name, "source_t2_name": row.source_t2_name,
        "status": row.status, "attention_level": row.attention_level,
        "rule_version": row.rule_version, "created_at": row.created_at,
        "completed_at": row.completed_at, "current_water_ratio": row.current_water_ratio,
        "new_water_ratio": row.new_water_ratio, "persistent_water_ratio": row.persistent_water_ratio,
        "receded_water_ratio": row.receded_water_ratio, "net_change_ratio": row.net_change_ratio,
        "confidence_snapshot": row.confidence_snapshot,
        "risk_reason_codes": values(row.risk_reason_codes),
        "risk_reason_text": values(row.risk_reason_text),
        "inspection_advice": values(row.inspection_advice),
        "operation_note": row.operation_note,
        "zone_counts": {"high_attention": row.high_attention_pixels, "inspection": row.inspection_pixels,
                        "general": row.general_pixels, "nodata": row.nodata_pixels},
        "image_url": f"/api/risk-assessments/{row.id}/image" if row.risk_png_path else None,
        "geotiff_url": f"/api/risk-assessments/{row.id}/geotiff" if row.risk_geotiff_path else None,
        "error_message": row.error_message,
        "boundary_note": BOUNDARY_NOTE,
    }


def create_risk_assessment(db: Session, source_change_analysis_id: str, name: str | None = None) -> RiskAssessment:
    change = db.get(ChangeAnalysis, source_change_analysis_id)
    if not change:
        raise RiskAssessmentError("来源时空变化分析不存在")
    if change.status != "completed":
        raise RiskAssessmentError("仅已完成的时空变化分析可用于风险研判")
    if not change.change_geotiff_path or not Path(change.change_geotiff_path).is_file():
        raise RiskAssessmentError("来源变化分析 GeoTIFF 不存在")
    cfg = get_config()
    decision = evaluate_rules(change, cfg)
    assessment_id = str(uuid.uuid4())
    default_name = f"{change.dataset_t1_name} → {change.dataset_t2_name} 水害辅助研判"
    result_t2 = db.get(InferenceResult, change.result_t2_id)
    row = RiskAssessment(
        id=assessment_id, assessment_name=(name or default_name).strip() or default_name,
        source_type="change_analysis", source_change_analysis_id=change.id,
        source_t1_name=change.dataset_t1_name, source_t2_name=change.dataset_t2_name,
        status="running", rule_version=decision["rule_version"],
        current_water_ratio=change.t2_water_ratio, new_water_ratio=change.new_water_ratio,
        persistent_water_ratio=change.persistent_water_ratio,
        receded_water_ratio=change.receded_water_ratio, net_change_ratio=change.net_change_ratio,
        confidence_snapshot=result_t2.mean_confidence if result_t2 else None,
        operation_note=OPERATION_NOTE,
        project_id=change.project_id,
    )
    db.add(row); db.commit()
    out_dir = cfg.paths.outputs / "risks" / assessment_id
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
        tif_path, png_path = out_dir / "risk_zone.tif", out_dir / "risk_zone.png"
        counts = _generate_zone(Path(change.change_geotiff_path), tif_path, png_path)
        row.attention_level = decision["attention_level"]
        row.risk_reason_codes = json.dumps(decision["reason_codes"], ensure_ascii=False)
        row.risk_reason_text = json.dumps(decision["reason_text"], ensure_ascii=False)
        row.inspection_advice = json.dumps(decision["inspection_advice"], ensure_ascii=False)
        for key, value in counts.items(): setattr(row, key, value)
        row.risk_png_path, row.risk_geotiff_path = str(png_path), str(tif_path)
        row.summary_json_path = str(out_dir / "summary.json")
        row.status, row.completed_at = "completed", _now()
        summary = serialize_risk(row)
        Path(row.summary_json_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        db.commit(); db.refresh(row)
        return row
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        row.status, row.error_message, row.completed_at = "failed", str(exc), _now()
        db.commit()
        raise RiskAssessmentError(str(exc)) from exc


def change_is_referenced(db: Session, change_id: str) -> bool:
    return bool(db.scalar(select(RiskAssessment.id).where(
        RiskAssessment.source_change_analysis_id == change_id
    ).limit(1)))


def delete_risk_assessment(db: Session, row: RiskAssessment) -> None:
    from ..db.models import OperationAssistanceRecord
    if db.scalar(select(OperationAssistanceRecord.id).where(OperationAssistanceRecord.risk_assessment_id == row.id).limit(1)):
        raise RiskAssessmentError("该风险研判已有作业辅助事项，为保证历史证据链不能删除。")
    if db.scalar(select(UavRiskOverlay.id).where(UavRiskOverlay.risk_assessment_id == row.id).limit(1)) or db.scalar(select(UavVerificationRecord.id).where(UavVerificationRecord.risk_assessment_id == row.id).limit(1)):
        raise RiskAssessmentError("该风险研判已有无人机空间覆盖或人工复核记录，为保证历史可追溯不能删除。")
    output_root = (get_config().paths.outputs / "risks").resolve()
    target = (output_root / row.id).resolve()
    if target.parent != output_root:
        raise RiskAssessmentError("风险研判成果目录无效")
    if target.exists():
        try: shutil.rmtree(target)
        except OSError as exc: raise RiskAssessmentError("风险研判成果删除失败，记录未删除") from exc
    db.delete(row); db.commit()
