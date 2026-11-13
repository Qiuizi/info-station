"""采集器公共部分：RawItem（抓到的原始条目）、FetchContext（带代理的 httpx 客户端）、FetchError、带重试的 get_text。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from ..config import Settings, github_token
from ..textutil import decode_bytes


@dataclass
class RawItem:
    title: str
    url: str
    published_at: datetime | None = None
    summary: str = ""
    content: str = ""
    author: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class FetchError(RuntimeError):
    pass


@dataclass
class FetchContext:
    settings: Settings
    client: httpx.AsyncClient
    proxy_url: str | None = None  # 该信源实际使用的代理（httpx 与 Playwright 共用）
    proxy_from: str = "off"

    @classmethod
    def create(cls, settings: Settings, source: dict[str, Any] | None = None) -> FetchContext:
        from .. import proxy as proxy_mod

        proxy_url, proxy_from = proxy_mod.resolve(settings, source)
        client = httpx.AsyncClient(
            proxy=proxy_url,
            timeout=settings.fetch.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": settings.fetch.user_agent,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        return cls(settings=settings, client=client, proxy_url=proxy_url, proxy_from=proxy_from)

    async def aclose(self) -> None:
        await self.client.aclose()

    def github_headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        tok = github_token(self.settings)
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        return h


class Fetcher(Protocol):
    async def __call__(self, source: dict[str, Any], ctx: FetchContext) -> list[RawItem]: ...


async def get_text(ctx: FetchContext, url: str, retries: int = 2, **kw) -> str:
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            r = await ctx.client.get(url, **kw)
            if r.status_code == 202 and "$_ts" in r.text[:4000]:
                raise FetchError("目标站启用了 JS 反爬（瑞数），请把该信源类型改为 playwright")
            r.raise_for_status()
            return decode_bytes(r.content, r.charset_encoding)
        except FetchError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise FetchError(f"请求失败：{url} · {type(last).__name__}: {last}")
