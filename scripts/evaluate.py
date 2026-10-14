"""模型评价：在测试集上真实运行模型，计算并导出指标 CSV。"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import torch
import yaml
from torch.utils.data import DataLoader

from src.datasets.sen1floods11 import (Sen1Floods11Dataset, build_splits,
                                       load_split_samples)
from src.models.unet import UNet
from src.preprocessing.sar_preprocess import load_stats
from src.training.trainer import evaluate_samples
from src.utils.device import resolve_device
from src.utils.seed import set_seed


def main():
    parser = argparse.ArgumentParser(description="Sen1Floods11 模型评价")
    parser.add_argument("--config", default="configs/train.yaml")
    parser.add_argument("--checkpoint", default="checkpoints/best_model.pt")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--threshold", type=float, default=None)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    set_seed(cfg["seed"])
    device = resolve_device(cfg["training"].get("device", "auto"))
    threshold = args.threshold if args.threshold is not None else cfg["inference"]["threshold"]

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.is_file():
        print(f"checkpoint 不存在: {ckpt_path}，请先训练")
        sys.exit(1)

    root = Path(cfg["data"]["root"])
    processed_dir = Path(cfg["data"]["processed_dir"])
    build_splits(root, processed_dir, seed=cfg["seed"])
    samples = load_split_samples(root, cfg["data"]["split_dir"], args.split)
    if not samples:
        print(f"划分 {args.split} 下没有可用样本")
        sys.exit(1)

    stats_path = Path(cfg["data"]["stats_path"])
    if not stats_path.is_file():
        print(f"缺少 SAR 统计参数 {stats_path}，请先运行 scripts/train.py")
        sys.exit(1)
    stats = load_stats(stats_path)

    model = UNet(in_channels=cfg["model"]["in_channels"],
                 out_channels=cfg["model"]["out_channels"],
                 base_channels=cfg["model"]["base_channels"])
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(device)

    ds = Sen1Floods11Dataset(samples, image_size=cfg["data"]["image_size"],
                             stats=stats, augmentation=False)
    loader = DataLoader(ds, batch_size=cfg["training"]["batch_size"],
                        shuffle=False, num_workers=cfg["training"]["num_workers"],
                        pin_memory=True)

    use_amp = bool(cfg["training"].get("amp", True)) and device.type == "cuda"
    overall, per_sample = evaluate_samples(model, loader, device,
                                           threshold=threshold, use_amp=use_amp)

    print(f"\n========== {args.split} 集总体指标（threshold={threshold}）==========")
    for k in ("precision", "recall", "f1", "iou", "dice", "accuracy"):
        print(f"{k:>10s}: {overall[k]:.4f}")

    df = pd.DataFrame(per_sample)
    overall_row = {"chip": "OVERALL", **{k: v for k, v in overall.items()}}
    df = pd.concat([df, pd.DataFrame([overall_row])], ignore_index=True)
    out_path = Path(cfg["outputs"]["metrics_dir"]) / f"{args.split}_metrics.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    if args.split == "test":
        per_csv = Path(cfg["outputs"]["metrics_dir"]) / "test_metrics_per_sample.csv"
        df[df["chip"] != "OVERALL"].to_csv(per_csv, index=False, encoding="utf-8-sig")
        print(f"单样本指标已导出: {per_csv}")
    print(f"\n指标已导出: {out_path}（含逐样本与 OVERALL 汇总行）")


if __name__ == "__main__":
    main()
