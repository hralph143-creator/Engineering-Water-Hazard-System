"""分割损失：BCEWithLogitsLoss + Dice Loss。

Dice 系数 = 2|P∩T| / (|P| + |T|)，Dice Loss = 1 - Dice。
仅在有效像素（valid mask）上计算，忽略标签中的无效像素（-1），
避免把无效区域当作“无水”像素计入损失。
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

_EPS = 1e-6


def soft_dice_loss(logits, target, valid=None, eps: float = _EPS):
    probs = torch.sigmoid(logits)
    if valid is not None:
        probs = probs * valid
        target = target * valid
    inter = (probs * target).sum()
    union = probs.sum() + target.sum()
    dice = (2 * inter + eps) / (union + eps)
    return 1.0 - dice


class SegmentationLoss(nn.Module):
    """Loss = bce_weight * BCE + dice_weight * (1 - Dice)。"""

    def __init__(self, bce_weight: float = 1.0, dice_weight: float = 1.0):
        super().__init__()
        self.bce_weight = bce_weight
        self.dice_weight = dice_weight

    def forward(self, logits, target, valid=None):
        if valid is not None:
            bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
            bce = (bce * valid).sum() / (valid.sum() + _EPS)
        else:
            bce = F.binary_cross_entropy_with_logits(logits, target)
        dice = soft_dice_loss(logits, target, valid)
        return self.bce_weight * bce + self.dice_weight * dice
