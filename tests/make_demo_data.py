"""生成 v1.1 格式 demo 数据（仅用于测试代码结构，不作为正式训练数据）。

在指定目录（默认 data/demo/）生成极小的 Sen1Floods11 v1.1 风格结构：
  v1.1/data/flood_events/HandLabeled/S1Hand/{chip}_S1Hand.tif        # 2 band Float32 (VV/VH)
  v1.1/data/flood_events/HandLabeled/LabelHand/{chip}_LabelHand.tif  # 1 band Int16 (-1/0/1)
  v1.1/splits/flood_handlabeled/flood_{train,valid,test}_data.csv
与真实数据（E 盘）完全隔离。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio

DEMO_ROOT = ROOT / "data" / "demo"
CHIPS = ["Bolivia_103757", "India_150316", "Nigeria_223164", "Pakistan_206056",
         "Colombia_127034", "Paraguay_161048", "Spain_7370579", "Mozambique_238931",
         "Ecuador_121392", "Guyana_119821"]
HAND_DIR = Path("v1.1") / "data" / "flood_events" / "HandLabeled"
SPLIT_DIR = Path("v1.1") / "splits" / "flood_handlabeled"


def _write_tif(arr, path, dtype):
    path.parent.mkdir(parents=True, exist_ok=True)
    count = arr.shape[0] if arr.ndim == 3 else 1
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[-2], width=arr.shape[-1],
        count=count, dtype=dtype, crs="EPSG:4326",
        transform=rasterio.Affine(10, 0, -65.0, 0, -10, -14.0),
    ) as dst:
        if arr.ndim == 3:
            dst.write(arr, [1, 2])
        else:
            dst.write(arr, 1)


def _random_s1(size, rng):
    """模拟 2 波段 S1（dB）：背景噪声 + 强散射斑块（含少量 NaN）。"""
    s1 = np.stack([rng.normal(-10, 3, size).astype(np.float32),
                   rng.normal(-17, 3, size).astype(np.float32)], axis=0)
    cy, cx = rng.randint(10, size[0] - 10), rng.randint(10, size[1] - 10)
    s1[0, cy - 3:cy + 3, cx - 3:cx + 3] += 3.0
    if rng.random() < 0.3:
        s1[0, 0, 0] = np.nan
    return s1


def _random_qc(size, rng, with_invalid):
    """0=无水, 1=洪水；部分 chip 含 -1（无效）。"""
    lab = np.zeros(size, dtype=np.int16)
    cy, cx = rng.randint(12, size[0] - 12), rng.randint(12, size[1] - 12)
    lab[cy - 6:cy + 6, cx - 6:cx + 6] = 1
    if with_invalid:
        lab[cy - 2:cy + 2, cx - 2:cx + 2] = -1
    return lab


def generate_demo(root: Path, seed: int = 42) -> None:
    rng = np.random.RandomState(seed)
    size = (64, 64)
    rows = {k: [] for k in ("train", "valid", "test")}
    for i, chip in enumerate(CHIPS):
        _write_tif(_random_s1(size, rng),
                   Path(root) / HAND_DIR / "S1Hand" / f"{chip}_S1Hand.tif", "float32")
        _write_tif(_random_qc(size, rng, with_invalid=(i % 3 == 0)),
                   Path(root) / HAND_DIR / "LabelHand" / f"{chip}_LabelHand.tif", "int16")
        split = ["train", "valid", "test"][i % 3]
        rows[split].append(f"{chip}_S1Hand.tif,{chip}_LabelHand.tif")
    for split in ("train", "valid", "test"):
        p = Path(root) / SPLIT_DIR / f"flood_{split}_data.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(rows[split]) + "\n", encoding="utf-8")
    (Path(root) / "README.txt").write_text(
        "DEMO 数据：随机张量，仅用于测试代码结构，严禁作为正式训练数据。\n", encoding="utf-8")
    print(f"demo 数据已生成: {root}（{len(CHIPS)} 个 chip）")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="生成 v1.1 格式 demo 数据（仅供结构测试）")
    parser.add_argument("--out", default=str(DEMO_ROOT), help="demo 数据输出目录")
    args = parser.parse_args()
    generate_demo(Path(args.out))


if __name__ == "__main__":
    main()
