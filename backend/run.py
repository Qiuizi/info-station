"""后端入口：开发时 `uv run python run.py`；PyInstaller 打包后作为 Tauri sidecar 运行。"""
import argparse
import os
import sys
import threading
import time

import uvicorn


def _force_utf8_stdio() -> None:
    """中文 Windows 的控制台 / 管道默认是 cp936（GBK）：日志里的中文、emoji 会乱码或抛 UnicodeEncodeError，
    而 Tauri 按 UTF-8 读 sidecar 输出。统一改成 UTF-8；子进程（Playwright 等）也继承 UTF-8 模式。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _log_to_file_when_frozen() -> None:
    """打包后（PyInstaller、无控制台）stdout/stderr 可能是 None 或没人读：写到 数据目录\\logs\\backend.log。

    超过 5 MB 时把旧文件改名为 backend.log.1（只留一份）。
    """
    if not getattr(sys, "frozen", False):
        return
    from app import config

    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "backend.log"
    try:
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            os.replace(path, log_dir / "backend.log.1")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115  进程生命周期内一直写
    sys.stdout = sys.stderr = f


def watch_parent(pid: int | None) -> threading.Thread | None:
    """父进程（Tauri 桌面壳）退出后，后端自己退出。

    PyInstaller 单文件 exe 是“引导进程 + 真正的 Python 子进程”两层，Tauri 退出时只能结束引导进程，
    Python 子进程会残留并占着 8765 端口。所以由后端盯着 Tauri 的进程号，它一退出就跟着退出。
    """
    if not pid:
        return None

    def _wait() -> None:
        if sys.platform.startswith("win"):
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
            if handle:
                kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
                kernel32.CloseHandle(handle)
        else:
            while True:
                try:
                    os.kill(int(pid), 0)
                except OSError:
                    break
                time.sleep(2)
        print(f"父进程 {pid} 已退出，后端随之退出", flush=True)
        os._exit(0)

    t = threading.Thread(target=_wait, name="watch-parent", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    _log_to_file_when_frozen()
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("INFO_STATION_PORT", 8765)))
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--parent-pid", type=int, default=None, help="桌面壳的进程号：它退出时后端也退出")
    a = ap.parse_args()
    watch_parent(a.parent_pid)
    from app.main import app  # noqa: E402  确保 PyInstaller 能分析到依赖

    if a.reload and sys.platform.startswith("win"):
        print("提示：Windows 上 --reload 会让 uvicorn 使用 SelectorEventLoop，Playwright（浏览器抓取）将无法启动子进程。")
    if a.reload:
        uvicorn.run("app.main:app", host=a.host, port=a.port, reload=True)
    else:
        # 打包后没有终端：关掉彩色输出（uvicorn 默认会调用 sys.stdout.isatty()）
        uvicorn.run(app, host=a.host, port=a.port, log_level="info", use_colors=False)
