"""冒烟测试：验证 v1.1 数据读取、U-Net、损失、指标、预处理（demo 数据）。

依赖缺失时自动跳过对应用例（torch / rasterio）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from tests import make_demo_data

torch = pytest.importorskip("torch")
rasterio = pytest.importorskip("rasterio")

from src.datasets.sen1floods11 import (Sen1Floods11Dataset, build_splits,
                                       load_split_samples, scan_scenes)
from src.losses.segmentation_loss import SegmentationLoss
from src.metrics.segmentation_metrics import MetricAccumulator
from src.models.unet import UNet
from src.preprocessing.sar_preprocess import (apply_preprocess, compute_sar_stats,
                                              load_stats, read_s1, save_stats)


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    make_demo_data.generate_demo(root)
    return root


def test_unet_forward():
    model = UNet(in_channels=2, out_channels=1, base_channels=64)
    y = model(torch.randn(2, 2, 256, 256))
    assert y.shape == (2, 1, 256, 256)


def test_loss_with_invalid_pixels():
    criterion = SegmentationLoss()
    logits = torch.randn(2, 1, 64, 64)
    target = torch.randint(0, 2, (2, 1, 64, 64)).float()
    valid = torch.ones_like(target)
    valid[:, :, 0:10, 0:10] = 0
    loss = criterion(logits, target, valid)
    assert torch.isfinite(loss) and loss > 0


def test_metric_accumulator_known():
    acc = MetricAccumulator()
    acc.update(np.array([1, 1, 0, 0]), np.array([1, 0, 1, 0]))
    m = acc.compute()
    # tp=1 fp=1 fn=1 tn=1
    assert m["precision"] == pytest.approx(0.5)
    assert m["recall"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(0.5)
    assert m["iou"] == pytest.approx(1 / 3)
    assert m["accuracy"] == pytest.approx(0.5)


def test_s1_two_band_read(demo_root):
    s1_dir = demo_root / "v1.1" / "data" / "flood_events" / "HandLabeled" / "S1Hand"
    s1 = sorted(s1_dir.glob("*_S1Hand.tif"))[0]
    vv, vh, crs, _ = read_s1(s1)
    assert vv.shape == vh.shape == (64, 64)
    assert crs is not None
    assert not np.isnan(vv).any()  # NaN 已被置 0


def test_scan_pairing(demo_root):
    samples = scan_scenes(demo_root)
    assert len(samples) == len(make_demo_data.CHIPS)
    for s in samples:
        assert s["s1"].endswith("_S1Hand.tif")
        assert s["label"].endswith("_LabelHand.tif")
        assert not s["missing"]


def test_build_splits_official(demo_root):
    assignment = build_splits(demo_root, demo_root / "processed", seed=42)
    assert set(assignment.keys()) == {"train", "val", "test"}
    covered = set().union(*assignment.values())
    assert covered == {s["chip"] for s in scan_scenes(demo_root)}
    assert not (assignment["train"] & assignment["val"])
    assert not (assignment["train"] & assignment["test"])
    assert not (assignment["val"] & assignment["test"])


def test_load_split_samples(demo_root):
    build_splits(demo_root, demo_root / "processed", seed=42)
    train = load_split_samples(demo_root, demo_root / "processed" / "splits", "train")
    assert len(train) > 0
    assert all(not s["missing"] for s in train)


def test_dataset_v11(demo_root):
    samples = scan_scenes(demo_root)
    stats = compute_sar_stats([s["s1"] for s in samples])
    stats_path = demo_root / "processed" / "sar_stats.json"
    save_stats(stats, stats_path)
    ds = Sen1Floods11Dataset(samples, image_size=256,
                             stats=load_stats(stats_path))
    image, mask, valid = ds[0]
    assert image.shape == (2, 256, 256)
    assert mask.shape == (1, 256, 256)
    assert valid.shape == (1, 256, 256)
    assert image.dtype == torch.float32
    assert mask.min() >= 0 and mask.max() <= 1
    assert valid.min() >= 0 and valid.max() <= 1
    # resize 后 mask 仍为离散 0/1
    assert set(torch.unique(mask).tolist()) <= {0.0, 1.0}


def test_qc_invalid_ignored(demo_root):
    qc_dir = demo_root / "v1.1" / "data" / "flood_events" / "HandLabeled" / "LabelHand"
    for qc in sorted(qc_dir.glob("*_LabelHand.tif")):
        with rasterio.open(qc) as src:
            lab = src.read(1)
        if -1 in np.unique(lab):
            mask = (lab == 1).astype(np.float32)
            valid = (lab != -1).astype(np.float32)
            assert valid.min() == 0.0            # 存在无效像素
            assert mask.sum() + (lab == 0).sum() == valid.sum()  # 无效像素未被当无水
            return
    pytest.skip("demo 数据中未生成含 -1 的 QC")


def test_preprocess_consistency(demo_root):
    """同一 stats 下，裁剪+归一化结果应在 [0,1] 内。"""
    samples = scan_scenes(demo_root)
    stats = compute_sar_stats([s["s1"] for s in samples])
    vv, vh, _, _ = read_s1(samples[0]["s1"])
    out = apply_preprocess(vv, vh, stats)
    assert out.shape == (2, vv.shape[0], vv.shape[1])
    assert out.min() >= 0.0 and out.max() <= 1.0 + 1e-6
