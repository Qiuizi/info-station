"""把 apps/desktop/src-tauri/icons/source/*.svg 渲染成 Tauri 需要的全部图标。

用法（仓库根目录，Windows / Linux 都行）：
    uv run --no-project --with resvg-py --with pillow python scripts/render-icons.py

步骤：
1. icon.svg（824/1024 瓦片，macOS 图标网格）→ icons/source/icon-1024.png，
   再调用 `pnpm tauri icon` 生成 icns、各尺寸 PNG、Square*Logo、StoreLogo；
2. 小尺寸（≤48px）改用 icon-small.svg（三道粗弧，留白更少），重写 icon.ico（16/24/32/48/64/256）、
   32x32.png、Square30x30Logo.png、Square44x44Logo.png；
3. tray.svg → icons/tray-32.rgba（32×32 原始 RGBA，lib.rs 里 include_bytes! 用作托盘图标）；
4. 界面内 logo / favicon：apps/desktop/public/favicon.svg = tray.svg（铺满的小尺寸版）。
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
ICONS = DESKTOP / "src-tauri" / "icons"
SRC = ICONS / "source"


def render(svg: Path, size: int) -> Image.Image:
    """先按 4 倍（至少 256）渲染再缩小，小尺寸边缘更干净。"""
    big = size if size >= 256 else max(size * 4, 256)
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg.read_text(encoding="utf-8"), width=big, height=big))
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    return im if big == size else im.resize((size, size), Image.LANCZOS)


def pick(size: int) -> Path:
    return SRC / ("icon-small.svg" if size <= 48 else "icon.svg")


def main() -> None:
    master = SRC / "icon-1024.png"
    render(SRC / "icon.svg", 1024).save(master)
    tauri = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    if "--skip-tauri" not in sys.argv:
        subprocess.run([tauri, "tauri", "icon", str(master), "-o", str(ICONS)], cwd=DESKTOP, check=True)
        for extra in ("android", "ios"):  # 只做桌面端
            shutil.rmtree(ICONS / extra, ignore_errors=True)

    sizes = [16, 24, 32, 48, 64, 256]
    imgs = [render(pick(s), s) for s in sizes]
    imgs[-1].save(ICONS / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    for name, size in (("32x32.png", 32), ("Square30x30Logo.png", 30), ("Square44x44Logo.png", 44)):
        render(pick(size), size).save(ICONS / name)

    tray = render(SRC / "tray.svg", 32)
    (ICONS / "tray-32.rgba").write_bytes(tray.tobytes())
    tray.save(SRC / "tray-32.png")
    shutil.copyfile(SRC / "tray.svg", DESKTOP / "public" / "favicon.svg")
    print("图标已生成：", ICONS)


if __name__ == "__main__":
    main()
