"""推理器：加载训练好的模型，对 VV/VH 预测洪水概率与二值掩膜。

推理与训练使用同一份 SAR 统计参数（stats），保证预处理一致。
输入尺寸任意（需为 16 的倍数，U-Net 4 次下采样）；否则自动补零后裁剪回原尺寸。
"""
import numpy as np
import torch

from ..preprocessing.sar_preprocess import apply_preprocess


class FloodPredictor:
    def __init__(self, model, checkpoint, stats: dict, device, threshold: float = 0.5):
        ckpt = torch.load(checkpoint, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state_dict"])
        model.to(device).eval()
        self.model = model
        self.stats = stats
        self.device = device
        self.threshold = float(threshold)

    @torch.no_grad()
    def predict(self, vv: np.ndarray, vh: np.ndarray):
        """vv/vh: (H, W) 原始数组；返回 (prob, mask)，mask 为 uint8 0/1。"""
        image = apply_preprocess(vv, vh, self.stats)  # (2, H, W)
        h, w = image.shape[1:]
        pad_h = (16 - h % 16) % 16
        pad_w = (16 - w % 16) % 16
        if pad_h or pad_w:
            image = np.pad(image, ((0, 0), (0, pad_h), (0, pad_w)), mode="constant")
        tensor = torch.from_numpy(image).unsqueeze(0).to(self.device)
        logits = self.model(tensor)
        prob = torch.sigmoid(logits)[0, 0].cpu().numpy()[:h, :w]
        mask = (prob >= self.threshold).astype(np.uint8)
        return prob, mask
