"""分割评价指标：Precision / Recall / F1 / IoU / Dice / Accuracy。

仅在有效像素上统计（排除标签中的无效像素 -1）。
二值分割中 Dice == F1，指标给出两者以对应验收标准。
"""
import numpy as np

_EPS = 1e-6


class MetricAccumulator:
    """累积混淆矩阵元素，避免一次性驻留大量预测结果。"""

    def __init__(self):
        self.tp = 0.0
        self.fp = 0.0
        self.fn = 0.0
        self.tn = 0.0

    def update(self, pred, target, valid=None):
        p = np.asarray(pred).astype(bool)
        t = np.asarray(target).astype(bool)
        if valid is None:
            v = np.ones_like(p)
        else:
            v = np.asarray(valid).astype(bool)
        p, t, v = p[v], t[v], v[v]
        self.tp += float(np.logical_and(p, t).sum())
        self.fp += float(np.logical_and(p, ~t).sum())
        self.fn += float(np.logical_and(~p, t).sum())
        self.tn += float(np.logical_and(~p, ~t).sum())

    def compute(self) -> dict:
        tp, fp, fn, tn = self.tp, self.fp, self.fn, self.tn
        precision = tp / (tp + fp + _EPS)
        recall = tp / (tp + fn + _EPS)
        f1 = 2 * tp / (2 * tp + fp + fn + _EPS)
        iou = tp / (tp + fp + fn + _EPS)
        dice = f1
        accuracy = (tp + tn) / (tp + tn + fp + fn + _EPS)
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "iou": iou,
            "dice": dice,
            "accuracy": accuracy,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
        }
