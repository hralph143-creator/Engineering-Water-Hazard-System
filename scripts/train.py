"""训练入口：Sen1Floods11 + U-Net 洪水分割基线。

流程：划分数据集 -> 统计 SAR 归一化参数 -> 构建 DataLoader -> 训练
      -> 保存 best/last checkpoint -> 测试集评价 -> 训练曲线 -> 实验记录。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import rasterio
import torch
import yaml
from torch.utils.data import DataLoader

from src.datasets.sen1floods11 import (Sen1Floods11Dataset, build_splits,
                                       load_split_samples, save_dataset_index)
from src.inference.predictor import FloodPredictor
from src.losses.segmentation_loss import SegmentationLoss
from src.models.unet import UNet
from src.preprocessing.sar_preprocess import (compute_sar_stats, load_stats,
                                              read_s1, save_stats)
from src.training.trainer import Trainer, evaluate_samples
from src.utils.device import resolve_device
from src.utils.seed import set_seed
from src.utils.visualization import save_prediction_figure, save_training_curves


def _build_loaders(cfg, samples, stats, shuffle):
    image_size = cfg["data"]["image_size"]
    ds = Sen1Floods11Dataset(
        samples, image_size=image_size, stats=stats,
        augmentation=cfg["data"].get("augmentation", True) and shuffle,
    )
    return DataLoader(
        ds, batch_size=cfg["training"]["batch_size"], shuffle=shuffle,
        num_workers=cfg["training"]["num_workers"], pin_memory=True,
        drop_last=False,
    )


def main():
    parser = argparse.ArgumentParser(description="训练 Sen1Floods11 U-Net")
    parser.add_argument("--config", default="configs/train.yaml")
    parser.add_argument("--resume", default=None, help="从 checkpoint 继续训练")
    parser.add_argument("--rebuild-splits", action="store_true", help="重新生成划分")
    parser.add_argument("--skip-eval", action="store_true", help="训练后不进行测试集评价")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    set_seed(cfg["seed"])
    device = resolve_device(cfg["training"].get("device", "auto"))
    print(f"设备: {device}  CUDA 可用: {torch.cuda.is_available()}")

    root = Path(cfg["data"]["root"])
    if not root.is_dir():
        print(f"数据目录不存在: {root.resolve()}，请先放置 Sen1Floods11 数据（见 data/README.md）")
        sys.exit(1)

    # 1) 数据集划分（优先官方，否则按场景随机；保存到 split_dir）
    processed_dir = Path(cfg["data"]["processed_dir"])
    build_splits(root, processed_dir, seed=cfg["seed"], force=args.rebuild_splits)
    train_samples = load_split_samples(root, cfg["data"]["split_dir"], "train")
    val_samples = load_split_samples(root, cfg["data"]["split_dir"], "val")
    if not train_samples or not val_samples:
        print("训练/验证样本为空，请检查数据与划分")
        sys.exit(1)
    print(f"训练样本: {len(train_samples)}  验证样本: {len(val_samples)}")

    # 2) SAR 归一化参数（仅在训练集统计，训练/验证/测试/推理共用）
    stats_path = Path(cfg["data"]["stats_path"])
    if stats_path.is_file():
        stats = load_stats(stats_path)
        print(f"使用已有 SAR 统计参数: {stats_path}")
    else:
        print("统计训练集 SAR 归一化参数 ...")
        stats = compute_sar_stats([s["s1"] for s in train_samples])
        save_stats(stats, stats_path)
        print(f"SAR 统计参数已保存: {stats_path}")

    # 3) DataLoader
    train_loader = _build_loaders(cfg, train_samples, stats, shuffle=True)
    val_loader = _build_loaders(cfg, val_samples, stats, shuffle=False)

    # 4) 模型 / 损失 / 优化器 / 调度器
    model = UNet(in_channels=cfg["model"]["in_channels"],
                 out_channels=cfg["model"]["out_channels"],
                 base_channels=cfg["model"]["base_channels"]).to(device)
    criterion = SegmentationLoss(bce_weight=cfg["training"]["bce_weight"],
                                 dice_weight=cfg["training"]["dice_weight"])
    optimizer = torch.optim.Adam(model.parameters(),
                                 lr=cfg["training"]["learning_rate"])
    sch_cfg = cfg["training"].get("scheduler", {})
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=sch_cfg.get("factor", 0.5),
        patience=sch_cfg.get("patience", 5))

    trainer = Trainer(model, criterion, optimizer, scheduler, device, cfg)
    metrics_dir = Path(cfg["outputs"]["metrics_dir"])
    metrics_dir.mkdir(parents=True, exist_ok=True)
    save_dataset_index(root, cfg["data"]["split_dir"], metrics_dir / "dataset_index.csv")
    history_path = metrics_dir / "training_history.csv"
    threshold = cfg["inference"]["threshold"]

    # 5) 恢复训练
    start_epoch, best_iou, history = 0, 0.0, []
    if args.resume:
        ckpt = Trainer.load_checkpoint(args.resume, model, optimizer, scheduler)
        start_epoch = int(ckpt["epoch"])
        best_iou = float(ckpt["best_iou"])
        if history_path.is_file():
            hist = pd.read_csv(history_path)
            history = hist[hist["epoch"] <= start_epoch].to_dict("records")
        print(f"恢复训练: 从 epoch {start_epoch + 1} 继续，历史最佳 IoU={best_iou:.4f}")

    # 6) 训练
    epochs = cfg["training"]["epochs"]
    if start_epoch >= epochs:
        print(f"checkpoint 已达最终 epoch（{epochs}），无需继续训练")
        elapsed = None
    else:
        history, best_iou, elapsed = trainer.fit(
            train_loader, val_loader, epochs, start_epoch=start_epoch,
            best_iou=best_iou, history=history,
            ckpt_last=cfg["checkpoint"]["last"], ckpt_best=cfg["checkpoint"]["best"],
            history_path=history_path, threshold=threshold)
        print(f"训练完成: 最佳 Val IoU={best_iou:.4f}，耗时 {elapsed:.1f}s")
        print(f"checkpoint: best={cfg['checkpoint']['best']}  last={cfg['checkpoint']['last']}")

    # 7) 训练曲线
    if history:
        save_training_curves(pd.DataFrame(history), cfg["outputs"]["figures_dir"])
        print(f"训练曲线已输出到 {cfg['outputs']['figures_dir']}")

    # 8) 测试集评价（真实运行，非虚构）
    experiment = {
        "config": args.config, "device": str(device),
        "train_samples": len(train_samples), "val_samples": len(val_samples),
        "best_epoch": history[-1]["epoch"] if history else start_epoch,
        "best_val_iou": best_iou,
        "elapsed_seconds": elapsed if history else None,
    }
    if not args.skip_eval:
        # 测试评价统一使用 best_model.pt（与 scripts/evaluate.py 一致）
        best_ckpt = Path(cfg["checkpoint"]["best"])
        if best_ckpt.is_file():
            ckpt = torch.load(best_ckpt, map_location="cpu", weights_only=False)
            model.load_state_dict(ckpt["model_state_dict"])
            model.to(device)
        test_samples = load_split_samples(root, cfg["data"]["split_dir"], "test")
        if test_samples:
            test_loader = _build_loaders(cfg, test_samples, stats, shuffle=False)
            overall, per_sample = evaluate_samples(
                model, test_loader, device, threshold=threshold,
                use_amp=bool(cfg["training"].get("amp", True)) and device.type == "cuda")
            test_df = pd.DataFrame(per_sample)
            overall_row = {"chip": "OVERALL", **{k: v for k, v in overall.items()}}
            test_df = pd.concat([test_df, pd.DataFrame([overall_row])], ignore_index=True)
            test_csv = metrics_dir / "test_metrics.csv"
            test_df.to_csv(test_csv, index=False, encoding="utf-8-sig")
            per_csv = metrics_dir / "test_metrics_per_sample.csv"
            test_df[test_df["chip"] != "OVERALL"].to_csv(per_csv, index=False,
                                                          encoding="utf-8-sig")
            print("\n========== 测试集总体指标 ==========")
            for k in ("precision", "recall", "f1", "iou", "dice", "accuracy"):
                print(f"{k:>10s}: {overall[k]:.4f}")
            print(f"测试集指标已导出: {test_csv}")
            experiment["test"] = {k: overall[k] for k in
                                  ("precision", "recall", "f1", "iou", "dice", "accuracy")}
            experiment["test_samples"] = len(test_samples)

            # 每个测试样本输出预测对比图（满足验收标准）
            fig_dir = Path(cfg["inference"]["out_dir"]) / "figures"
            fig_dir.mkdir(parents=True, exist_ok=True)
            pred = FloodPredictor(model, cfg["checkpoint"]["best"], stats, device,
                                  threshold=threshold)
            for s in test_samples:
                vv, vh, _, _ = read_s1(s["s1"])
                prob, mask = pred.predict(vv, vh)
                with rasterio.open(s["label"]) as src:
                    qc = src.read(1)
                gt = (qc == 1).astype("uint8")
                save_prediction_figure(vv, vh, prob, mask, gt=gt,
                                       scene=f"[test] {s['chip']}",
                                       out_path=fig_dir / f"{s['chip']}.png")
            print(f"测试样本预测对比图已输出到 {fig_dir}")
        else:
            print("测试集为空，跳过测试评价")

    record_path = metrics_dir / "experiment_summary.json"
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(experiment, f, indent=2, ensure_ascii=False)
    print(f"实验记录已保存: {record_path}")


if __name__ == "__main__":
    main()
