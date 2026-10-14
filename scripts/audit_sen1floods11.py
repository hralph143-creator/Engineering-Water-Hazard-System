"""真实 Sen1Floods11 v1.1 目录与样本审查。

递归检查数据根目录，随机抽取样本用 rasterio 检查 S1 / QC：
shape / count / dtype / crs / transform / nodata / bounds / min / max / mean / NaN / Inf，
以及 QC 标签唯一值、-1/0/1 数量、水体比例、无效像素比例。
"""
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio

RNG = random.Random(42)


def _print_tree(root: Path, max_depth: int = 3):
    print("\n========== 目录树（前 %d 层） ==========" % max_depth)
    for p in sorted(root.rglob("*"))[:400]:
        rel = p.relative_to(root)
        depth = len(rel.parts)
        if depth > max_depth:
            continue
        indent = "  " * (depth - 1)
        if p.is_dir():
            n = sum(1 for _ in p.rglob("*.tif"))
            print(f"{indent}{p.name}/  ({n} tif)")
        else:
            print(f"{indent}{p.name}")


def _dir_stats(root: Path):
    print("\n========== 一级目录统计 ==========")
    for sub in sorted(root.iterdir()):
        if sub.is_dir():
            tifs = list(sub.rglob("*.tif"))
            exts = Counter(x.suffix for x in sub.rglob("*") if x.is_file())
            print(f"{sub.name}/  tif={len(tifs)}  扩展名={dict(exts)}")


def _split_stats(root: Path):
    print("\n========== 官方 split ==========")
    split_dir = root / "v1.1" / "splits" / "flood_handlabeled"
    for csv_name in ["flood_train_data.csv", "flood_valid_data.csv", "flood_test_data.csv"]:
        f = split_dir / csv_name
        if not f.is_file():
            print(f"{csv_name}: 缺失")
            continue
        lines = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"{csv_name}: {len(lines)} 行")
        if lines:
            print(f"  示例: {lines[0]}")


def _inspect_pair(s1_path: Path, qc_path: Path):
    print("\n" + "=" * 70)
    print(f"样本: {s1_path.name}  <=>  {qc_path.name}")
    with rasterio.open(s1_path) as src:
        arr = src.read()
        print(f"  S1 : shape={arr.shape} count={src.count} dtype={src.dtypes} "
              f"crs={src.crs} nodata={src.nodata}")
        t = src.transform
        print(f"       transform~[{t.a:.3f},{t.b},{t.c:.1f};{t.d},{t.e:.3f},{t.f:.1f}] "
              f"bounds={tuple(round(v, 1) for v in src.bounds)}")
        for b in range(arr.shape[0]):
            band = arr[b]
            valid = band[np.isfinite(band)]
            print(f"       band{b}: min={np.nanmin(band):.3f} max={np.nanmax(band):.3f} "
                  f"mean={np.nanmean(band):.3f} NaN={int(np.isnan(band).sum())} "
                  f"Inf={int(np.isinf(band).sum())}")
    with rasterio.open(qc_path) as src:
        lab = src.read(1)
        print(f"  QC : shape={lab.shape} dtype={lab.dtype} crs={src.crs} nodata={src.nodata}")
        vals, counts = np.unique(lab, return_counts=True)
        dist = dict(zip(vals.tolist(), counts.tolist()))
        n_neg1 = dist.get(-1, 0)
        n0 = dist.get(0, 0)
        n1 = dist.get(1, 0)
        total_valid = n0 + n1
        print(f"       唯一值={vals.tolist()}  分布={dist}")
        print(f"       -1(无效)={n_neg1}  0(无水)={n0}  1(水)={n1}")
        if total_valid:
            print(f"       水体比例(有效像素内)={n1 / total_valid:.4f}  无效比例={n_neg1 / lab.size:.4f}")


def main():
    parser = argparse.ArgumentParser(description="Sen1Floods11 v1.1 真实数据审查")
    parser.add_argument("--root", default=r"E:\floodrisk\datasets\Sen1Floods11")
    parser.add_argument("--n", type=int, default=8, help="抽样样本数")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"数据目录不存在: {root}")
        sys.exit(1)

    _print_tree(root)
    _dir_stats(root)
    _split_stats(root)

    s1_dir = root / "v1.1" / "data" / "flood_events" / "HandLabeled" / "S1Hand"
    qc_dir = root / "v1.1" / "data" / "flood_events" / "HandLabeled" / "LabelHand"
    s1_files = sorted(s1_dir.glob("*_S1Hand.tif"))
    if not s1_files:
        print(f"\nS1Hand 目录无文件: {s1_dir}")
        sys.exit(1)

    print(f"\n========== 抽样检查 {min(args.n, len(s1_files))} 个真实样本 ==========")
    for s1 in RNG.sample(s1_files, min(args.n, len(s1_files))):
        chip = s1.name.replace("_S1Hand.tif", "")
        qc = qc_dir / f"{chip}_LabelHand.tif"
        if not qc.is_file():
            print(f"样本 {chip}: 缺少 QC 文件 {qc.name}")
            continue
        _inspect_pair(s1, qc)


if __name__ == "__main__":
    main()
