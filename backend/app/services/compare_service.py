"""结果对比与像素级洪水变化分析服务。

空间一致性规则（严格）：
- 仅当两景 mask 的 CRS / transform / bounds / width / height 完全一致时，
  才允许逐像素变化分析；否则只做统计对比。
- 禁止把不同地区影像 resize 到同一尺寸强行比较。

变化类别（基于二值 mask，A=前一景 B=后一景）：
  新增洪水 A=0 B=1 | 消退洪水 A=1 B=0 | 持续洪水 A=1 B=1 | 持续非洪水 A=0 B=0
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio


class SpatialMismatchError(ValueError):
    pass


def _meta_of(src) -> dict:
    return {
        "crs": src.crs.to_string() if src.crs else None,
        "transform": list(src.transform),
        "bounds": list(src.bounds),
        "width": src.width,
        "height": src.height,
        "nodata": src.nodata,
    }


def read_mask_meta(mask_path: Path) -> dict:
    with rasterio.open(mask_path) as src:
        return _meta_of(src)


def spatial_consistent(result_a, result_b) -> bool:
    """两景空间元数据完全一致才允许像素级比较。"""
    return read_mask_meta(Path(result_a.mask_path)) == read_mask_meta(Path(result_b.mask_path))


def change_statistics(result_a, result_b) -> dict:
    """只读取两景二值掩膜并返回变化统计，不写文件、不修改原始结果。"""
    with rasterio.open(Path(result_a.mask_path)) as src_a, rasterio.open(Path(result_b.mask_path)) as src_b:
        if _meta_of(src_a) != _meta_of(src_b):
            raise SpatialMismatchError("两景数据空间范围或栅格网格不一致")
        a = src_a.read(1).astype(np.uint8)
        b = src_b.read(1).astype(np.uint8)
    valid = (a <= 1) & (b <= 1)
    counts = {
        "new_flood_pixels": int((valid & (a == 0) & (b == 1)).sum()),
        "recession_pixels": int((valid & (a == 1) & (b == 0)).sum()),
        "persistent_pixels": int((valid & (a == 1) & (b == 1)).sum()),
        "non_flood_pixels": int((valid & (a == 0) & (b == 0)).sum()),
    }
    total_valid = int(valid.sum())
    return {
        "counts": counts,
        "ratios": {
            key.replace("_pixels", "_ratio"): (value / total_valid if total_valid else 0.0)
            for key, value in counts.items()
        },
        "valid_pixels": total_valid,
    }


def change_analysis(
    result_a, result_b, out_dir: Path, *,
    tif_name: str = "change_mask.tif", png_name: str = "change_mask.png",
) -> dict:
    """两景空间一致时的像素级变化分析，返回分类统计并写出 change_mask.tif/png。"""
    with rasterio.open(Path(result_a.mask_path)) as src_a, rasterio.open(Path(result_b.mask_path)) as src_b:
        meta_a = _meta_of(src_a)
        meta_b = _meta_of(src_b)
        if meta_a != meta_b:
            raise SpatialMismatchError(
                "两景数据空间范围或栅格网格不一致，不支持像素级洪水变化分析"
            )
        a = src_a.read(1).astype(np.uint8)
        b = src_b.read(1).astype(np.uint8)
        profile = src_a.profile.copy()
        dst_crs = src_a.crs
        dst_transform = src_a.transform

    # 仅当两景均为有效二值像素（0/1）时参与分类
    valid = ((a <= 1) & (b <= 1))
    new_flood = valid & (a == 0) & (b == 1)      # 新增洪水
    recession = valid & (a == 1) & (b == 0)      # 消退洪水
    persistent = valid & (a == 1) & (b == 1)     # 持续洪水
    non_flood = valid & (a == 0) & (b == 0)      # 持续非洪水

    change = np.zeros(a.shape, dtype=np.uint8)
    change[new_flood] = 1
    change[recession] = 2
    change[persistent] = 3
    # 无效像素置 4
    change[~valid] = 4

    total_valid = int(valid.sum())
    counts = {
        "new_flood_pixels": int(new_flood.sum()),
        "recession_pixels": int(recession.sum()),
        "persistent_pixels": int(persistent.sum()),
        "non_flood_pixels": int(non_flood.sum()),
    }
    ratios = {
        k.replace("_pixels", "_ratio"): (v / total_valid if total_valid else 0.0)
        for k, v in counts.items()
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    change_path = out_dir / tif_name
    png_path = out_dir / png_name
    profile.update(count=1, dtype="uint8", compress="deflate", nodata=None)
    with rasterio.open(change_path, "w", **profile) as dst:
        dst.write(change, 1)
    _write_change_png(change, png_path)

    return {
        "counts": counts,
        "ratios": ratios,
        "valid_pixels": total_valid,
        "change_mask_tif": str(change_path),
        "change_mask_png": str(png_path),
        "color_note": "新增积水：红/橙；消退积水：绿；持续积水：蓝",
    }


def _write_change_png(change: np.ndarray, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    cmap = ListedColormap(
        ["#eef2f3", "#e64b3c", "#35a66f", "#3478d4", "#d7dde0"]
    )
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(change, cmap=cmap, vmin=0, vmax=4)
    ax.set_title("积水变化分析（新增 / 消退 / 持续）")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
