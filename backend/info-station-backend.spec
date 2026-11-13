# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置：把 FastAPI 后端打成单个 exe，作为 Tauri sidecar。
# 用法（在 backend 目录）：uv run pyinstaller info-station-backend.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("apscheduler")
    + collect_submodules("app")
    + collect_submodules("playwright")
    + ["feedparser", "selectolax.lexbor", "dateutil", "yaml", "greenlet", "pyee"]
    # 邮箱授权码存 Windows 凭据管理器：keyring 的 Windows 后端走 pywin32-ctypes
    + collect_submodules("keyring") + collect_submodules("win32ctypes")
)

# Playwright 没有官方 PyInstaller hook：把 driver（node.exe + package，约 110 MB）一起打进去，
# 否则安装版里“浏览器”信源和 X 信源都会报找不到 driver。浏览器本身用系统的 Chrome / Edge，不打包。
try:
    playwright_datas = collect_data_files("playwright")
except Exception:  # 没装 browser extra 时照常打包（这类信源会提示未安装 Playwright）
    playwright_datas = []

a = Analysis(
    ["run.py"],
    pathex=["."],
    datas=[("../config", "config"), ("../prompts", "prompts")] + playwright_datas + copy_metadata("keyring"),
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="info-station-backend",
    console=False,  # 不弹黑窗口；日志由 Tauri 收集
    upx=False,
)
