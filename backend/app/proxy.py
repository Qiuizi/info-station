"""出站代理：设置页的全局代理地址，按信源启用（use_proxy）；未填写时尝试读取 Windows 系统代理。

- httpx：FetchContext 创建客户端时传 proxy=
- Playwright：launch_persistent_context(proxy={"server": ..., "username": ..., "password": ...})
- 系统代理只读“手动代理”（Internet 设置 → 代理服务器）；PAC 脚本读不了，此时浏览器仍会自己走系统设置。
"""
from __future__ import annotations

import sys
import urllib.request
from typing import Any
from urllib.parse import unquote, urlsplit

from .config import Settings

DEFAULT_ON_TYPES = {"x"}  # 这些类型的信源默认走代理


def _norm(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def _parse_proxy_server(value: str) -> str | None:
    """解析注册表 ProxyServer：可能是 "127.0.0.1:7890"，也可能是 "http=127.0.0.1:7890;https=…;socks=…"。"""
    value = (value or "").strip()
    if not value:
        return None
    if "=" not in value:
        return _norm(value)
    parts = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
    for key in ("https", "http"):
        if parts.get(key):
            return _norm(parts[key])
    if parts.get("socks"):
        return "socks5://" + parts["socks"].split("://")[-1]
    return None


def _windows_registry_proxy() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            enabled = winreg.QueryValueEx(k, "ProxyEnable")[0]
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
    except OSError:
        return None
    return _parse_proxy_server(server) if enabled else None


def system_proxy() -> str | None:
    """Windows 系统代理（手动代理），其次是 HTTPS_PROXY / HTTP_PROXY 环境变量。"""
    p = _windows_registry_proxy()
    if p:
        return p
    env = urllib.request.getproxies_environment()
    for key in ("https", "http", "all"):
        if env.get(key):
            return _norm(env[key])
    return None


def source_uses_proxy(source: dict[str, Any] | None) -> bool:
    if not source:
        return False
    if "use_proxy" in source:
        return bool(source.get("use_proxy"))
    return source.get("type") in DEFAULT_ON_TYPES


def resolve(settings: Settings, source: dict[str, Any] | None = None, *, force: bool = False) -> tuple[str | None, str]:
    """返回 (代理地址, 来源说明)。来源：settings / system / none / off（信源没启用代理）。"""
    if not force and not source_uses_proxy(source):
        return None, "off"
    if settings.fetch.proxy_url:
        return _norm(settings.fetch.proxy_url), "settings"
    if settings.fetch.proxy_use_system:
        p = system_proxy()
        if p:
            return p, "system"
    return None, "none"


def for_playwright(proxy_url: str | None) -> dict[str, str] | None:
    """Playwright 的 proxy 参数：用户名密码要单独传，server 里不能带。"""
    if not proxy_url:
        return None
    u = urlsplit(proxy_url)
    scheme = u.scheme or "http"
    if scheme == "socks5h":
        scheme = "socks5"  # Chromium 只认 socks5（本身就在代理端解析域名）
    server = f"{scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    out = {"server": server}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


def describe(proxy_url: str | None) -> str:
    """展示用：隐藏用户名密码。"""
    if not proxy_url:
        return ""
    u = urlsplit(proxy_url)
    return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
