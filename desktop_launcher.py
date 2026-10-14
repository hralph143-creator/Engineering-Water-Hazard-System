"""Windows 便携程序入口：启动本地 API，并在内嵌浏览器中加载前端页面。"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
import yaml
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSplashScreen
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWebEngineWidgets import QWebEngineView


APP_NAME = "工程水害管理系统"


def bundled_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def application_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def user_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    legacy = base / "FloodRiskSystem"
    preferred = base / "ZhiyuShield"
    # 已安装用户继续使用原目录，确保配置、数据库与历史成果不丢失；新安装使用新品牌目录。
    path = legacy if legacy.exists() else preferred
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_runtime_config(app_dir: Path, data_dir: Path) -> Path:
    config_dir = data_dir / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "app.yaml"
    model_dir = app_dir / "resources" / "model"
    runtime_dir = data_dir / "runtime"
    payload = {
        "model": {
            "checkpoint": str(model_dir / "best_model.pt"),
            "sar_stats": str(model_dir / "sar_stats.json"),
            "experiment_summary": str(model_dir / "experiment_summary.json"),
            "training_history": str(model_dir / "training_history.csv"),
            "threshold": 0.65,
            "device": "auto",
            "name": "U-Net",
            "version": "Sen1Floods11-v1.1-baseline",
        },
        "paths": {
            "uploads": str(runtime_dir / "uploads"),
            "outputs": str(runtime_dir / "outputs"),
            "database": str(runtime_dir / "floodrisk.db"),
        },
        "risk": {"low": 0.02, "medium": 0.08, "high": 0.2},
        "risk_rules": {"version": "water-risk-v1", "new_attention": 0.01, "new_high": 0.05, "persistent_attention": 0.02, "persistent_high": 0.08, "net_expansion": 0.01, "net_reduction": -0.01},
        "work_area_impact": {"version": "work-area-impact-v1", "high_attention_ratio": 0.05, "inspection_ratio": 0.10},
        "equipment_position": {"version": "equipment-water-link-v1", "position_stale_hours": 24},
        "terrain": {"version": "terrain-analysis-v1", "low_elevation_percentile": 20},
        "rainfall": {"version": "rainfall-event-v1", "rain_event_gap_hours": 6, "change_rainfall_pre_window_hours": 24},
    }
    if config_path.is_file():
        current = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        changed = False
        for section, values in payload.items():
            if section not in current:
                current[section], changed = values, True
            elif isinstance(values, dict) and isinstance(current[section], dict):
                for key, value in values.items():
                    if key not in current[section]:
                        current[section][key], changed = value, True
        if changed:
            config_path.write_text(yaml.safe_dump(current, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return config_path
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_path


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as source:
        source.bind(("127.0.0.1", 0))
        return int(source.getsockname()[1])


class BackendThread(threading.Thread):
    def __init__(self, port: int) -> None:
        super().__init__(name="FloodRiskBackend", daemon=True)
        self.port = port
        self.server: uvicorn.Server | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            from backend.app.main import app
            config = uvicorn.Config(
                app, host="127.0.0.1", port=self.port, log_config=None,
                access_log=False, lifespan="on",
            )
            self.server = uvicorn.Server(config)
            self.server.run()
        except Exception as exc:  # pragma: no cover - desktop startup guard
            self.error = exc
            logging.exception("Desktop backend failed")

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True


class MainWindow(QWebEngineView):
    def __init__(self, url: QUrl, icon: QIcon) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.setMinimumSize(1100, 700)
        self.resize(1500, 900)
        self.page().newWindowRequested.connect(lambda request: QDesktopServices.openUrl(request.requestedUrl()))
        self.page().profile().downloadRequested.connect(self._download)
        self.load(url)

    def _download(self, download: QWebEngineDownloadRequest) -> None:
        suggested = download.downloadFileName() or "download"
        path, _ = QFileDialog.getSaveFileName(self, "保存文件", str(Path.home() / "Downloads" / suggested))
        if not path:
            download.cancel()
            return
        target = Path(path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("工程水害管理系统")
    app.setOrganizationName("ZhiyuShield")
    icon_path = bundled_path("frontend", "public", "system-icon.png")
    icon = QIcon(str(icon_path))
    app.setWindowIcon(icon)

    app_dir = application_dir()
    data_dir = user_data_dir()
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(app_dir)
    os.environ["FLOODRISK_DATA_DIR"] = str(data_dir)
    os.environ["FLOODRISK_CONFIG_PATH"] = str(ensure_runtime_config(app_dir, data_dir))
    os.environ["FLOODRISK_FRONTEND_DIR"] = str(bundled_path("frontend", "dist"))

    port = reserve_port()
    backend = BackendThread(port)
    backend.start()

    splash = QSplashScreen(icon.pixmap(420, 420))
    splash.showMessage("正在启动模型与本地服务，请稍候……")
    splash.show()
    started = time.monotonic()
    window: MainWindow | None = None

    def poll_backend() -> None:
        nonlocal window
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
                ready = response.status == 200
        except (URLError, OSError):
            ready = False
        if ready:
            timer.stop()
            window = MainWindow(QUrl(f"http://127.0.0.1:{port}/"), icon)
            window.showMaximized()
            splash.finish(window)
            return
        if backend.error or time.monotonic() - started > 180:
            timer.stop()
            splash.close()
            detail = str(backend.error) if backend.error else "后端启动超时，请查看运行日志。"
            QMessageBox.critical(None, "启动失败", f"系统无法启动。\n\n{detail}\n\n日志：{log_dir / 'error.log'}")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll_backend)
    timer.start(300)

    def shutdown() -> None:
        backend.stop()
        backend.join(timeout=8)

    app.aboutToQuit.connect(shutdown)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
