from functools import lru_cache
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, SecretStr

from .services.secret_store import SecretStoreError, protect_secret, unprotect_secret

PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class ModelConfig(BaseModel):
    checkpoint: Path
    sar_stats: Path
    experiment_summary: Path
    training_history: Path
    threshold: float = 0.5
    device: str = "auto"
    name: str = "U-Net"
    version: str = "Sen1Floods11-v1.1-baseline"


class PathConfig(BaseModel):
    uploads: Path
    outputs: Path
    database: Path


class RiskConfig(BaseModel):
    low: float = 0.02
    medium: float = 0.08
    high: float = 0.20

    def validate_order(self) -> None:
        if not 0 <= self.low < self.medium < self.high <= 1:
            raise ValueError("风险阈值必须满足 0 ≤ low < medium < high ≤ 1")


class RiskRuleConfig(BaseModel):
    """辅助研判验证参数，不属于行业强制安全标准。"""

    version: str = "water-risk-v1"
    new_attention: float = 0.01
    new_high: float = 0.05
    persistent_attention: float = 0.02
    persistent_high: float = 0.08
    net_expansion: float = 0.01
    net_reduction: float = -0.01

    def validate_rules(self) -> None:
        if not 0 <= self.new_attention < self.new_high <= 1:
            raise ValueError("新增积水研判阈值必须满足 0 ≤ 关注阈值 < 高关注阈值 ≤ 1")
        if not 0 <= self.persistent_attention < self.persistent_high <= 1:
            raise ValueError("持续积水研判阈值必须满足 0 ≤ 关注阈值 < 高关注阈值 ≤ 1")
        if not -1 <= self.net_reduction < 0 < self.net_expansion <= 1:
            raise ValueError("净变化阈值必须满足 -1 ≤ 缩小阈值 < 0 < 扩大阈值 ≤ 1")


class WorkAreaImpactConfig(BaseModel):
    """施工区域影响规则验证参数，不属于行业强制安全标准。"""

    version: str = "work-area-impact-v1"
    high_attention_ratio: float = 0.05
    inspection_ratio: float = 0.10

    def validate_rules(self) -> None:
        if not 0 <= self.high_attention_ratio <= 1:
            raise ValueError("高关注占比阈值必须位于 0 到 1")
        if not 0 <= self.inspection_ratio <= 1:
            raise ValueError("重点巡检占比阈值必须位于 0 到 1")


class EquipmentPositionConfig(BaseModel):
    """设备位置快照时效参数，不属于安全法规阈值。"""

    version: str = "equipment-water-link-v1"
    position_stale_hours: float = 24.0

    def validate_rules(self) -> None:
        if not 0 < self.position_stale_hours <= 8760:
            raise ValueError("设备位置过期阈值必须大于0且不超过8760小时")


class TerrainConfig(BaseModel):
    """相对地形分析参数，不属于行业水害或安全标准。"""
    version: str = "terrain-analysis-v1"
    low_elevation_percentile: float = 20.0

    def validate_rules(self) -> None:
        if not 0 < self.low_elevation_percentile < 100:
            raise ValueError("低高程分位阈值必须位于0到100之间")


class RainfallConfig(BaseModel):
    """降雨事件分段与背景窗口参数，不属于气象预警或停工标准。"""
    version: str = "rainfall-event-v1"
    rain_event_gap_hours: float = 6.0
    change_rainfall_pre_window_hours: float = 24.0

    def validate_rules(self) -> None:
        if not 0 < self.rain_event_gap_hours <= 168:
            raise ValueError("降雨事件间隔必须大于0且不超过168小时")
        if not 0 <= self.change_rainfall_pre_window_hours <= 720:
            raise ValueError("变化分析前置降雨窗口必须位于0到720小时")


class AppConfig(BaseModel):
    model: ModelConfig
    paths: PathConfig
    risk: RiskConfig
    risk_rules: RiskRuleConfig = RiskRuleConfig()
    work_area_impact: WorkAreaImpactConfig = WorkAreaImpactConfig()
    equipment_position: EquipmentPositionConfig = EquipmentPositionConfig()
    terrain: TerrainConfig = TerrainConfig()
    rainfall: RainfallConfig = RainfallConfig()


class LLMConfig(BaseModel):
    """AI 服务仅从环境变量读取，密钥使用 SecretStr 防止意外输出。"""

    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout: float = 60.0
    temperature: float = 0.2
    max_output_tokens: int = 1600

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.api_key
            and self.api_key.get_secret_value().strip()
            and self.base_url.strip()
            and self.model.strip()
        )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    path = CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = AppConfig.model_validate(raw)
    base = path.parent.resolve()
    for field in ("checkpoint", "sar_stats", "experiment_summary", "training_history"):
        value = getattr(cfg.model, field)
        if not value.is_absolute():
            setattr(cfg.model, field, (base / value).resolve())
    for field in ("uploads", "outputs", "database"):
        value = getattr(cfg.paths, field)
        if not value.is_absolute():
            setattr(cfg.paths, field, (base / value).resolve())
    cfg.risk.validate_order()
    cfg.risk_rules.validate_rules()
    cfg.work_area_impact.validate_rules()
    cfg.equipment_position.validate_rules()
    cfg.terrain.validate_rules()
    cfg.rainfall.validate_rules()
    for directory in (cfg.paths.uploads, cfg.paths.outputs, cfg.paths.database.parent):
        directory.mkdir(parents=True, exist_ok=True)
    return cfg


def _default_config_path() -> Path:
    explicit = os.environ.get("FLOODRISK_CONFIG_PATH")
    if explicit:
        return Path(explicit)
    local = os.environ.get("LOCALAPPDATA")
    user_config = Path(local) / "ZhiyuShield" / "configs" / "development.yaml" if local else None
    if user_config and user_config.is_file():
        return user_config
    return PROJECT_ROOT / "configs" / "app.yaml"


CONFIG_PATH = _default_config_path()
_EDITABLE_KEYS = {
    "threshold": "threshold",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "model_checkpoint": "checkpoint",
    "sar_stats": "sar_stats",
    "output_directory": "outputs",
    "upload_directory": "uploads",
    "database_path": "database",
    "risk_new_attention": "new_attention",
    "risk_new_high": "new_high",
    "risk_persistent_attention": "persistent_attention",
    "risk_persistent_high": "persistent_high",
    "risk_net_expansion": "net_expansion",
    "risk_net_reduction": "net_reduction",
    "impact_high_attention_ratio": "high_attention_ratio",
    "impact_inspection_ratio": "inspection_ratio",
    "position_stale_hours": "position_stale_hours",
    "low_elevation_percentile": "low_elevation_percentile",
    "rain_event_gap_hours": "rain_event_gap_hours",
    "change_rainfall_pre_window_hours": "change_rainfall_pre_window_hours",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


def get_llm_config() -> LLMConfig:
    """优先读取页面保存的本机配置；不存在时回退到部署环境变量。"""
    local = load_local_llm_settings()
    if local:
        key = str(local.get("api_key", "")).strip()
        return LLMConfig(
            enabled=bool(local.get("enabled", False)),
            api_key=SecretStr(key) if key else None,
            base_url=str(local.get("base_url", "https://api.openai.com/v1")).strip(),
            model=str(local.get("model", "")).strip(),
            timeout=min(300.0, max(5.0, float(local.get("timeout", 60.0)))),
            temperature=min(2.0, max(0.0, float(local.get("temperature", 0.2)))),
            max_output_tokens=min(8192, max(128, int(local.get("max_output_tokens", 1600)))),
        )
    key = os.environ.get("LLM_API_KEY", "").strip()
    return LLMConfig(
        enabled=_env_bool("LLM_ENABLED"),
        api_key=SecretStr(key) if key else None,
        base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").strip(),
        model=os.environ.get("LLM_MODEL", "").strip(),
        timeout=_env_float("LLM_TIMEOUT", 60.0, 5.0, 300.0),
        temperature=_env_float("LLM_TEMPERATURE", 0.2, 0.0, 2.0),
        max_output_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", 1600, 128, 8192),
    )


def _llm_config_path() -> Path:
    return Path(os.environ.get("FLOODRISK_LLM_CONFIG_PATH", CONFIG_PATH.with_name("llm.local.json")))


def load_local_llm_settings() -> dict[str, Any]:
    path = _llm_config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("无法读取本机 AI 配置：%s", type(exc).__name__)
        return {}
    protected = raw.get("api_key_protected", "")
    if protected:
        try:
            raw["api_key"] = unprotect_secret(base64.b64decode(protected)).strip()
        except (ValueError, TypeError, SecretStoreError) as exc:
            # 保留本机配置及加密密文，不静默回退到另一把环境变量 Key。
            logger.warning("无法解密本机 AI Key：%s", type(exc).__name__)
    return raw


def save_local_llm_settings(
    *, enabled: bool, base_url: str, model: str, timeout: float,
    temperature: float, max_output_tokens: int, api_key: str | None = None,
    clear_api_key: bool = False,
) -> None:
    current = load_local_llm_settings()
    key = "" if clear_api_key else (api_key.strip() if api_key is not None else str(current.get("api_key", "")))
    payload: dict[str, Any] = {
        "enabled": enabled,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if key:
        payload["api_key_protected"] = base64.b64encode(protect_secret(key)).decode("ascii")
    elif not clear_api_key and current.get("api_key_protected"):
        payload["api_key_protected"] = current["api_key_protected"]
    path = _llm_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_editable_settings(**values: Any) -> None:
    """只写允许编辑字段到 app.yaml，保留文件其余内容与注释。"""
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    changed = []
    for key, raw in values.items():
        if key not in _EDITABLE_KEYS:
            raise ValueError(f"不允许修改配置字段：{key}")
        yaml_key = _EDITABLE_KEYS[key]
        pattern = re.compile(rf"^(\s*{re.escape(yaml_key)}\s*:\s*).*$")
        matched = False
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if m:
                value = json.dumps(raw, ensure_ascii=False) if isinstance(raw, str) else str(raw)
                lines[i] = f"{m.group(1)}{value}"
                matched = True
                break
        if not matched:
            raise ValueError(f"配置文件中未找到字段：{yaml_key}")
        changed.append(key)
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_config.cache_clear()
    return changed
