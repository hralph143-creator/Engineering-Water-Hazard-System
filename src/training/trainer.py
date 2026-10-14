"""训练器：训练/验证循环、混合精度、checkpoint 保存与恢复。"""
import time
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch

from ..metrics.segmentation_metrics import MetricAccumulator


def evaluate_samples(model, loader, device, threshold: float = 0.5, use_amp: bool = False):
    """在给定 DataLoader 上逐样本计算指标。

    返回 (overall_metrics, per_sample_metrics)；per_sample 含 chip 字段。
    """
    model.eval()
    overall = MetricAccumulator()
    per_sample = []
    sample_idx = 0
    with torch.no_grad():
        for images, masks, valids in loader:
            images = images.to(device, non_blocking=True)
            masks = masks.to(device, non_blocking=True)
            valids = valids.to(device, non_blocking=True)
            with torch.autocast(device_type="cuda", dtype=torch.float16) if use_amp else nullcontext():
                logits = model(images)
            preds = (torch.sigmoid(logits) >= threshold).float()

            pred_np = preds.cpu().numpy()
            mask_np = masks.cpu().numpy()
            valid_np = valids.cpu().numpy()
            for i in range(images.size(0)):
                acc = MetricAccumulator()
                acc.update(pred_np[i], mask_np[i], valid_np[i])
                row = acc.compute()
                if hasattr(loader.dataset, "samples"):
                    rec = loader.dataset.samples[sample_idx + i]
                    row["chip"] = rec["chip"]
                    row["event"] = rec.get("event", "")
                    v = valid_np[i].astype(bool)
                    row["flood_ratio_gt"] = float(mask_np[i][v].mean()) if v.any() else 0.0
                    row["flood_ratio_pred"] = float(pred_np[i][v].mean()) if v.any() else 0.0
                per_sample.append(row)
            overall.update(pred_np, mask_np, valid_np)
            sample_idx += images.size(0)
    return overall.compute(), per_sample


class Trainer:
    def __init__(self, model, criterion, optimizer, scheduler, device, config: dict):
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.device = device
        self.config = config
        self.use_amp = bool(config["training"].get("amp", True)) and device.type == "cuda"
        if self.use_amp:
            try:
                from torch.amp import GradScaler  # torch>=2.3
                self.scaler = GradScaler("cuda")
            except ImportError:
                self.scaler = torch.cuda.amp.GradScaler()
        else:
            self.scaler = None

    def train_one_epoch(self, loader) -> float:
        self.model.train()
        total_loss = 0.0
        n = 0
        for images, masks, valids in loader:
            images = images.to(self.device, non_blocking=True)
            masks = masks.to(self.device, non_blocking=True)
            valids = valids.to(self.device, non_blocking=True)
            self.optimizer.zero_grad(set_to_none=True)
            if self.use_amp:
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    logits = self.model(images)
                    loss = self.criterion(logits, masks, valids)
                self.scaler.scale(loss).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                logits = self.model(images)
                loss = self.criterion(logits, masks, valids)
                loss.backward()
                self.optimizer.step()
            bs = images.size(0)
            total_loss += loss.item() * bs
            n += bs
        return total_loss / max(n, 1)

    @torch.no_grad()
    def evaluate(self, loader, threshold: float = 0.5) -> dict:
        self.model.eval()
        total_loss = 0.0
        n = 0
        acc = MetricAccumulator()
        for images, masks, valids in loader:
            images = images.to(self.device, non_blocking=True)
            masks = masks.to(self.device, non_blocking=True)
            valids = valids.to(self.device, non_blocking=True)
            with torch.autocast(device_type="cuda", dtype=torch.float16) if self.use_amp else nullcontext():
                logits = self.model(images)
                loss = self.criterion(logits, masks, valids)
            preds = (torch.sigmoid(logits) >= threshold).float()
            acc.update(preds.cpu().numpy(), masks.cpu().numpy(), valids.cpu().numpy())
            bs = images.size(0)
            total_loss += loss.item() * bs
            n += bs
        metrics = acc.compute()
        metrics["loss"] = total_loss / max(n, 1)
        return metrics

    def fit(self, train_loader, val_loader, epochs: int, start_epoch: int = 0,
            best_iou: float = 0.0, history=None, ckpt_last=None, ckpt_best=None,
            history_path=None, threshold: float = 0.5):
        """主训练循环。每 epoch 依据验证集 IoU 更新 best_model.pt，并保存 last_model.pt。"""
        history = history if history is not None else []
        t0 = time.time()
        for epoch in range(start_epoch, epochs):
            train_loss = self.train_one_epoch(train_loader)
            val_metrics = self.evaluate(val_loader, threshold=threshold)
            iou = val_metrics["iou"]
            lr = self.optimizer.param_groups[0]["lr"]

            if self.scheduler is not None:
                if isinstance(self.scheduler, torch.optim.lr_scheduler.ReduceLROnPlateau):
                    self.scheduler.step(iou)
                else:
                    self.scheduler.step()

            record = {
                "epoch": epoch + 1,
                "train_loss": train_loss,
                "val_loss": val_metrics["loss"],
                "precision": val_metrics["precision"],
                "recall": val_metrics["recall"],
                "f1": val_metrics["f1"],
                "iou": iou,
                "learning_rate": lr,
            }
            history.append(record)
            if history_path:
                pd.DataFrame(history).to_csv(history_path, index=False)

            is_best = iou > best_iou
            if is_best:
                best_iou = iou
            if ckpt_best and is_best:
                self.save_checkpoint(ckpt_best, epoch, best_iou)
            if ckpt_last:
                self.save_checkpoint(ckpt_last, epoch, best_iou)

            print(f"Epoch {epoch + 1}/{epochs} "
                  f"train_loss={train_loss:.4f} val_loss={val_metrics['loss']:.4f} "
                  f"f1={val_metrics['f1']:.4f} iou={iou:.4f} "
                  f"lr={lr:.2e}" + (" [best]" if is_best else ""))

        elapsed = time.time() - t0
        return history, best_iou, elapsed

    def save_checkpoint(self, path, epoch: int, best_iou: float) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ckpt = {
            "epoch": epoch + 1,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "scheduler_state_dict": self.scheduler.state_dict() if self.scheduler else None,
            "best_iou": best_iou,
            "config": self.config,
        }
        torch.save(ckpt, path)

    @staticmethod
    def load_checkpoint(path, model, optimizer=None, scheduler=None):
        """加载 checkpoint，恢复模型/优化器/调度器状态。"""
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model_state_dict"])
        if optimizer is not None and "optimizer_state_dict" in ckpt:
            optimizer.load_state_dict(ckpt["optimizer_state_dict"])
        if scheduler is not None and ckpt.get("scheduler_state_dict"):
            scheduler.load_state_dict(ckpt["scheduler_state_dict"])
        return ckpt
