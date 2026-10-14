# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, get_package_paths

root = Path(SPECPATH).parent
shiboken_package = Path(get_package_paths("shiboken6")[1])

a = Analysis(
    [str(root / "desktop_launcher.py")],
    pathex=[str(root)],
    # QtCore.pyd 会经 pyside6.abi3.dll 加载 shiboken6.abi3.dll。
    # Windows 解析 QtCore 原生依赖时不会搜索 PyInstaller 默认的同级目录，
    # 因此将 shiboken6 DLL 同时放到 PySide6 DLL 所在目录。
    binaries=[(str(shiboken_package / "shiboken6.abi3.dll"), "PySide6")],
    datas=[
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "frontend" / "public" / "system-icon.png"), "frontend/public"),
    ],
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite.pysqlite", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets", "multipart",
    ] + collect_submodules("rasterio"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 构建解释器中可能安装了下列包，但桌面识别程序并不使用。
    # 显式排除可避免 PyInstaller 的 torch/pandas 钩子带入无关的机器学习、
    # 云服务、Notebook 和音频依赖。
    excludes=[
        "tkinter", "pytest", "IPython", "jupyter", "notebook",
        "transformers", "torchaudio", "torchvision", "tensorboard",
        "sklearn", "scipy", "boto3", "botocore", "pyarrow", "openpyxl",
        "fsspec", "win32com",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ZhiyuShield", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False,
    icon=str(root / "packaging" / "ZhiyuShield.ico"),
    version=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=True,
    upx_exclude=[], name="ZhiyuShield",
)
