"""可视化工具：样本展示、预测对比、训练曲线（论文风格，低饱和配色）。"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np

# 配色（低饱和）
C_BLUE = "#4C72B0"
C_ORANGE = "#DD8452"
C_GREEN = "#55A868"
C_RED = "#C44E52"

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 11
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.color"] = "#d9d9d9"
plt.rcParams["grid.linestyle"] = "--"
plt.rcParams["grid.linewidth"] = 0.6


def _style_axes(ax):
    """只保留左/下边框，刻度向内。"""
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(direction="in")
    ax.set_axisbelow(True)


def _stretch(arr, low=2, high=98):
    """线性拉伸到 [0,1]，用于灰度显示。"""
    a = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    lo, hi = np.percentile(a, [low, high])
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def _finalize(fig, path, dpi=300):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_sample_figure(vv, vh, label, scene, out_path):
    """真实样本展示：VV / VH / Flood Label / VV+VH 合成，4 联图。"""
    comp = np.stack(
        [_stretch(vv), _stretch(vh), (_stretch(vv) + _stretch(vh)) / 2.0], axis=-1
    )
    fig, axes = plt.subplots(1, 4, figsize=(17, 4.4))
    axes[0].imshow(_stretch(vv), cmap="gray")
    axes[1].imshow(_stretch(vh), cmap="gray")
    axes[2].imshow(label, cmap="Blues", vmin=0, vmax=1)
    axes[3].imshow(comp)
    for ax, title in zip(axes, ["VV", "VH", "Flood Label", "VV+VH Composite"]):
        ax.set_title(title, fontsize=12)
        ax.set_xticks([])
        ax.set_yticks([])
        _style_axes(ax)
    fig.suptitle(scene, fontsize=13)
    fig.tight_layout()
    _finalize(fig, out_path)


def save_prediction_figure(vv, vh, prob, pred, gt=None, scene="", out_path=None):
    """预测对比图：仅呈现数据，不画任何图内标题/标签（图注全部由报告 HTML 承担）。

    有 GT：VV / VH / 真实标签 / 洪水掩膜 / 误差分布（5 联）
    无 GT：VV / VH / 洪水掩膜 / 洪水概率（4 联）
    """
    has_gt = gt is not None
    n = 5 if has_gt else 4
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 3.4))
    axes[0].imshow(_stretch(vv), cmap="gray")
    axes[1].imshow(_stretch(vh), cmap="gray")
    idx = 2
    if has_gt:
        axes[idx].imshow(gt, cmap="Blues", vmin=0, vmax=1)
        idx += 1
    axes[idx].imshow(pred, cmap=ListedColormap(["#EEF2F5", "#1E6E9C"]), vmin=0, vmax=1)
    idx += 1
    if has_gt:
        error = pred.astype(np.int8) - gt.astype(np.int8)
        axes[idx].imshow(error, cmap="RdBu", vmin=-1, vmax=1)
    else:
        axes[idx].imshow(prob, cmap="Blues", vmin=0, vmax=1)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.tight_layout()
    if out_path is not None:
        _finalize(fig, out_path, dpi=240)
    return fig


def save_overlay_figure(vv, prob, mask, out_path):
    """VV 灰度底图 + 预测洪水区域（红色半透明）叠加图。"""
    fig, ax = plt.subplots(figsize=(6.4, 6.0))
    ax.imshow(_stretch(vv), cmap="gray")
    masked = np.ma.masked_where(mask == 0, mask)
    ax.imshow(masked, cmap="Reds", alpha=0.55, vmin=0, vmax=1,
              interpolation="nearest")
    ax.set_title("VV + Predicted Flood")
    ax.set_xticks([])
    ax.set_yticks([])
    _style_axes(ax)
    _finalize(fig, out_path)


def save_training_curves(history, out_dir, dpi=300):
    """从训练历史 DataFrame 生成 Loss / F1 / IoU 曲线（PNG）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    epoch = history["epoch"].to_numpy()
    curves = {
        "loss_curve": ("Loss", [
            ("train_loss", "train loss", C_BLUE),
            ("val_loss", "val loss", C_ORANGE),
        ]),
        "f1_curve": ("F1", [("f1", "F1", C_BLUE)]),
        "iou_curve": ("IoU", [("iou", "IoU", C_BLUE)]),
    }
    for fname, (ylabel, lines) in curves.items():
        fig, ax = plt.subplots(figsize=(6.6, 4.4))
        for col, label, color in lines:
            ax.plot(epoch, history[col].to_numpy(), label=label, lw=1.6, color=color)
        ax.set_xlabel("Epoch")
        ax.set_ylabel(ylabel)
        ax.set_xlim(epoch.min(), epoch.max())
        ax.legend(frameon=False, loc="best")
        _style_axes(ax)
        _finalize(fig, out_dir / f"{fname}.png", dpi=dpi)
