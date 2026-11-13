"""X 新版网页（2026-11）——GraphQL 换到 api.x.com、页面去掉 data-testid、未登录识别、UA 与 Client Hints 对齐。"""
import os
from datetime import datetime
from pathlib import Path

os.environ["INFO_STATION_NO_SCHEDULER"] = "1"

import pytest
from test_x import NOW, SRC, fake_browser, load  # noqa: F401  复用假浏览器

from app import config, db
from app.fetchers import FetchContext, browser
from app.fetchers import x as xmod
from app.fetchers.x_parse import GRAPHQL_RE, detect_block, parse_dom, snowflake_time
from app.textutil import TZ

FIX = Path(__file__).parent / "fixtures" / "x"
# 用户 Windows 上用同一个 profile 打开 x.com/karpathy 保存下来的页面（去掉了样式 / 脚本）：未登录状态的新版页面
PROFILE_HTML = (FIX / "profile_2026_logged_out.html").read_text(encoding="utf-8")
LOGGED_IN_LIKE = (PROFILE_HTML.replace('data-interaction="mobile-top-bar-log-in"', "")
                  .replace("/i/jf/onboarding/web?mode=login", "/home"))


@pytest.mark.parametrize("url,op", [
    ("https://api.x.com/graphql/TFR-aLDwrWbXeo5hn9UKfQ/SearchTimeline?variables=%7B%7D", "SearchTimeline"),
    ("https://api.x.com/graphql/abc/UserTweets", "UserTweets"),
    ("https://x.com/i/api/graphql/abc/UserTweets?variables=1", "UserTweets"),
    ("https://twitter.com/i/api/graphql/abc/SearchTimeline", "SearchTimeline"),
])
def test_graphql_url_new_and_old_host(url, op):
    m = GRAPHQL_RE.search(url)
    assert m and m.group(1) == op


@pytest.mark.parametrize("url", [
    "https://api.x.com/graphql/TFR-aLDwrWbXeo5hn9UKfQ/UserSportsProfile",
    "https://api.x.com/graphql/abc/UserTweetsXYZ",
    "https://evil.example/x.com/graphql/abc/SearchTimeline",
])
def test_graphql_url_rejects_others(url):
    assert GRAPHQL_RE.search(url) is None


def test_logged_out_detection():
    # 未登录打开搜索页会被跳到新的登录引导页（以前没认出来，被当成“0 条、正常结束”）
    assert detect_block("https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fsearch%3Fq%3DLLM&mode=login") == "login"
    # 用户主页未登录也能打开，但页面上有“登录 / 注册”入口
    assert detect_block("https://x.com/karpathy", PROFILE_HTML) == "login"
    assert detect_block("https://x.com/karpathy", LOGGED_IN_LIKE) is None
    st = xmod.mark_login_result("https://x.com/i/jf/onboarding/web?mode=login")
    assert st["state"] == "unknown"


def test_snowflake_time():
    assert snowflake_time("2119114537442189652") == datetime(2026, 11, 8, 1, 7, 31, 627000, tzinfo=TZ)
    assert snowflake_time("abc") is None and snowflake_time("0") is None


def test_parse_new_markup():
    tweets = parse_dom(PROFILE_HTML)
    assert [t.id for t in tweets] == ["2119114537442189652", "2116957135661945283", "2109361546505966046",
                                      "2105909609487872075", "2105819303471976479"]  # 引用推文不单独算
    t = tweets[0]
    assert t.handle == "karpathy" and t.name == "Andrej Karpathy"
    assert t.text.startswith("Earlier this week Tristan published his top YouTube channels")
    assert (t.likes, t.retweets, t.replies) == (9215, 1009, 657)  # “9215 次点赞。点赞”
    assert t.created_at == datetime(2026, 11, 8, 1, 7, 31, 627000, tzinfo=TZ)  # 页面只写“11月8日”，用推文 id 还原
    assert t.avatar.endswith("_normal.jpg")
    assert "Cool eval" in tweets[3].text and "celestepoasts" not in tweets[3].handle


async def test_logged_out_profile_stops_immediately(fake_browser, tmp_env):  # noqa: F811
    page = fake_browser["install"]({
        "x.com/karpathy": (None, [("UserSportsProfile", 200, {"data": {}})], PROFILE_HTML),
        "x.com/OpenAI": (None, [], LOGGED_IN_LIKE),
    })
    src = {**SRC, "keywords": [], "accounts": ["karpathy", "OpenAI"]}
    ctx = FetchContext.create(tmp_env, src)
    with pytest.raises(xmod.XBlocked) as ei:
        await xmod.fetch_x(dict(src), ctx)
    await ctx.aclose()
    assert ei.value.kind == "login" and len(page.visited) == 1
    st = xmod.get_status()
    assert st["blocked"] and st["state"] == "login"


async def test_new_markup_dom_fallback_when_logged_in(fake_browser, tmp_env):  # noqa: F811
    fake_browser["install"]({"x.com/karpathy": (None, [], LOGGED_IN_LIKE)})
    src = {**SRC, "keywords": [], "accounts": ["karpathy"]}
    ctx = FetchContext.create(tmp_env, src)
    assert await xmod.fetch_x(dict(src), ctx) == []  # 默认只要 3 天内：karpathy 最新一条是 11-08 01:07
    tmp_env.x.max_age_days = 10
    items = await xmod.fetch_x(dict(src), ctx)
    await ctx.aclose()
    assert [i.extra["tweet_id"] for i in items] == ["2119114537442189652", "2116957135661945283"]
    assert items[0].extra["likes"] == 9215 and items[0].author == "Andrej Karpathy (@karpathy)"
    st = xmod.get_status()
    assert st["state"] == "logged_in" and st["last_run"]["via_dom"] == 1


async def test_profile_skips_other_authors(fake_browser, tmp_env):  # noqa: F811
    html = LOGGED_IN_LIKE.replace("/karpathy/status/2116957135661945283", "/someone/status/2116957135661945283")
    fake_browser["install"]({"x.com/karpathy": (None, [], html)})
    tmp_env.x.max_age_days = 10
    src = {**SRC, "keywords": [], "accounts": ["karpathy"]}
    ctx = FetchContext.create(tmp_env, src)
    items = await xmod.fetch_x(dict(src), ctx)
    await ctx.aclose()
    assert [i.extra["handle"] for i in items] == ["karpathy"]  # 主页上别人的推文按转推处理，默认不要


def test_user_agent_matches_real_browser(tmp_path):
    s = config.Settings()
    s.fetch.browser_channel = "chrome"
    assert browser.user_agent_for(s, headless=False, profile_dir=tmp_path) is None  # 版本未知：用浏览器自己的 UA
    assert browser.user_agent_for(s, headless=True, profile_dir=tmp_path) == s.fetch.user_agent
    (tmp_path / "Last Version").write_text("156.0.8078.80", encoding="utf-8")
    ua = browser.user_agent_for(s, headless=True, profile_dir=tmp_path)
    assert "Chrome/156.0.0.0" in ua and "Headless" not in ua and "Chrome/130" not in ua
    s.fetch.browser_channel = "msedge"
    assert browser.user_agent_for(s, headless=True, profile_dir=tmp_path).endswith("Edg/156.0.8078.80")
    s.fetch.browser_channel = "chromium"
    assert browser.user_agent_for(s, headless=True, profile_dir=tmp_path) == s.fetch.user_agent


def test_json_responses_declare_utf8(tmp_env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "sources.yaml").write_text(
        "sources:\n- id: x-ai\n  name: X · AI 大模型（示例，确认后再启用）\n  type: x\n  keywords: [大模型]\n",
        encoding="utf-8")
    db.init_db()
    with TestClient(app) as c:
        r = c.get("/api/sources")
        assert r.headers["content-type"] == "application/json; charset=utf-8"
        assert r.content.decode("utf-8").count("X · AI 大模型（示例，确认后再启用）") == 1
        assert r.json()[0]["name"] == "X · AI 大模型（示例，确认后再启用）"
