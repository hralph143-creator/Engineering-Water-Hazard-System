from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path
from urllib.parse import urlsplit

from ..config import EquipmentPositionConfig, RainfallConfig, RiskRuleConfig, TerrainConfig, WorkAreaImpactConfig, get_config, get_llm_config, save_editable_settings, save_local_llm_settings
from ..schemas.common import ok

router = APIRouter(prefix="/settings", tags=["settings"])


def _settings_payload(cfg) -> dict:
    llm = get_llm_config()
    secret = llm.api_key.get_secret_value().strip() if llm.api_key else ""
    return {
        "model_checkpoint": str(cfg.model.checkpoint), "sar_stats": str(cfg.model.sar_stats),
        "output_directory": str(cfg.paths.outputs), "upload_directory": str(cfg.paths.uploads),
        "database_path": str(cfg.paths.database), "default_threshold": cfg.model.threshold,
        "device": cfg.model.device, "risk": cfg.risk.model_dump(),
        "risk_note": "基础水害提示沿用现有积水识别占比规则，仅用于辅助分析，不替代现场安全检查和施工安全决策。",
        "risk_rules": getattr(cfg, "risk_rules", RiskRuleConfig()).model_dump(),
        "risk_rules_note": "当前参数用于科研与工程场景验证，不属于行业强制安全标准。识别阈值与辅助研判规则相互独立。",
        "work_area_impact": getattr(cfg, "work_area_impact", WorkAreaImpactConfig()).model_dump(),
        "work_area_impact_note": "施工区域影响阈值用于科研和工程场景验证，不属于行业强制安全标准。",
        "equipment_position": getattr(cfg, "equipment_position", EquipmentPositionConfig()).model_dump(),
        "equipment_position_note": "设备位置过期阈值仅用于位置快照时效判断，不属于安全法规阈值。",
        "terrain": getattr(cfg, "terrain", TerrainConfig()).model_dump(),
        "terrain_note": "低高程分位参数仅用于相对地形分区，不属于行业水害或安全标准。坡度单位为度。",
        "rainfall": getattr(cfg, "rainfall", RainfallConfig()).model_dump(),
        "rainfall_note": "降雨事件间隔和前置窗口仅用于数据组织与辅助分析，不属于气象预警或工程停工标准。",
        "ai": {
            "enabled": llm.enabled,
            "base_url": llm.base_url,
            "model": llm.model,
            "key_configured": bool(secret),
            "key_hint": f"{secret[:6]}...{secret[-4:]}" if len(secret) >= 12 else ("已配置" if secret else None),
            "key_length": len(secret) if secret else 0,
            "timeout": llm.timeout,
            "temperature": llm.temperature,
            "max_output_tokens": llm.max_output_tokens,
        },
    }


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float | None = None
    low: float | None = None
    medium: float | None = None
    high: float | None = None
    model_checkpoint: str | None = Field(default=None, max_length=1024)
    sar_stats: str | None = Field(default=None, max_length=1024)
    output_directory: str | None = Field(default=None, max_length=1024)
    upload_directory: str | None = Field(default=None, max_length=1024)
    database_path: str | None = Field(default=None, max_length=1024)
    ai_enabled: bool | None = None
    ai_base_url: str | None = Field(default=None, max_length=2048)
    ai_model: str | None = Field(default=None, max_length=256)
    ai_api_key: str | None = Field(default=None, max_length=4096)
    ai_clear_api_key: bool = False
    ai_timeout: float | None = Field(default=None, ge=5, le=300)
    ai_temperature: float | None = Field(default=None, ge=0, le=2)
    ai_max_output_tokens: int | None = Field(default=None, ge=128, le=8192)
    risk_new_attention: float | None = Field(default=None, ge=0, le=1)
    risk_new_high: float | None = Field(default=None, ge=0, le=1)
    risk_persistent_attention: float | None = Field(default=None, ge=0, le=1)
    risk_persistent_high: float | None = Field(default=None, ge=0, le=1)
    risk_net_expansion: float | None = Field(default=None, gt=0, le=1)
    risk_net_reduction: float | None = Field(default=None, ge=-1, lt=0)
    impact_high_attention_ratio: float | None = Field(default=None, ge=0, le=1)
    impact_inspection_ratio: float | None = Field(default=None, ge=0, le=1)
    position_stale_hours: float | None = Field(default=None, gt=0, le=8760)
    low_elevation_percentile: float | None = Field(default=None, gt=0, lt=100)
    rain_event_gap_hours: float | None = Field(default=None, gt=0, le=168)
    change_rainfall_pre_window_hours: float | None = Field(default=None, ge=0, le=720)


def _validate_path(name: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{name}不能为空")
    if "\x00" in value:
        raise HTTPException(status_code=400, detail=f"{name}包含无效字符")
    return value


def _validate_ai_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="AI 接口地址必须是有效的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=400, detail="AI 接口地址不能包含账号、密码、查询参数或片段")
    return value


@router.get("")
def get_settings():
    return ok(_settings_payload(get_config()))


@router.put("")
def update_settings(payload: SettingsUpdate):
    cfg = get_config()
    llm = get_llm_config()
    threshold = payload.threshold if payload.threshold is not None else cfg.model.threshold
    low = payload.low if payload.low is not None else cfg.risk.low
    medium = payload.medium if payload.medium is not None else cfg.risk.medium
    high = payload.high if payload.high is not None else cfg.risk.high
    rr = getattr(cfg, "risk_rules", RiskRuleConfig())
    risk_rule_values = {
        "risk_new_attention": payload.risk_new_attention if payload.risk_new_attention is not None else rr.new_attention,
        "risk_new_high": payload.risk_new_high if payload.risk_new_high is not None else rr.new_high,
        "risk_persistent_attention": payload.risk_persistent_attention if payload.risk_persistent_attention is not None else rr.persistent_attention,
        "risk_persistent_high": payload.risk_persistent_high if payload.risk_persistent_high is not None else rr.persistent_high,
        "risk_net_expansion": payload.risk_net_expansion if payload.risk_net_expansion is not None else rr.net_expansion,
        "risk_net_reduction": payload.risk_net_reduction if payload.risk_net_reduction is not None else rr.net_reduction,
    }
    risk_rule_updates = {
        key: value for key, value in {
            "risk_new_attention": payload.risk_new_attention,
            "risk_new_high": payload.risk_new_high,
            "risk_persistent_attention": payload.risk_persistent_attention,
            "risk_persistent_high": payload.risk_persistent_high,
            "risk_net_expansion": payload.risk_net_expansion,
            "risk_net_reduction": payload.risk_net_reduction,
        }.items() if value is not None
    }
    impact_cfg = getattr(cfg, "work_area_impact", WorkAreaImpactConfig())
    impact_values = {
        "impact_high_attention_ratio": payload.impact_high_attention_ratio if payload.impact_high_attention_ratio is not None else impact_cfg.high_attention_ratio,
        "impact_inspection_ratio": payload.impact_inspection_ratio if payload.impact_inspection_ratio is not None else impact_cfg.inspection_ratio,
    }
    impact_updates = {key:value for key,value in {
        "impact_high_attention_ratio":payload.impact_high_attention_ratio,
        "impact_inspection_ratio":payload.impact_inspection_ratio,
    }.items() if value is not None}
    # 后端二次校验（不能只依赖前端）
    if not 0 < threshold < 1:
        raise HTTPException(status_code=400, detail="识别阈值必须满足 0 < 阈值 < 1")
    if not 0 <= low < medium < high <= 1:
        raise HTTPException(status_code=400, detail="基础水害提示阈值必须满足 0 ≤ 阈值一 < 阈值二 < 阈值三 ≤ 1")
    if not 0 <= risk_rule_values["risk_new_attention"] < risk_rule_values["risk_new_high"] <= 1:
        raise HTTPException(status_code=400, detail="新增积水研判阈值顺序无效")
    if not 0 <= risk_rule_values["risk_persistent_attention"] < risk_rule_values["risk_persistent_high"] <= 1:
        raise HTTPException(status_code=400, detail="持续积水研判阈值顺序无效")
    if not -1 <= risk_rule_values["risk_net_reduction"] < 0 < risk_rule_values["risk_net_expansion"] <= 1:
        raise HTTPException(status_code=400, detail="净变化研判阈值顺序无效")
    if any(not 0 <= value <= 1 for value in impact_values.values()):
        raise HTTPException(status_code=400, detail="施工区域影响阈值必须位于 0 到 1")
    position_cfg = getattr(cfg, "equipment_position", EquipmentPositionConfig())
    position_stale_hours = payload.position_stale_hours if payload.position_stale_hours is not None else position_cfg.position_stale_hours
    if not 0 < position_stale_hours <= 8760:
        raise HTTPException(status_code=400, detail="设备位置过期阈值必须大于0且不超过8760小时")
    position_updates = {"position_stale_hours": payload.position_stale_hours} if payload.position_stale_hours is not None else {}
    terrain_cfg = getattr(cfg, "terrain", TerrainConfig())
    low_elevation_percentile = payload.low_elevation_percentile if payload.low_elevation_percentile is not None else terrain_cfg.low_elevation_percentile
    if not 0 < low_elevation_percentile < 100:
        raise HTTPException(status_code=400, detail="低高程分位阈值必须位于0到100之间")
    terrain_updates = {"low_elevation_percentile": payload.low_elevation_percentile} if payload.low_elevation_percentile is not None else {}
    rainfall_cfg = getattr(cfg, "rainfall", RainfallConfig())
    rain_event_gap_hours = payload.rain_event_gap_hours if payload.rain_event_gap_hours is not None else rainfall_cfg.rain_event_gap_hours
    change_rainfall_pre_window_hours = payload.change_rainfall_pre_window_hours if payload.change_rainfall_pre_window_hours is not None else rainfall_cfg.change_rainfall_pre_window_hours
    if not 0 < rain_event_gap_hours <= 168 or not 0 <= change_rainfall_pre_window_hours <= 720:
        raise HTTPException(status_code=400, detail="降雨事件参数超出允许范围")
    rainfall_updates = {key:value for key,value in {
        "rain_event_gap_hours":payload.rain_event_gap_hours,
        "change_rainfall_pre_window_hours":payload.change_rainfall_pre_window_hours,
    }.items() if value is not None}
    path_updates = {}
    path_fields = {
        "model_checkpoint": ("模型权重路径", payload.model_checkpoint),
        "sar_stats": ("SAR 预处理统计路径", payload.sar_stats),
        "output_directory": ("识别输出目录", payload.output_directory),
        "upload_directory": ("上传目录", payload.upload_directory),
        "database_path": ("SQLite 数据库路径", payload.database_path),
    }
    for key, (label, raw) in path_fields.items():
        if raw is not None:
            path_updates[key] = _validate_path(label, raw)
    suffix_rules = {
        "model_checkpoint": ({".pt", ".pth"}, "模型权重文件应为 .pt 或 .pth"),
        "sar_stats": ({".json"}, "SAR 预处理统计文件应为 .json"),
        "database_path": ({".db", ".sqlite", ".sqlite3"}, "数据库文件应为 .db、.sqlite 或 .sqlite3"),
    }
    for key, (suffixes, message) in suffix_rules.items():
        if key in path_updates and Path(path_updates[key]).suffix.lower() not in suffixes:
            raise HTTPException(status_code=400, detail=message)
    for key, label in (("output_directory", "识别输出目录"), ("upload_directory", "上传目录")):
        if key in path_updates and Path(path_updates[key]).is_file():
            raise HTTPException(status_code=400, detail=f"{label}不能指向文件")
    ai_enabled = payload.ai_enabled if payload.ai_enabled is not None else llm.enabled
    ai_base_url = _validate_ai_url(payload.ai_base_url) if payload.ai_base_url is not None else llm.base_url
    ai_model = payload.ai_model.strip() if payload.ai_model is not None else llm.model
    ai_timeout = payload.ai_timeout if payload.ai_timeout is not None else llm.timeout
    ai_temperature = payload.ai_temperature if payload.ai_temperature is not None else llm.temperature
    ai_max_tokens = payload.ai_max_output_tokens if payload.ai_max_output_tokens is not None else llm.max_output_tokens
    api_key = payload.ai_api_key.strip() if payload.ai_api_key is not None else None
    if payload.ai_api_key is not None and not api_key:
        api_key = None  # 空输入表示保留现有密钥
    key_available = bool(api_key) or (
        bool(llm.api_key and llm.api_key.get_secret_value().strip()) and not payload.ai_clear_api_key
    )
    if ai_enabled and (not ai_model or not key_available):
        raise HTTPException(status_code=400, detail="启用 AI 前请填写模型名称并配置 API Key")
    try:
        save_editable_settings(
            threshold=threshold, low=low, medium=medium, high=high,
            **risk_rule_updates, **impact_updates, **position_updates, **terrain_updates, **rainfall_updates, **path_updates,
        )
        save_local_llm_settings(
            enabled=ai_enabled, base_url=ai_base_url, model=ai_model,
            timeout=ai_timeout, temperature=ai_temperature,
            max_output_tokens=ai_max_tokens,
            api_key=(api_key if api_key is not None else (
                llm.api_key.get_secret_value() if llm.api_key and not payload.ai_clear_api_key else None
            )),
            clear_api_key=payload.ai_clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(_settings_payload(get_config()), "设置已保存并生效")
