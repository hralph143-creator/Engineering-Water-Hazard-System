"""数据检查：统计 Sen1Floods11 v1.1 样本数量、缺失、S1/QC 统计与标签分布。

输出：终端摘要 + outputs/metrics/dataset_summary.csv（含逐样本统计与汇总行）。
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import rasterio
from tqdm import tqdm

from src.datasets.sen1floods11 import SPLIT_DIR, scan_scenes
from src.preprocessing.sar_preprocess import read_s1


def _band_stats(arr):
    arr = np.asarray(arr, dtype=np.float32)
    return {
        "min": float(arr.min()), "max": float(arr.max()),
        "mean": float(arr.mean()), "std": float(arr.std()),
        "nan": int(np.isnan(arr).sum()), "inf": int(np.isinf(arr).sum()),
    }


def _split_counts(root):
    sp = root / SPLIT_DIR
    counts = {}
    for name in ("flood_train_data.csv", "flood_valid_data.csv", "flood_test_data.csv"):
        n = 0
        f = sp / name
        if f.is_file():
            with open(f, encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2 and row[0].strip():
                        n += 1
        counts[name] = n
    return counts


def main():
    parser = argparse.ArgumentParser(description="Sen1Floods11 v1.1 数据检查")
    parser.add_argument("--root", default=r"E:\floodrisk\datasets\Sen1Floods11", help="数据根目录")
    parser.add_argument("--out", default=r"E:\floodrisk\outputs\metrics\dataset_summary.csv",
                        help="输出 CSV")
    parser.add_argument("--limit", type=int, default=None, help="仅检查前 N 个样本（快速检查）")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"数据目录不存在: {root.resolve()}，请先下载/放置 Sen1Floods11 v1.1 数据")
        sys.exit(1)

    samples = scan_scenes(root)
    if not samples:
        print(f"在 {root} 下未找到任何 Sen1Floods11 样本（v1.1/data/flood_events/HandLabeled/S1Hand）")
        sys.exit(1)

    rows = []
    missing_label = 0
    valid_count = 0
    for s in tqdm(samples, desc="检查样本"):
        missing_label += "label" in s["missing"]
        row = {
            "chip": s["chip"], "event": s["event"], "chip_id": s["chip_id"],
            "s1_exists": True, "label_exists": "label" not in s["missing"],
            "complete": not s["missing"],
        }
        if s["missing"]:
            rows.append(row)
            continue
        valid_count += 1
        vv, vh, _, _ = read_s1(s["s1"])
        with rasterio.open(s["label"]) as src:
            qc = src.read(1)

        row.update({"height": int(vv.shape[0]), "width": int(vv.shape[1]),
                    "dtype": str(vv.dtype)})
        for band, arr in (("vv", vv), ("vh", vh)):
            for k, v in _band_stats(arr).items():
                row[f"{band}_{k}"] = v
        vals, counts = np.unique(qc, return_counts=True)
        dist = dict(zip(vals.tolist(), counts.tolist()))
        row["label_neg1"] = dist.get(-1, 0)
        row["label_0"] = dist.get(0, 0)
        row["label_1"] = dist.get(1, 0)
        total_valid = row["label_0"] + row["label_1"]
        row["water_ratio"] = row["label_1"] / total_valid if total_valid else float("nan")
        row["invalid_ratio"] = row["label_neg1"] / qc.size
        if args.limit and len(rows) >= args.limit:
            break
        rows.append(row)

    df = pd.DataFrame(rows)
    summary = {
        "chip": "SUMMARY",
        "total_chips": len(samples),
        "label_count": len(samples) - missing_label,
        "valid_samples": valid_count,
        "missing_label": missing_label,
    }
    summary.update({f"split_{k}": v for k, v in _split_counts(root).items()})
    out = pd.concat([df, pd.DataFrame([summary])], ignore_index=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    try:
        import openpyxl  # noqa: F401
        out.to_excel(out_path.with_suffix(".xlsx"), index=False)
        xlsx_note = f" + {out_path.with_suffix('.xlsx')}"
    except ImportError:
        xlsx_note = "（未安装 openpyxl，跳过 xlsx）"

    print("\n========== 数据集摘要 ==========")
    print(f"样本总数        : {len(samples)}")
    print(f"有效样本数      : {valid_count}")
    print(f"缺失标签        : {missing_label}")
    print(f"官方 split      : train={summary.get('split_flood_train_data.csv')} "
          f"val={summary.get('split_flood_valid_data.csv')} "
          f"test={summary.get('split_flood_test_data.csv')}")
    if valid_count:
        img_h = df["height"].mode().iloc[0] if "height" in df else "?"
        img_w = df["width"].mode().iloc[0] if "width" in df else "?"
        print(f"图像尺寸(众数)  : {img_h} x {img_w}")
        print(f"洪水像素比例(均值): {df['water_ratio'].mean():.4f}")
        print(f"无效像素比例(均值): {df['invalid_ratio'].mean():.4f}")
    print(f"\nCSV 已导出: {out_path.resolve()}{xlsx_note}")


if __name__ == "__main__":
    main()
