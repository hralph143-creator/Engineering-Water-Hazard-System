"""Sen1Floods11 v1.1 数据集读取与划分。

官方 v1.1 bucket 结构（cloudtostreet/Sen1Floods11）：
  v1.1/data/flood_events/HandLabeled/
    S1Hand/{EVENT_CHIPID_S1Hand.tif}        # 2 band Float32, band0=VV band1=VH
    LabelHand/{EVENT_CHIPID_LabelHand.tif}  # 1 band Int16, -1=无效 0=无水 1=水
  v1.1/splits/flood_handlabeled/flood_{train,valid,test}_data.csv
      # 每行: {chip}_S1Hand.tif,{chip}_LabelHand.tif

配对：按 CHIP ID（EVENT_CHIPID）在 S1Hand 与 LabelHand 目录间配对，
不靠文件名 replace 猜测。标签 -1 为无效像素，返回独立 valid mask，
损失/指标仅在有效像素上计算。
"""
import csv
import random
from pathlib import Path

import numpy as np
import torch
import torchvision.transforms.functional as TF
from torch.utils.data import Dataset

from ..preprocessing.sar_preprocess import apply_preprocess, read_s1

HAND_DIR = Path("v1.1") / "data" / "flood_events" / "HandLabeled"
SPLIT_DIR = Path("v1.1") / "splits" / "flood_handlabeled"
OFFICIAL_SPLIT_FILES = {
    "train": "flood_train_data.csv",
    "val": "flood_valid_data.csv",
    "test": "flood_test_data.csv",
}


def scan_scenes(root):
    """扫描官方 v1.1 HandLabeled 的 S1Hand + LabelHand，按 chip 配对。"""
    root = Path(root)
    s1_dir = root / HAND_DIR / "S1Hand"
    qc_dir = root / HAND_DIR / "LabelHand"
    if not s1_dir.is_dir():
        raise FileNotFoundError(
            f"S1Hand 目录不存在: {s1_dir}（请按官方 v1.1 结构放置数据，见 data/README.md）")
    samples = []
    for s1 in sorted(s1_dir.glob("*_S1Hand.tif")):
        chip = s1.name[: -len("_S1Hand.tif")]
        qc = qc_dir / f"{chip}_LabelHand.tif"
        event = chip.rsplit("_", 1)[0] if "_" in chip else chip
        chip_id = chip.rsplit("_", 1)[1] if "_" in chip else chip
        samples.append({
            "chip": chip, "event": event, "chip_id": chip_id,
            "s1": str(s1), "label": str(qc) if qc.is_file() else None,
            "missing": [] if qc.is_file() else ["label"],
        })
    return samples


def _read_official_splits(root: Path) -> dict:
    result = {}
    sp = root / SPLIT_DIR
    for split_name, fname in OFFICIAL_SPLIT_FILES.items():
        f = sp / fname
        if not f.is_file():
            continue
        chips = set()
        with open(f, encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0].strip():
                    chips.add(row[0].strip().replace("_S1Hand.tif", ""))
        result[split_name] = chips
    return result


def build_splits(root, processed_dir, seed: int = 42, val_ratio: float = 0.15,
                 test_ratio: float = 0.15, force: bool = False) -> dict:
    """生成 train / val / test 划分并保存清单（chip id 列表）。

    优先使用官方 v1.1 CSV split；官方未覆盖的 chip 按事件随机补充（seed=42），
    避免同一事件样本进入多个集合造成数据泄漏。
    """
    root = Path(root)
    split_dir = Path(processed_dir) / "splits"
    if split_dir.joinpath("train.txt").is_file() and not force:
        return {s: _read_split(split_dir / f"{s}.txt") for s in ("train", "val", "test")}

    chips = sorted({s["chip"] for s in scan_scenes(root)})
    assignment = {}
    official = _read_official_splits(root)
    if official:
        # 官方 v1.1 split：直接采用；官方未覆盖的 chip 不参与，保持官方划分、防泄漏
        for split_name, ids in official.items():
            for chip in ids:
                if chip in chips:
                    assignment[chip] = split_name
    else:
        # 无官方 split 时按事件随机划分（seed 固定，避免同事件进入多集合）
        unassigned = [c for c in chips if c not in assignment]
        if unassigned:
            rng = random.Random(seed)
            rng.shuffle(unassigned)
            n_test = max(1, int(len(unassigned) * test_ratio))
            n_val = max(1, int(len(unassigned) * val_ratio))
            for chip in unassigned[:n_test]:
                assignment[chip] = "test"
            for chip in unassigned[n_test:n_test + n_val]:
                assignment[chip] = "val"
            for chip in unassigned[n_test + n_val:]:
                assignment[chip] = "train"

    split_dir.mkdir(parents=True, exist_ok=True)
    for split_name in ("train", "val", "test"):
        ids = sorted(c for c in chips if assignment.get(c) == split_name)
        (split_dir / f"{split_name}.txt").write_text(
            "\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    return {s: _read_split(split_dir / f"{s}.txt") for s in ("train", "val", "test")}


def _read_split(path: Path):
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def load_split_samples(root, split_dir, split: str):
    """读取指定划分（train/val/test）下文件完整的样本列表。"""
    split_file = Path(split_dir) / f"{split}.txt"
    if not split_file.is_file():
        raise FileNotFoundError(
            f"缺少划分文件 {split_file}，请先运行 scripts/train.py 或 build_splits")
    chip_ids = _read_split(split_file)
    samples = [s for s in scan_scenes(root) if s["chip"] in chip_ids]
    incomplete = [s["chip"] for s in samples if s["missing"]]
    if incomplete:
        print(f"[{split}] 警告: {len(incomplete)} 个样本缺失标签，将被跳过: {incomplete}")
    return [s for s in samples if not s["missing"]]


def save_dataset_index(root, split_dir, out_path):
    """导出样本索引 CSV 及各 split 明细（train/val/test_samples.csv）。"""
    import pandas as pd

    rows = []
    for split in ("train", "val", "test"):
        for s in load_split_samples(root, split_dir, split):
            rows.append({
                "sample_id": s["chip"], "s1_path": s["s1"], "label_path": s["label"],
                "event": s["event"], "chip_id": s["chip_id"], "split": split,
            })
    df = pd.DataFrame(rows)
    out_path = Path(out_path)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    for split in ("train", "val", "test"):
        sub = df[df["split"] == split].drop(columns=["split"])
        sub.to_csv(out_dir / f"{split}_samples.csv", index=False, encoding="utf-8-sig")
    return df


# ---------------------------------------------------------------- Dataset
class Sen1Floods11Dataset(Dataset):
    """按需读取官方 v1.1 样本，返回 (image[2,H,W], mask[1,H,W], valid[1,H,W])。"""

    def __init__(self, samples, image_size: int = 256, stats: dict = None,
                 augmentation: bool = False):
        if not samples:
            raise ValueError("样本列表为空，请检查数据目录与划分结果")
        self.samples = samples
        self.image_size = int(image_size)
        self.stats = stats
        self.augmentation = augmentation

    def __len__(self):
        return len(self.samples)

    @staticmethod
    def _check_georef(chip, s1_tf, s1_crs, qc_tf, qc_crs, s1_shape, qc_shape):
        """校验 S1 与 QC 的尺寸 / CRS / transform 一致（官方数据应完全配准）。"""
        if s1_shape != qc_shape:
            raise ValueError(f"样本 {chip} S1/QC 尺寸不一致: {s1_shape} vs {qc_shape}")
        if s1_crs != qc_crs:
            raise ValueError(f"样本 {chip} S1/QC CRS 不一致: {s1_crs} vs {qc_crs}")
        if not np.allclose([s1_tf.a, s1_tf.b, s1_tf.c, s1_tf.d, s1_tf.e, s1_tf.f],
                           [qc_tf.a, qc_tf.b, qc_tf.c, qc_tf.d, qc_tf.e, qc_tf.f]):
            raise ValueError(f"样本 {chip} S1/QC transform 不一致")

    def __getitem__(self, idx):
        import rasterio

        s = self.samples[idx]
        if s.get("s1") is None or not Path(s["s1"]).is_file():
            raise FileNotFoundError(f"样本 {s['chip']} 缺少 S1 文件: {s.get('s1')}")
        if s.get("label") is None or not Path(s["label"]).is_file():
            raise FileNotFoundError(f"样本 {s['chip']} 缺少 QC 标签文件: {s.get('label')}")

        vv, vh, s1_crs, s1_tf = read_s1(s["s1"])
        with rasterio.open(s["label"]) as src:
            qc = src.read(1)
            qc_crs, qc_tf = src.crs, src.transform
        self._check_georef(s["chip"], s1_tf, s1_crs, qc_tf, qc_crs, vv.shape, qc.shape)

        mask = (qc == 1).astype(np.float32)     # 洪水像素
        valid = (qc != -1).astype(np.float32)   # 有效像素（排除无效标签 -1）

        if self.stats is None:
            raise RuntimeError("未提供 SAR 统计参数 stats，无法预处理")
        image = apply_preprocess(vv, vh, self.stats)  # (2, H, W)

        image_t = torch.from_numpy(image)
        mask_t = torch.from_numpy(mask).unsqueeze(0)
        valid_t = torch.from_numpy(valid).unsqueeze(0)

        size = self.image_size
        if image_t.shape[-2:] != (size, size):
            image_t = TF.resize(image_t, (size, size), TF.InterpolationMode.BILINEAR)
            mask_t = TF.resize(mask_t, (size, size), TF.InterpolationMode.NEAREST)
            valid_t = TF.resize(valid_t, (size, size), TF.InterpolationMode.NEAREST)

        if self.augmentation:
            if torch.rand(1).item() < 0.5:
                image_t = torch.flip(image_t, dims=[2])
                mask_t = torch.flip(mask_t, dims=[2])
                valid_t = torch.flip(valid_t, dims=[2])
            if torch.rand(1).item() < 0.5:
                image_t = torch.flip(image_t, dims=[1])
                mask_t = torch.flip(mask_t, dims=[1])
                valid_t = torch.flip(valid_t, dims=[1])
        return image_t, mask_t, valid_t
