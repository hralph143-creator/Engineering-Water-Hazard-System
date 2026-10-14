"""Sentinel-1 SAR 预处理（Sen1Floods11 v1.1）。

官方 v1.1 的 S1 为 2 波段 GeoTIFF：band0=VV、band1=VH（单位 dB，Float32），
无效区域以 NaN 标记（COG nodata）。本模块：
  1) 读取 2 波段 S1（NaN/Inf 置 0，避免污染统计）；
  2) 在训练集上统计分位数裁剪范围与归一化 min/max；
  3) 训练 / 验证 / 测试 / 推理统一使用同一份 sar_stats.json。
不做 /255、不套 ImageNet mean/std——SAR 是 dB 值，按真实分布处理。
"""
import json
from pathlib import Path

import numpy as np


def read_s1(path):
    """读取官方 2 波段 S1 GeoTIFF，返回 (vv, vh, crs, transform)。

    vv/vh 为 (H, W) float32，NaN/Inf 已置 0；crs/transform 供空间一致性检查。
    """
    import rasterio

    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)  # (2, H, W)
        crs, transform = src.crs, src.transform
    if arr.ndim != 3 or arr.shape[0] < 2:
        raise ValueError(f"S1 文件波段数异常（期望 2，实际 {0 if arr.ndim != 3 else arr.shape[0]}）: {path}")
    vv = np.nan_to_num(arr[0], nan=0.0, posinf=0.0, neginf=0.0)
    vh = np.nan_to_num(arr[1], nan=0.0, posinf=0.0, neginf=0.0)
    return vv, vh, crs, transform


def read_band_flat(path, band, max_pixels=20000, seed=42):
    """读取第 band 波段并降采样为扁平数组（统计用，控制内存）。"""
    import rasterio

    with rasterio.open(path) as src:
        arr = src.read(band + 1).astype(np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    flat = arr.ravel()
    if flat.size > max_pixels:
        rng = np.random.RandomState(seed)
        flat = rng.choice(flat, size=max_pixels, replace=False)
    return flat


def compute_sar_stats(s1_paths, clip_percentiles=(2.0, 98.0),
                      max_pixels_per_band=20000, seed=42):
    """从训练集 S1 文件统计 VV/VH 的裁剪与归一化参数。

    返回 {"vv": {"clip_percentiles", "clip_min", "clip_max", "norm_min", "norm_max"},
          "vh": {...}}
    """
    vv = np.concatenate([read_band_flat(p, 0, max_pixels_per_band, seed) for p in s1_paths])
    vh = np.concatenate([read_band_flat(p, 1, max_pixels_per_band, seed) for p in s1_paths])
    low, high = clip_percentiles
    stats = {}
    for name, arr in (("vv", vv), ("vh", vh)):
        p_low, p_high = np.percentile(arr, [low, high])
        clipped = np.clip(arr, p_low, p_high)
        stats[name] = {
            "clip_percentiles": [low, high],
            "clip_min": float(p_low),
            "clip_max": float(p_high),
            "norm_min": float(clipped.min()),
            "norm_max": float(clipped.max()),
        }
    return stats


def apply_preprocess(vv, vh, stats, eps=1e-6):
    """按统计参数裁剪并 min-max 归一化，返回 (2, H, W) float32。"""
    h, w = vv.shape
    out = np.empty((2, h, w), dtype=np.float32)
    for i, (band, name) in enumerate(((vv, "vv"), (vh, "vh"))):
        s = stats[name]
        clipped = np.clip(band, s["clip_min"], s["clip_max"])
        span = s["norm_max"] - s["norm_min"]
        out[i] = (clipped - s["norm_min"]) / (span + eps)
    return out


def save_stats(stats, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def load_stats(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
