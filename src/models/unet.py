"""基础 U-Net 洪水分割模型。

结构：Encoder(4 次下采样) -> Bottleneck -> Decoder(4 次上采样 + Skip) -> Head。
输入 2 通道（VV+VH），输出 1 通道 logits（洪水概率，经 sigmoid）。
仅使用标准组件：Conv2d / BatchNorm / ReLU / MaxPool / ConvTranspose2d。
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.block(x)


class Down(nn.Module):
    """MaxPool + DoubleConv。"""
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.block = nn.Sequential(nn.MaxPool2d(2), DoubleConv(in_ch, out_ch))

    def forward(self, x):
        return self.block(x)


class Up(nn.Module):
    """ConvTranspose2d + Skip Connection 拼接 + DoubleConv。

    in_ch: 上采样输入通道；skip_ch: 跳跃连接通道；out_ch: 输出通道。
    拼接通道数 = up 输出(out_ch) + skip_ch，故 conv 输入为 out_ch + skip_ch。
    """
    def __init__(self, in_ch, skip_ch, out_ch):
        super().__init__()
        self.up = nn.ConvTranspose2d(in_ch, out_ch, kernel_size=2, stride=2)
        self.conv = DoubleConv(out_ch + skip_ch, out_ch)

    def forward(self, x1, x2):
        x1 = self.up(x1)
        # 处理下采样导致的尺寸奇偶不一致
        dy = x2.size(2) - x1.size(2)
        dx = x2.size(3) - x1.size(3)
        x1 = F.pad(x1, [dx // 2, dx - dx // 2, dy // 2, dy - dy // 2])
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class UNet(nn.Module):
    def __init__(self, in_channels: int = 2, out_channels: int = 1,
                 base_channels: int = 64):
        super().__init__()
        self.inc = DoubleConv(in_channels, base_channels)
        self.down1 = Down(base_channels, base_channels * 2)
        self.down2 = Down(base_channels * 2, base_channels * 4)
        self.down3 = Down(base_channels * 4, base_channels * 8)
        self.down4 = Down(base_channels * 8, base_channels * 8)  # bottleneck
        self.up1 = Up(base_channels * 8, base_channels * 8, base_channels * 4)
        self.up2 = Up(base_channels * 4, base_channels * 4, base_channels * 2)
        self.up3 = Up(base_channels * 2, base_channels * 2, base_channels)
        self.up4 = Up(base_channels, base_channels, base_channels)
        self.head = nn.Conv2d(base_channels, out_channels, kernel_size=1)

    def forward(self, x):
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        return self.head(x)


if __name__ == "__main__":
    model = UNet(in_channels=2, out_channels=1, base_channels=64)
    y = model(torch.randn(1, 2, 256, 256))
    print("output:", tuple(y.shape))
    n_params = sum(p.numel() for p in model.parameters())
    print(f"params: {n_params / 1e6:.2f}M")
