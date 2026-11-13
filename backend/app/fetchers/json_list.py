"""JSON 接口：用点路径把数组映射成条目。

  items_path: "data.list"     # 数组所在路径，空表示根就是数组
  fields: {title: name, url: html_url, date: published_at, summary: body}
  url_template: "https://x.com/{id}"   # 可选，URL 不在字段里时用模板拼
"""
from __future__ import annotations

import json
from typing import Any

from ..textutil import clean_text, parse_date_loose
from .base import FetchContext, FetchError, RawItem, get_text


def dig(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def map_items(data: Any, source: dict[str, Any]) -> list[RawItem]:
    arr = dig(data, source.get("items_path"))
    if not isinstance(arr, list):
        raise FetchError("items_path 没有指向数组")
    f = source.get("fields") or {}
    out = []
    for obj in arr[: int(source.get("limit", 30))]:
        title = clean_text(str(dig(obj, f.get("title", "title")) or ""))
        url = dig(obj, f.get("url", "url"))
        if not url and source.get("url_template"):
            try:
                url = source["url_template"].format(**obj)
            except (KeyError, IndexError):
                url = None
        if not title or not url:
            continue
        summary = clean_text(str(dig(obj, f.get("summary", "summary")) or ""), 2000)
        out.append(RawItem(title=title, url=str(url), published_at=parse_date_loose(str(dig(obj, f.get("date", "date")) or "")), summary=summary[:600], content=summary))
    return out


async def fetch_json(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    text = await get_text(ctx, source["url"], headers=source.get("headers") or {})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError(f"不是合法 JSON：{e}") from e
    return map_items(data, source)
