"""Playwright 登录态抓取（可选依赖）。

- 使用持久化浏览器 profile（data/browser-profile），用户在可见窗口里自己登录一次，之后无头复用会话。
- Windows 默认用系统自带的 Edge（channel=msedge），无需额外下载浏览器；也可设为 chrome / chromium。
- 同一 profile 同时只能被一个浏览器进程占用，所以全局加锁串行执行。
"""
from __future__ import annotations

import asyncio
import re
import shutil
import sys
from typing import Any

from .. import config
from ..textutil import normalize_url, sha1
from .base import FetchContext, FetchError, RawItem
from .html_list import extract_detail, parse_list

_lock = asyncio.Lock()


def _channel(pref: str) -> str | None:
    if pref and pref != "auto":
        return None if pref == "chromium" else pref
    if sys.platform.startswith("win"):
        return "msedge"
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return "chrome"
    return None  # Playwright 自带 chromium（需 playwright install chromium）


def installed_version(profile_dir=None) -> str | None:
    """真实浏览器的版本：Chrome / Edge 每次启动都会写 <profile>/Last Version（如 156.0.8078.80）。"""
    p = (profile_dir or config.BROWSER_PROFILE_DIR) / "Last Version"
    try:
        v = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if re.fullmatch(r"\d+(\.\d+){1,3}", v) else None


def _platform_token() -> str:
    if sys.platform.startswith("win"):
        return "Windows NT 10.0; Win64; x64"
    if sys.platform == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


def user_agent_for(settings: config.Settings, headless: bool, profile_dir=None) -> str | None:
    """浏览器 UA：

    以前固定用设置里的 “Chrome/130” UA。用真实 Chrome 156 时，这个 UA 和浏览器自己上报的
    Client Hints（Sec-CH-UA: Chrome 156）对不上，是很明显的自动化特征。现在：
    - 真实 Chrome / Edge：按 profile 里记录的实际版本生成同版本 UA（主要是去掉无头模式的 “HeadlessChrome”）；
      版本未知且是有头窗口时不覆盖，用浏览器自己的 UA
    - 自带 chromium 或版本未知的无头模式：沿用设置里的 UA
    """
    channel = _channel(settings.fetch.browser_channel)
    if channel in ("chrome", "msedge"):
        ver = installed_version(profile_dir)
        if ver:
            major = ver.split(".")[0]
            ua = (f"Mozilla/5.0 ({_platform_token()}) AppleWebKit/537.36 (KHTML, like Gecko) "
                  f"Chrome/{major}.0.0.0 Safari/537.36")
            return ua + (f" Edg/{ver}" if channel == "msedge" else "")
        if not headless:
            return None
    return settings.fetch.user_agent or None


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


async def _launch(p, settings: config.Settings, headless: bool, proxy_url: str | None = None):
    from ..proxy import for_playwright

    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {}
    pw_proxy = for_playwright(proxy_url)
    if pw_proxy:  # 没有代理时浏览器自己走系统设置
        extra["proxy"] = pw_proxy
    ua = user_agent_for(settings, headless)
    if ua:
        extra["user_agent"] = ua
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(config.BROWSER_PROFILE_DIR),
        channel=_channel(settings.fetch.browser_channel),
        headless=headless,
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        **extra,
    )


async def fetch_playwright(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    if not available():
        raise FetchError("未安装 Playwright：uv sync --extra browser 后再试")
    from playwright.async_api import async_playwright

    list_cfg = source.get("list") or {}
    detail_cfg = source.get("detail") or {}
    wait_for = source.get("wait_for") or list_cfg.get("item")
    async with _lock:
        try:
            pw_cm = async_playwright()
            p = await pw_cm.__aenter__()
        except NotImplementedError as e:  # Windows + SelectorEventLoop（uvicorn --reload）
            raise FetchError("当前事件循环不支持子进程：请不要用 --reload 启动后端") from e
        try:
            browser = await _launch(p, ctx.settings, ctx.settings.fetch.browser_headless, ctx.proxy_url)
            try:
                page = await browser.new_page()
                await page.goto(source["url"], wait_until="load", timeout=45000)
                try:
                    await page.wait_for_selector(wait_for, timeout=int(source.get("wait_ms", 15000)))
                except Exception as e:  # noqa: BLE001
                    if "login" in page.url or "cas" in page.url:
                        raise FetchError("需要登录：请在信源管理里点“打开浏览器登录”，登录一次后再抓") from e
                    raise FetchError(f"等待列表元素超时：{wait_for}") from e
                html = await page.content()
                items = parse_list(html, page.url, list_cfg)
                if detail_cfg.get("enabled"):
                    known = source.get("_known_keys") or set()
                    fresh = [it for it in items if sha1(normalize_url(it.url)) not in known]
                    for it in fresh[: int(detail_cfg.get("max", ctx.settings.fetch.max_detail_fetch))]:
                        try:
                            await page.goto(it.url, wait_until="load", timeout=30000)
                            await page.wait_for_timeout(800)
                            it.content, title = extract_detail(await page.content(), detail_cfg)
                            if title and it.title.endswith(("...", "…")):
                                it.title = title
                        except Exception:  # noqa: BLE001
                            continue
                        it.extra["detail_fetched"] = True
                return items
            finally:
                await browser.close()
        finally:
            await pw_cm.__aexit__(None, None, None)


async def open_login_window(url: str, settings: config.Settings, timeout_s: int = 600,
                            proxy_url: str | None = None) -> str:
    """弹出可见浏览器窗口让用户自己登录；用户关闭窗口（或超时）后会话保存在 profile 中。

    返回窗口关闭前最后停留的地址（X 登录据此判断是否已经登录成功）。
    """
    if not available():
        raise FetchError("未安装 Playwright")
    from playwright.async_api import async_playwright

    async with _lock:
        async with async_playwright() as p:
            browser = await _launch(p, settings, headless=False, proxy_url=proxy_url)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            last_url = {"v": url}

            def _nav(frame) -> None:
                if frame == frame.page.main_frame:
                    last_url["v"] = frame.url

            page.on("framenavigated", _nav)
            try:
                await page.goto(url)
            except Exception:  # noqa: BLE001  代理不通等：窗口仍留给用户看报错页
                pass
            closed = asyncio.Event()
            browser.on("close", lambda *_: closed.set())
            page.on("close", lambda *_: closed.set())
            try:
                await asyncio.wait_for(closed.wait(), timeout=timeout_s)
            except TimeoutError:
                pass
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
    return last_url["v"]
