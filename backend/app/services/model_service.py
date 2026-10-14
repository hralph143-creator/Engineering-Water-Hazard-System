import json
import sys
import threading
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
import rasterio
import torch

from ..config import PROJECT_ROOT, AppConfig

sys.path.insert(0, str(PROJECT_ROOT))
from src.inference.predictor import FloodPredictor  # noqa: E402
from src.models.unet import UNet  # noqa: E402
from src.preprocessing.sar_preprocess import load_stats, read_s1  # noqa: E402
from src.utils.visualization import save_prediction_figure  # noqa: E402


class ModelUnavailableError(RuntimeError):
    pass


def compute_probability_statistics(
    prob: np.ndarray, mask: np.ndarray, valid: np.ndarray, threshold: float
) -> dict:
    """概率统计（仅对有效像素计算，NaN/Inf 不进入统计）。

    高置信洪水阈值 = max(任务阈值, 0.8)；比例分母 = 全部预测洪水像素。
    该函数是概率统计的唯一实现，推理与单测共用，保证前后端口径一致。
    """
    prob_valid = prob[valid]
    if prob_valid.size:
        stats = {
            "min": float(prob_valid.min()),
            "max": float(prob_valid.max()),
            "mean": float(prob_valid.mean()),
            "median": float(np.median(prob_valid)),
        }
    else:
        stats = {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
    flood_count = int(np.logical_and(mask == 1, valid).sum())
    flood_probs = prob[np.logical_and(mask == 1, valid)]
    mean_confidence = float(flood_probs.mean()) if flood_probs.size else 0.0
    high_threshold = max(threshold, 0.8)
    high_conf_count = int(
        np.logical_and(np.logical_and(mask == 1, valid), prob >= high_threshold).sum()
    )
    high_conf_ratio = high_conf_count / flood_count if flood_count else 0.0
    return {
        **stats,
        "flood_count": flood_count,
        "mean_confidence": mean_confidence,
        "high_confidence_flood_pixel_count": high_conf_count,
        "high_confidence_flood_ratio": high_conf_ratio,
    }


class ModelService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.model = None
        self.predictor = None
        self.device = None
        self.error: str | None = None
        self.load_count = 0
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.predictor is not None and self.error is None

    def load(self) -> None:
        if self.loaded:
            return
        checkpoint = self.config.model.checkpoint
        stats_path = self.config.model.sar_stats
        try:
            if not checkpoint.is_file():
                raise FileNotFoundError(f"模型文件不存在：{checkpoint}")
            if not stats_path.is_file():
                raise FileNotFoundError(f"SAR stats 缺失：{stats_path}")
            requested = self.config.model.device.lower()
            if requested == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("配置要求 CUDA，但当前 CUDA 不可用")
            self.device = torch.device(
                "cuda" if requested == "auto" and torch.cuda.is_available() else
                "cpu" if requested == "auto" else requested
            )
            model = UNet(in_channels=2, out_channels=1, base_channels=64)
            stats = load_stats(stats_path)
            self.predictor = FloodPredictor(
                model, checkpoint, stats, self.device, self.config.model.threshold
            )
            self.model = model
            self.error = None
            self.load_count += 1
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.model = self.predictor = None
            raise ModelUnavailableError(self.error) from exc

    def metrics(self) -> dict:
        path = self.config.model.experiment_summary
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("test", {})
        except (OSError, ValueError):
            return {}

    def status(self) -> dict:
        m = self.metrics()
        return {
            "loaded": self.loaded,
            "error": self.error,
            "model_name": self.config.model.name,
            "model_version": self.config.model.version,
            "input_channels": 2,
            "checkpoint": self.config.model.checkpoint.name,
            "checkpoint_path": str(self.config.model.checkpoint),
            "device": str(self.device) if self.device else None,
            "threshold": self.config.model.threshold,
            "load_count": self.load_count,
            "test_precision": m.get("precision"),
            "test_recall": m.get("recall"),
            "test_f1": m.get("f1"),
            "test_iou": m.get("iou"),
            "test_dice": m.get("dice"),
            "test_accuracy": m.get("accuracy"),
        }

    @staticmethod
    def _write_tif(arr, template_path: Path, out_path: Path, dtype: str) -> None:
        with rasterio.open(template_path) as src:
            profile = src.profile.copy()
            profile.update(count=1, dtype=dtype, compress="deflate", nodata=None)
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(arr.astype(dtype), 1)

    @staticmethod
    def _save_layer(arr, path: Path, cmap, vmin=None, vmax=None, dpi: int = 240) -> None:
        """保存单图层 PNG：仅呈现数据，无标题、无刻度、无边框、无白边、统一尺寸、高清。"""
        fig, ax = plt.subplots(figsize=(6.4, 6.4))
        ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.tight_layout(pad=0.0)
        fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white", pad_inches=0.0)
        plt.close(fig)

    def infer(self, image_path: Path, output_dir: Path, threshold: float) -> dict:
        if not self.loaded:
            raise ModelUnavailableError(self.error or "模型尚未加载")
        output_dir.mkdir(parents=True, exist_ok=False)
        started = time.perf_counter()
        with rasterio.open(image_path) as source:
            raw_vv = source.read(1).astype(np.float32)
            raw_vh = source.read(2).astype(np.float32)
        valid = np.isfinite(raw_vv) & np.isfinite(raw_vh)
        if not valid.any():
            raise ValueError("SAR 影像至少一个波段不包含有效数值")
        vv, vh, _, _ = read_s1(image_path)
        with self._lock:
            original_threshold = self.predictor.threshold
            self.predictor.threshold = threshold
            try:
                prob, mask = self.predictor.predict(vv, vh)
            finally:
                self.predictor.threshold = original_threshold
        probability_path = output_dir / "probability.tif"
        mask_path = output_dir / "mask.tif"
        comparison_path = output_dir / "comparison.png"
        self._write_tif(prob, image_path, probability_path, "float32")
        self._write_tif(mask, image_path, mask_path, "uint8")
        save_prediction_figure(vv, vh, prob, mask, scene=image_path.stem, out_path=comparison_path)
        self._save_layer(vv, output_dir / "vv.png", "gray")
        self._save_layer(vh, output_dir / "vh.png", "gray")
        self._save_layer(prob, output_dir / "probability.png", "Blues", vmin=0.0, vmax=1.0)
        self._save_layer(mask, output_dir / "mask.png", ListedColormap(["#EEF2F5", "#1E6E9C"]), vmin=0, vmax=1)
        valid_count = int(valid.sum())
        prob_stats = compute_probability_statistics(prob, mask, valid, threshold)
        flood_count = prob_stats["flood_count"]
        flood_ratio = flood_count / valid_count if valid_count else 0.0
        mean_confidence = prob_stats["mean_confidence"]
        probability_min = prob_stats["min"]
        probability_max = prob_stats["max"]
        probability_mean = prob_stats["mean"]
        probability_median = prob_stats["median"]
        high_conf_flood_count = prob_stats["high_confidence_flood_pixel_count"]
        high_conf_flood_ratio = prob_stats["high_confidence_flood_ratio"]
        return {
            "probability_path": str(probability_path),
            "mask_path": str(mask_path),
            "comparison_path": str(comparison_path),
            "flood_pixel_count": flood_count,
            "valid_pixel_count": valid_count,
            "flood_ratio": flood_ratio,
            "mean_confidence": mean_confidence,
            "inference_seconds": time.perf_counter() - started,
            "probability_min": probability_min,
            "probability_max": probability_max,
            "probability_mean": probability_mean,
            "probability_median": probability_median,
            "high_confidence_flood_pixel_count": high_conf_flood_count,
            "high_confidence_flood_ratio": high_conf_flood_ratio,
        }


model_service: ModelService | None = None


def init_model_service(config: AppConfig) -> ModelService:
    global model_service
    model_service = ModelService(config)
    model_service.load()
    return model_service


def get_model_service() -> ModelService:
    if model_service is None:
        raise ModelUnavailableError("模型服务尚未初始化")
    return model_service
