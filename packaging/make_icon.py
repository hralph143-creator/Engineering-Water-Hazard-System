from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
target = Path(__file__).resolve().parent / "ZhiyuShield.ico"
source = root / "frontend" / "public" / "system-icon.png"
image = Image.open(source).convert("RGBA")
image.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(target)
