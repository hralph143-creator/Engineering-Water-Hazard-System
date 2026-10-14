"""样本可视化：随机抽取 N 个真实样本，输出 VV / VH / Flood Label / VV+VH 合成 4 联图。"""
import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rasterio
import yaml

from src.datasets.sen1floods11 import load_split_samples
from src.preprocessing.sar_preprocess import read_s1
from src.utils.visualization import save_sample_figure


def main():
    parser = argparse.ArgumentParser(description="Sen1Floods11 样本可视化")
    parser.add_argument("--config", default="configs/train.yaml")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--num", type=int, default=6, help="抽取样本数")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="outputs/figures", help="输出目录")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    root = Path(cfg["data"]["root"])
    split_dir = Path(cfg["data"]["split_dir"])

    samples = load_split_samples(root, split_dir, args.split)
    if not samples:
        print(f"划分 {args.split} 下没有可用样本")
        sys.exit(1)
    rng = random.Random(args.seed)
    picked = rng.sample(samples, min(args.num, len(samples)))

    out_dir = Path(args.out) / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in picked:
        vv, vh, _, _ = read_s1(s["s1"])
        with rasterio.open(s["label"]) as src:
            qc = src.read(1)
        label_bin = (qc == 1).astype("float32")
        out_path = out_dir / f"{args.split}_{s['chip']}.png"
        save_sample_figure(vv, vh, label_bin, f"[{args.split}] {s['chip']}", out_path)
        print(f"已保存: {out_path}")


if __name__ == "__main__":
    main()
