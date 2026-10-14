"""模型推理：对 Sentinel-1 GeoTIFF 输出洪水概率图、二值 Mask、叠加图与对比图。

输入两种方式（二选一）：
  --s1 2波段 S1 GeoTIFF（官方 v1.1 格式，band0=VV band1=VH，推荐）
  --vv + --vh 两个单波段文件（兼容旧数据）
有 QC 标签时（--label）额外输出 Ground Truth / Error Map 对比图。
输出 GeoTIFF 保留源影像的地理参考（transform/crs）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
import yaml

from src.inference.predictor import FloodPredictor
from src.models.unet import UNet
from src.preprocessing.sar_preprocess import load_stats, read_s1
from src.utils.device import resolve_device
from src.utils.seed import set_seed
from src.utils.visualization import (save_overlay_figure, save_prediction_figure)


def _read_single_band(path):
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def _write_tif(arr, template_path, out_path, dtype="float32"):
    """按模板影像的 transform/crs 写出单波段 GeoTIFF。"""
    with rasterio.open(template_path) as src:
        profile = src.profile
    profile.update(dtype=dtype, count=1, compress="lzw")
    if dtype != "float32":
        profile.update(nodata=None)  # 整型输出不能用 NaN 作 nodata
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr.astype(dtype), 1)


def main():
    parser = argparse.ArgumentParser(description="Sen1Floods11 U-Net 推理")
    parser.add_argument("--s1", default=None, help="2 波段 S1 GeoTIFF（band0=VV, band1=VH）")
    parser.add_argument("--vv", default=None, help="单波段 VV GeoTIFF（与 --vh 一起使用）")
    parser.add_argument("--vh", default=None, help="单波段 VH GeoTIFF")
    parser.add_argument("--label", default=None, help="可选：QC 标签 GeoTIFF（-1/0/1）")
    parser.add_argument("--checkpoint", default="checkpoints/best_model.pt")
    parser.add_argument("--config", default="configs/train.yaml")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--out", default=None, help="输出目录（默认 outputs/predictions）")
    parser.add_argument("--name", default=None, help="输出文件前缀（默认取 S1 文件名）")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    set_seed(cfg["seed"])
    device = resolve_device(cfg["training"].get("device", "auto"))
    threshold = args.threshold if args.threshold is not None else cfg["inference"]["threshold"]

    stats_path = Path(cfg["data"]["stats_path"])
    if not stats_path.is_file():
        print(f"缺少 SAR 统计参数 {stats_path}，请先运行 scripts/train.py")
        sys.exit(1)
    stats = load_stats(stats_path)

    # 输入解析
    if args.s1:
        vv, vh, _, _ = read_s1(args.s1)
        template = args.s1
        default_name = Path(args.s1).stem.replace("_S1Hand", "")
    elif args.vv and args.vh:
        vv = _read_single_band(args.vv)
        vh = _read_single_band(args.vh)
        template = args.vv
        default_name = Path(args.vv).stem.replace("_vv", "")
    else:
        print("必须提供 --s1（2 波段）或 --vv + --vh")
        sys.exit(1)
    if vv.shape != vh.shape:
        print(f"VV/VH 尺寸不一致: {vv.shape} vs {vh.shape}")
        sys.exit(1)

    model = UNet(in_channels=cfg["model"]["in_channels"],
                 out_channels=cfg["model"]["out_channels"],
                 base_channels=cfg["model"]["base_channels"])
    predictor = FloodPredictor(model, args.checkpoint, stats, device, threshold=threshold)
    prob, mask = predictor.predict(vv, vh)

    name = args.name or default_name
    out_dir = Path(args.out or cfg["inference"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_tif(prob, template, out_dir / f"{name}_prob.tif", dtype="float32")
    _write_tif(mask, template, out_dir / f"{name}_mask.tif", dtype="uint8")
    save_overlay_figure(vv, prob, mask, out_dir / f"{name}_overlay.png")
    print(f"洪水概率图 : {out_dir / (name + '_prob.tif')}")
    print(f"二值洪水Mask: {out_dir / (name + '_mask.tif')}")
    print(f"叠加图     : {out_dir / (name + '_overlay.png')}")

    if args.label:
        with rasterio.open(args.label) as src:
            lab = src.read(1)
        if lab.shape != mask.shape:
            print(f"标签尺寸与影像不一致: {lab.shape} vs {mask.shape}，跳过对比图")
        else:
            gt = (lab == 1).astype(np.uint8)
            fig_path = out_dir / f"{name}_comparison.png"
            save_prediction_figure(vv, vh, prob, mask, gt=gt, scene=name,
                                   out_path=fig_path)
            print(f"对比图     : {fig_path}")
    else:
        fig_path = out_dir / f"{name}_prediction.png"
        save_prediction_figure(vv, vh, prob, mask, gt=None, scene=name,
                               out_path=fig_path)
        print(f"预测图     : {fig_path}")


if __name__ == "__main__":
    main()
