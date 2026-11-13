"""采集器注册表：信源 type → 采集函数。"""
from .base import FetchContext, Fetcher, FetchError, RawItem
from .browser import fetch_playwright
from .feishu import fetch_feishu
from .github import fetch_github
from .html_list import fetch_html_list
from .json_list import fetch_json
from .mail import fetch_email
from .rss import fetch_rss
from .x import fetch_x

FETCHERS: dict[str, Fetcher] = {
    "rss": fetch_rss,
    "html_list": fetch_html_list,
    "json": fetch_json,
    "github": fetch_github,
    "playwright": fetch_playwright,
    "feishu": fetch_feishu,
    "x": fetch_x,
    "email": fetch_email,
}

__all__ = ["FETCHERS", "FetchContext", "FetchError", "RawItem"]
