"""RSS / Atom 采集（feedparser），支持 {RSSHUB} 占位。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser

from ..textutil import TZ, clean_text, parse_date_loose
from .base import FetchContext, FetchError, RawItem, get_text


def _strip_html(html: str) -> str:
    from selectolax.lexbor import LexborHTMLParser

    if not html:
        return ""
    try:
        return clean_text(LexborHTMLParser(html).text(separator="\n", strip=True))
    except Exception:  # noqa: BLE001
        return clean_text(html)


def resolve_url(url: str, ctx: FetchContext) -> str:
    return url.replace("{RSSHUB}", ctx.settings.fetch.rsshub_base.rstrip("/"))


def parse_feed(text: str, limit: int = 30) -> list[RawItem]:
    feed = feedparser.parse(text)
    if feed.bozo and not feed.entries:
        raise FetchError(f"RSS 解析失败：{feed.bozo_exception}")
    out = []
    for e in feed.entries[:limit]:
        dt = None
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                dt = datetime(*e[key][:6], tzinfo=__import__("datetime").timezone.utc).astimezone(TZ)
                break
        if dt is None:
            dt = parse_date_loose(e.get("published") or e.get("updated"))
        body = ""
        if e.get("content"):
            body = e.content[0].get("value", "")
        summary_html = e.get("summary", "")
        out.append(
            RawItem(
                title=clean_text(e.get("title", "")) or "(无标题)",
                url=e.get("link", ""),
                published_at=dt,
                summary=_strip_html(summary_html)[:600],
                content=_strip_html(body or summary_html)[:8000],
                author=e.get("author", ""),
            )
        )
    return [i for i in out if i.url]


async def fetch_rss(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    text = await get_text(ctx, resolve_url(source["url"], ctx))
    return parse_feed(text, int(source.get("limit", 30)))
