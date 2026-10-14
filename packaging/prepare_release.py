from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from backend.app.config import get_config  # noqa: E402

release = root / "release" / "ZhiyuShield"
config = get_config()
model_dir = release / "resources" / "model"
model_dir.mkdir(parents=True, exist_ok=True)

files = {
    config.model.checkpoint: model_dir / "best_model.pt",
    config.model.sar_stats: model_dir / "sar_stats.json",
    config.model.experiment_summary: model_dir / "experiment_summary.json",
    config.model.training_history: model_dir / "training_history.csv",
}
missing = [str(source) for source in files if not source.is_file()]
if missing:
    raise SystemExit("Missing release resources:\n" + "\n".join(missing))
for source, target in files.items():
    shutil.copy2(source, target)

for source, target in (
    (root / "README.md", release / "使用说明.md"),
    (root / "SYSTEM_GUIDE.md", release / "系统运行说明.md"),
    (root / "FINAL_ACCEPTANCE.md", release / "最终验收说明.md"),
    (root / "系统功能说明.md", release / "系统功能说明.md"),
):
    shutil.copy2(source, target)
(release / "请保留完整程序目录.txt").write_text(
    "请双击 ZhiyuShield.exe 启动智域水盾。\n"
    "请勿只复制 EXE；_internal 和 resources 目录是程序运行所必需的。\n"
    "运行数据默认保存在当前用户的 LOCALAPPDATA\\ZhiyuShield 目录。\n"
    "升级安装会沿用已有本地数据，不依赖开发电脑的绝对路径。\n",
    encoding="utf-8-sig",
)
print(release)
