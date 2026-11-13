"""CSS 选择器抓列表页（借鉴 AIHOT web_list / changedetection 思路，Python 重写）。

list 配置：
  item:    每条的选择器（必填）
  title:   标题选择器，支持 "sel@attr"、"@attr"（取 item 自身属性）、"@text"（item 自身文本），默认 @text
  link:    链接选择器，默认 item 自身（若是 <a>）或其中第一个 a
  date:    日期选择器（字符串或列表，按 年 月 日 顺序拼接后宽松解析）
  summary: 摘要选择器（可选）
  limit:   最多取多少条（默认 20）
detail 配置（可选，抓正文用于截止日期抽取与模型打分）：
  enabled: true
  content: 正文选择器列表（按顺序尝试）
  title:   详情页标题选择器（列表标题被截断时有用）
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from ..textutil import clean_text, parse_date_loose
from .base import FetchContext, FetchError, RawItem, get_text

DEFAULT_CONTENT_SELECTORS = [
    ".v_news_content", "#vsb_content", "#vsb_content_2", ".wp_articlecontent", "article",
    ".article-content", ".article", ".content", "#content", ".main", "main",
]


def _select_value(node: LexborNode, spec: str | None) -> str:
    if not spec or spec == "@text":
        return node.text(separator=" ", strip=True)
    if spec.startswith("@"):
        return node.attributes.get(spec[1:]) or ""
    sel, _, attr = spec.partition("@")
    target = node.css_first(sel)
    if target is None:
        return ""
    if attr:
        return target.attributes.get(attr) or ""
    return target.text(separator=" ", strip=True)


def parse_list(html: str, base_url: str, cfg: dict[str, Any]) -> list[RawItem]:
    tree = LexborHTMLParser(html)
    item_sel = cfg.get("item")
    if not item_sel:
        raise FetchError("html_list 缺少 list.item 选择器")
    out: list[RawItem] = []
    seen: set[str] = set()
    for node in tree.css(item_sel):
        title = clean_text(_select_value(node, cfg.get("title")))
        link_spec = cfg.get("link")
        if link_spec:
            href = _select_value(node, link_spec if "@" in link_spec else f"{link_spec}@href")
        elif node.tag == "a":
            href = node.attributes.get("href") or ""
        else:
            a = node.css_first("a")
            href = (a.attributes.get("href") if a else "") or ""
        if not title or not href or href.startswith("javascript"):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        dates = cfg.get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        date_text = " ".join(_select_value(node, d) for d in dates)
        summary = clean_text(_select_value(node, cfg["summary"])) if cfg.get("summary") else ""
        out.append(RawItem(title=title, url=url, published_at=parse_date_loose(date_text), summary=summary))
        if len(out) >= int(cfg.get("limit", 20)):
            break
    return out


def extract_detail(html: str, cfg: dict[str, Any]) -> tuple[str, str]:
    """返回 (正文, 详情页标题)。"""
    tree = LexborHTMLParser(html)
    for bad in tree.css("script, style, noscript, nav, header, footer"):
        bad.decompose()
    content = ""
    for sel in list(cfg.get("content") or []) + DEFAULT_CONTENT_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            text = clean_text(node.text(separator="\n", strip=True))
            if len(text) > 40:
                content = text
                break
    if not content and tree.body is not None:
        content = clean_text(tree.body.text(separator="\n", strip=True))
    title = ""
    if cfg.get("title"):
        n = tree.css_first(cfg["title"])
        title = clean_text(n.text(strip=True)) if n else ""
    return content[:8000], title


async def fetch_html_list(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    html = await get_text(ctx, source["url"])
    items = parse_list(html, source["url"], source.get("list") or {})
    if not items:
        raise FetchError("列表选择器没有匹配到任何条目（页面结构可能变了）")
    return items


async def fetch_detail_http(ctx: FetchContext, item: RawItem, detail_cfg: dict[str, Any]) -> None:
    try:
        html = await get_text(ctx, item.url, retries=2)
    except FetchError:
        return
    content, title = extract_detail(html, detail_cfg)
    item.content = content
    if title and (item.title.endswith("...") or item.title.endswith("…")):
        item.title = title
