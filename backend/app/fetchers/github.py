"""GitHub 信源。

mode:
  releases  repo: owner/name           仓库 Release（官方 API）
  search    query: "topic:llm stars:>500 pushed:>{days_ago:7}"   仓库搜索（按 stars 排序），近似“趋势”
  trending  language: python / since: daily|weekly    抓 github.com/trending（非官方页面，允许失败）
可选 token：设置里填 GitHub Token 或环境变量 GITHUB_TOKEN，提高限流额度。
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from ..textutil import clean_text, now, parse_date_loose
from .base import FetchContext, FetchError, RawItem

API = "https://api.github.com"


def _expand(query: str) -> str:
    return re.sub(r"\{days_ago:(\d+)\}", lambda m: (now() - timedelta(days=int(m[1]))).strftime("%Y-%m-%d"), query)


async def _api(ctx: FetchContext, path: str, params: dict | None = None) -> Any:
    r = await ctx.client.get(API + path, params=params, headers=ctx.github_headers())
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise FetchError("GitHub API 触发限流，请在设置里填写 GitHub Token")
    if r.status_code >= 400:
        raise FetchError(f"GitHub API {r.status_code}: {r.text[:200]}")
    return r.json()


async def fetch_github(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    mode = source.get("mode", "releases")
    limit = int(source.get("limit", 10))
    if mode == "releases":
        repo = source["repo"]
        data = await _api(ctx, f"/repos/{repo}/releases", {"per_page": limit})
        return [
            RawItem(
                title=f"{repo} 发布 {r.get('name') or r.get('tag_name')}",
                url=r["html_url"],
                published_at=parse_date_loose(r.get("published_at") or r.get("created_at")),
                summary=clean_text(r.get("body") or "", 400),
                content=clean_text(r.get("body") or "", 6000),
                author=(r.get("author") or {}).get("login", ""),
                extra={"prerelease": r.get("prerelease")},
            )
            for r in data
            if not r.get("draft")
        ]
    if mode == "search":
        q = _expand(source["query"])
        data = await _api(ctx, "/search/repositories", {"q": q, "sort": source.get("sort", "stars"), "order": "desc", "per_page": limit})
        out = []
        for r in data.get("items", []):
            desc = r.get("description") or ""
            out.append(
                RawItem(
                    title=f"{r['full_name']} · ★{r['stargazers_count']:,}",
                    url=r["html_url"],
                    published_at=parse_date_loose(r.get("created_at")),
                    summary=clean_text(desc, 300),
                    content=f"{desc}\n语言：{r.get('language')}；Stars：{r['stargazers_count']}；Topics：{', '.join(r.get('topics') or [])}",
                    author=(r.get("owner") or {}).get("login", ""),
                )
            )
        return out
    if mode == "trending":
        lang = source.get("language", "")
        url = f"https://github.com/trending/{lang}?since={source.get('since', 'daily')}"
        r = await ctx.client.get(url)
        if r.status_code >= 400:
            raise FetchError(f"Trending 页面 {r.status_code}")
        tree = LexborHTMLParser(r.text)
        out = []
        for row in tree.css("article.Box-row")[:limit]:
            a = row.css_first("h2 a")
            if a is None:
                continue
            name = re.sub(r"\s+", "", a.text())
            desc_node = row.css_first("p")
            desc = clean_text(desc_node.text()) if desc_node else ""
            gained = row.css_first("span.d-inline-block.float-sm-right")
            gain = clean_text(gained.text()) if gained else ""
            out.append(
                RawItem(
                    title=f"{name} 上榜 GitHub Trending" + (f"（{gain}）" if gain else ""),
                    url="https://github.com" + (a.attributes.get("href") or ""),
                    published_at=now().replace(hour=0, minute=0, second=0, microsecond=0),
                    summary=desc,
                    content=desc,
                )
            )
        if not out:
            raise FetchError("Trending 页面结构变化，未解析到仓库")
        return out
    raise FetchError(f"未知 github mode: {mode}")
