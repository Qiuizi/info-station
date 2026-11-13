"""X 信源：用构造的、符合公开结构的 GraphQL 响应与页面 DOM 测解析、防封号逻辑与代理。不访问网络、不登录。"""
import json
import os
from datetime import datetime
from pathlib import Path

os.environ["INFO_STATION_NO_SCHEDULER"] = "1"

import pytest

from app import config, db, proxy
from app.fetchers import FetchContext, FetchError, browser
from app.fetchers import x as xmod
from app.fetchers.x_parse import (
    detect_block,
    graphql_error_kind,
    looks_like_error_page,
    make_title,
    normalize_handle,
    parse_count,
    parse_dom,
    parse_timeline,
    parse_x_time,
    search_url,
)
from app.textutil import TZ

FIX = Path(__file__).parent / "fixtures" / "x"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 解析
def test_parse_search_timeline():
    tweets = parse_timeline(load("search_timeline.json"))
    ids = [t.id for t in tweets]
    # 广告（promoted）不要；同一推文只出现一次；TweetWithVisibilityResults 解包
    assert ids == ["1988000000000000001", "1988000000000000002", "1988000000000000003", "1988000000000000004"]
    t1 = tweets[0]
    assert t1.handle == "deepseek_ai" and t1.name == "DeepSeek"
    assert "https://api-docs.deepseek.com/news/v4" in t1.text  # t.co 展开
    assert "t.co" not in t1.text and "&amp;" not in t1.text and "&" in t1.text  # 媒体占位链接去掉、HTML 实体还原
    assert (t1.likes, t1.retweets, t1.quotes, t1.replies, t1.views) == (5200, 1300, 210, 480, 1234)
    assert t1.created_at == datetime(2026, 11, 11, 0, 30, tzinfo=TZ)  # UTC 16:30 → 北京时间次日 00:30
    assert t1.url == "https://x.com/deepseek_ai/status/1988000000000000001"
    assert t1.media == ["https://pbs.twimg.com/media/abc.jpg"]
    assert tweets[1].handle == "someone" and tweets[1].avatar.endswith("someone_normal.jpg")  # 旧版 legacy 用户结构
    assert tweets[3].is_reply
    no_replies = parse_timeline(load("search_timeline.json"), include_replies=False)
    assert "1988000000000000004" not in [t.id for t in no_replies]


def test_parse_user_tweets():
    tweets = parse_timeline(load("user_tweets.json"))
    ids = [t.id for t in tweets]
    # 置顶也解析出来（是否太旧由抓取时的日期窗口决定）；转推默认不要；对话串模块里的推文要；墓碑跳过
    assert ids == ["1700000000000000000", "1988000000000000012", "1988000000000000013"]
    long_t = tweets[1]
    assert long_t.text.startswith("Introducing a new model for students. It is free")  # 长推文用 note_tweet 全文
    with_rt = parse_timeline(load("user_tweets.json"), include_retweets=True)
    rt = next(t for t in with_rt if t.retweeted_by)
    assert rt.id == "1988000000000000010" and rt.handle == "karpathy" and rt.retweeted_by == "OpenAI"


def test_dom_fallback():
    tweets = parse_dom((FIX / "tweet_dom.html").read_text(encoding="utf-8"))
    assert [t.id for t in tweets] == ["1988000000000000001", "1988000000000000002"]  # 转推、广告跳过
    a, b = tweets
    assert a.handle == "deepseek_ai" and a.name == "DeepSeek" and a.text == "DeepSeek-V4 正式发布，开源权重"
    assert (a.likes, a.retweets, a.replies) == (5200, 1300, 480)
    assert a.created_at == datetime(2026, 11, 11, 0, 30, tzinfo=TZ)
    assert a.avatar.endswith("a_normal.jpg")
    assert b.likes == 12000  # “1.2万 喜欢次数”


@pytest.mark.parametrize("text,n", [("1,234", 1234), ("5.2K Likes. Like", 5200), ("3.4M", 3400000), ("1.2万", 12000),
                                    ("", 0), (None, 0), (7, 7)])
def test_parse_count(text, n):
    assert parse_count(text) == n


def test_helpers():
    assert parse_x_time("Wed Nov 11 01:05:00 +0000 2026") == datetime(2026, 11, 11, 9, 5, tzinfo=TZ)
    assert parse_x_time("2026-11-11T01:05:00.000Z") == datetime(2026, 11, 11, 9, 5, tzinfo=TZ)
    assert parse_x_time("bad") is None
    u = search_url("大模型 min_faves:20")
    assert u.startswith("https://x.com/search?q=") and u.endswith("&src=typed_query&f=live") and " " not in u
    assert normalize_handle("@OpenAI") == "OpenAI"
    assert normalize_handle("https://x.com/karpathy/status/1") == "karpathy"
    assert normalize_handle("bad handle") is None and normalize_handle("a" * 16) is None
    assert make_title("  第一行\n第二行 https://t.co/x ") == "第一行 第二行"
    assert len(make_title("字" * 200)) == 70


def test_block_detection():
    assert detect_block("https://x.com/i/flow/login?redirect_after_login=%2Fsearch") == "login"
    assert detect_block("https://x.com/") == "login"
    assert detect_block("https://x.com/account/access") == "challenge"
    assert detect_block("https://x.com/search?q=a", "<iframe src='https://client-api.arkoselabs.com/'>") == "challenge"
    assert detect_block("https://x.com/search?q=a", "", [200, 429]) == "rate_limited"
    assert detect_block("https://x.com/search?q=a", "", [200]) is None
    assert detect_block("https://x.com/OpenAI", "<div data-testid=\"primaryColumn\">") is None
    assert graphql_error_kind(load("rate_limited.json")) == "rate_limited"
    assert graphql_error_kind({"errors": [{"code": 326}]}) == "challenge"
    assert graphql_error_kind(load("search_timeline.json")) is None
    assert looks_like_error_page("<span>Something went wrong. Try reloading.</span>")


# ---------------------------------------------------------------- 代理
def test_proxy_resolution(monkeypatch):
    s = config.Settings()
    assert s.fetch.proxy_url == "http://127.0.0.1:7897"  # 默认 Clash Verge 端口
    assert proxy.resolve(s, {"type": "x"}) == ("http://127.0.0.1:7897", "settings")  # X 默认走代理
    assert proxy.resolve(s, {"type": "rss"}) == (None, "off")  # 其他信源默认不走
    assert proxy.resolve(s, {"type": "rss", "use_proxy": True})[0] == "http://127.0.0.1:7897"
    assert proxy.resolve(s, {"type": "x", "use_proxy": False}) == (None, "off")
    s.fetch.proxy_url = ""
    monkeypatch.setattr(proxy, "system_proxy", lambda: "http://127.0.0.1:10809")
    assert proxy.resolve(s, {"type": "x"}) == ("http://127.0.0.1:10809", "system")
    s.fetch.proxy_use_system = False
    assert proxy.resolve(s, {"type": "x"}) == (None, "none")


@pytest.mark.parametrize("raw,want", [
    ("127.0.0.1:7897", "http://127.0.0.1:7897"),
    ("http=127.0.0.1:7897;https=127.0.0.1:7898", "http://127.0.0.1:7898"),
    ("socks=127.0.0.1:7891", "socks5://127.0.0.1:7891"),
    ("", None),
])
def test_parse_windows_proxy_server(raw, want):
    assert proxy._parse_proxy_server(raw) == want


def test_system_proxy_env(monkeypatch):
    monkeypatch.setattr(proxy, "_windows_registry_proxy", lambda: None)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    assert proxy.system_proxy() == "http://127.0.0.1:7897"


def test_playwright_proxy_and_validation():
    assert proxy.for_playwright("http://u%40x:p@127.0.0.1:7897") == {
        "server": "http://127.0.0.1:7897", "username": "u@x", "password": "p"}
    assert proxy.for_playwright("socks5h://127.0.0.1:7891") == {"server": "socks5://127.0.0.1:7891"}
    assert proxy.for_playwright(None) is None
    assert proxy.describe("http://u:p@127.0.0.1:7897") == "http://127.0.0.1:7897"
    with pytest.raises(ValueError):
        config.FetchSettings(proxy_url="127.0.0.1 7897")
    with pytest.raises(ValueError):
        config.XSettings(min_delay_seconds=50, max_delay_seconds=10)
    with pytest.raises(ValueError):
        config.XSettings(time="25:00")


async def test_httpx_client_uses_proxy(tmp_env):
    ctx = FetchContext.create(tmp_env, {"type": "x"})
    try:
        assert ctx.proxy_url == "http://127.0.0.1:7897" and ctx.proxy_from == "settings"
        pools = ctx.client._mounts  # httpx 按代理挂载 transport
        assert any(getattr(t, "_pool", None).__class__.__name__ == "AsyncHTTPProxy" for t in pools.values())
    finally:
        await ctx.aclose()
    ctx = FetchContext.create(tmp_env, {"type": "rss"})
    assert ctx.proxy_url is None
    await ctx.aclose()


# ---------------------------------------------------------------- 假浏览器：跑完整的抓取流程
class FakeResponse:
    def __init__(self, url, status, data):
        self.url, self.status, self._data = url, status, data

    async def json(self):
        return self._data


class FakeMouse:
    def __init__(self, page):
        self.page = page

    async def wheel(self, dx, dy):
        self.page.scrolls += 1


class FakePage:
    """routes: url 片段 → (跳转后的地址, [(GraphQL 操作名, 状态码, 数据)], 页面 HTML)"""

    def __init__(self, routes):
        self.routes, self.url, self.handlers, self.scrolls, self.html = routes, "about:blank", [], 0, ""
        self.mouse = FakeMouse(self)
        self.visited = []

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    async def goto(self, url, **kw):
        self.visited.append(url)
        for frag, (landing, responses, html) in self.routes.items():
            if frag in url:
                self.url, self.html = landing or url, html
                for op, status, data in responses:
                    for h in self.handlers:
                        h(FakeResponse(f"https://x.com/i/api/graphql/AbC123/{op}?variables=%7B%7D", status, data))
                return
        raise AssertionError(url)

    async def content(self):
        return self.html


class FakeContext:
    def __init__(self, page):
        self.pages, self.closed = [page], False

    async def close(self):
        self.closed = True


class FakePW:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return None


@pytest.fixture()
def fake_browser(tmp_env, monkeypatch):
    holder = {}

    def install(routes):
        page = FakePage(routes)
        holder["page"] = page
        holder["ctx"] = FakeContext(page)

        async def _launch(p, settings, headless, proxy_url=None):
            holder["launch"] = {"headless": headless, "proxy": proxy_url}
            return holder["ctx"]

        monkeypatch.setattr(browser, "_launch", _launch)
        return page

    async def _nosleep(a, b):
        holder.setdefault("sleeps", []).append((a, b))

    monkeypatch.setattr(browser, "available", lambda: True)
    monkeypatch.setattr(xmod, "_playwright", lambda: FakePW())
    monkeypatch.setattr(xmod, "_sleep", _nosleep)
    monkeypatch.setattr(xmod, "now", lambda: NOW)
    monkeypatch.setattr(xmod, "FIRST_RESPONSE_TIMEOUT", 0.3)
    db.init_db()
    holder["install"] = install
    return holder


SRC = {"id": "x-test", "name": "X 测试", "type": "x", "category": "news", "tier": "T2",
       "keywords": ["DeepSeek"], "accounts": ["OpenAI"]}


async def test_fetch_x_success(fake_browser, tmp_env):
    fake_browser["install"]({
        "search?q=DeepSeek": (None, [("SearchTimeline", 200, load("search_timeline.json"))], ""),
        "x.com/OpenAI": (None, [("UserTweets", 200, load("user_tweets.json"))], ""),
    })
    ctx = FetchContext.create(tmp_env, SRC)
    items = await xmod.fetch_x(dict(SRC), ctx)
    await ctx.aclose()
    ids = [it.extra["tweet_id"] for it in items]
    # 搜索：回复不要；主页：太旧的置顶不要、转推不要
    assert ids == ["1988000000000000001", "1988000000000000002", "1988000000000000003",
                   "1988000000000000012", "1988000000000000013"]
    first = items[0]
    assert first.author == "DeepSeek (@deepseek_ai)" and first.extra["likes"] == 5200
    assert first.extra["dedup_key"] == "x:1988000000000000001" and first.extra["query"] == "DeepSeek"
    assert first.published_at == datetime(2026, 11, 11, 0, 30, tzinfo=TZ)
    page = fake_browser["page"]
    assert page.visited[0].endswith("f=live") and page.visited[1] == "https://x.com/OpenAI"
    assert fake_browser["launch"] == {"headless": True, "proxy": "http://127.0.0.1:7897"}
    assert fake_browser["ctx"].closed
    assert (8, 40) in fake_browser["sleeps"]  # 关键词 / 账号之间随机等待 8–40 秒
    assert page.scrolls <= 2 * tmp_env.x.max_scrolls
    st = xmod.get_status()
    assert st["state"] == "logged_in" and not st["blocked"] and st["today_count"] == 5
    assert st["last_run"]["by_target"] == {"DeepSeek": 3, "@OpenAI": 2} and st["last_run"]["stopped"] is None


async def test_fetch_x_caps(fake_browser, tmp_env):
    fake_browser["install"]({
        "search?q=DeepSeek": (None, [("SearchTimeline", 200, load("search_timeline.json"))], ""),
        "x.com/OpenAI": (None, [("UserTweets", 200, load("user_tweets.json"))], ""),
    })
    src = {**SRC, "per_target_max": 2}
    ctx = FetchContext.create(tmp_env, src)
    assert len(await xmod.fetch_x(dict(src), ctx)) == 4  # 每个目标最多 2 条
    tmp_env.x.run_max = 3
    assert len(await xmod.fetch_x(dict(SRC), ctx)) == 3  # 单次总量上限
    tmp_env.x.daily_max = 9  # 今天已经 7 条，只剩 2 条额度
    assert len(await xmod.fetch_x(dict(SRC), ctx)) == 2
    with pytest.raises(FetchError, match="上限"):
        await xmod.fetch_x(dict(SRC), ctx)
    await ctx.aclose()


async def test_fetch_x_login_required_stops_and_no_retry(fake_browser, tmp_env):
    page = fake_browser["install"]({
        "search?q=DeepSeek": ("https://x.com/i/flow/login?redirect_after_login=%2Fsearch", [], "<html></html>"),
        "x.com/OpenAI": (None, [("UserTweets", 200, load("user_tweets.json"))], ""),
    })
    ctx = FetchContext.create(tmp_env, SRC)
    with pytest.raises(xmod.XBlocked) as ei:
        await xmod.fetch_x(dict(SRC), ctx)
    assert ei.value.kind == "login" and ei.value.partial == []
    assert len(page.visited) == 1  # 立即停止，不再打开后面的账号
    st = xmod.get_status()
    assert st["blocked"] and st["state"] == "login" and "重新登录" in st["message"]
    with db.get_conn() as c:
        n = c.execute("SELECT title, body FROM notifications WHERE item_id IS NULL").fetchall()
    assert len(n) == 1 and "X 抓取已停止" in n[0][0]
    # 不重试：定时任务跳过，手动抓取也先要求重新登录
    assert xmod.scheduled_skip_reason()
    with pytest.raises(FetchError, match="已暂停"):
        await xmod.fetch_x(dict(SRC), ctx)
    assert len(page.visited) == 1
    # 登录窗口关闭时停在 /home → 恢复
    xmod.mark_login_result("https://x.com/home")
    st = xmod.get_status()
    assert st["state"] == "logged_in" and not st["blocked"] and xmod.scheduled_skip_reason() is None
    xmod.mark_login_result("https://x.com/i/flow/login")
    assert xmod.get_status()["state"] == "unknown"
    await ctx.aclose()


async def test_fetch_x_rate_limit_keeps_partial(fake_browser, tmp_env, monkeypatch):
    fake_browser["install"]({
        "search?q=DeepSeek": (None, [("SearchTimeline", 200, load("search_timeline.json"))], ""),
        "x.com/OpenAI": (None, [("UserTweets", 429, {})], ""),
    })
    from app.pipeline import runner

    monkeypatch.setattr(config, "get_source", lambda sid: dict(SRC) if sid == SRC["id"] else None)
    monkeypatch.setattr(config, "load_sources", lambda: [dict(SRC)])
    r = await runner.run_source(SRC["id"], manual=True)
    assert not r["ok"] and "限流" in r["error"] and r["new"] == 3  # 已拿到的照常入库
    with db.get_conn() as c:
        st = c.execute("SELECT last_error, last_new FROM source_state WHERE source_id=?", (SRC["id"],)).fetchone()
        rows = c.execute("SELECT author, extra, category, url_key FROM items ORDER BY id").fetchall()
    assert "限流" in st[0] and st[1] == 3
    assert rows[0][0] == "DeepSeek (@deepseek_ai)" and rows[0][2] == "news"
    assert json.loads(rows[0][1])["retweets"] == 1300
    assert xmod.get_status()["state"] == "rate_limited" and xmod.get_status()["blocked"]


async def test_fetch_x_graphql_error_and_dom_fallback(fake_browser, tmp_env):
    fake_browser["install"]({
        "search?q=DeepSeek": (None, [], (FIX / "tweet_dom.html").read_text(encoding="utf-8")),
        "x.com/OpenAI": (None, [("UserTweets", 200, load("rate_limited.json"))], ""),
    })
    ctx = FetchContext.create(tmp_env, SRC)
    with pytest.raises(xmod.XBlocked) as ei:
        await xmod.fetch_x(dict(SRC), ctx)
    await ctx.aclose()
    assert ei.value.kind == "rate_limited"  # GraphQL errors code 88
    assert [i.extra["tweet_id"] for i in ei.value.partial] == ["1988000000000000001", "1988000000000000002"]
    assert xmod.get_status()["last_run"]["via_dom"] == 1


async def test_only_one_x_task(fake_browser, tmp_env):
    fake_browser["install"]({})
    ctx = FetchContext.create(tmp_env, SRC)
    async with xmod._x_lock:
        assert xmod.get_status()["running"]
        with pytest.raises(FetchError, match="已有一个 X 任务"):
            await xmod.fetch_x({**SRC, "_manual": True}, ctx)
    await ctx.aclose()


# ---------------------------------------------------------------- 入库：按推文 id 去重、互动数作规则分信号
def test_insert_dedup_by_tweet_id_and_engagement(tmp_env, monkeypatch):
    from app.fetchers.x_parse import Tweet
    from app.pipeline import runner
    from app.queries import get_item

    monkeypatch.setattr(config, "load_sources", lambda: [dict(SRC)])
    db.init_db()
    hot = Tweet(id="111", text="DeepSeek 发布新模型", created_at=NOW, handle="deepseek_ai", name="DeepSeek",
                likes=5000, retweets=1000)
    cold = Tweet(id="222", text="DeepSeek 发布新模型", created_at=NOW, handle="someone", likes=1)
    a = xmod.tweet_to_raw(hot, "DeepSeek")
    b = xmod.tweet_to_raw(hot, "@deepseek_ai")
    b.url = "https://twitter.com/DeepSeek_AI/status/111?s=20"  # 同一推文换了个链接
    ids = runner.insert_items(SRC, [a, b, xmod.tweet_to_raw(cold, "DeepSeek")], tmp_env, first_run=False)
    assert len(ids) == 2
    assert runner.insert_items(SRC, [xmod.tweet_to_raw(hot, "x")], tmp_env, first_run=False) == []
    runner.apply_rules_to(ids, tmp_env, notify=False)
    hot_item, cold_item = get_item(ids[0]), get_item(ids[1])
    assert hot_item["extra"]["likes"] == 5000 and hot_item["extra"]["handle"] == "deepseek_ai"
    assert hot_item["rule_score"] - cold_item["rule_score"] == 10  # 互动 5000+3×1000=8000 → +10
    assert any("互动" in h for h in hot_item["reason"].split("；"))
    assert hot_item["rule_score"] <= 75


def test_old_items_have_empty_extra(tmp_env):
    from app.fetchers import RawItem
    from app.pipeline import runner
    from app.queries import get_item

    db.init_db()
    ids = runner.insert_items({"id": "rss1", "category": "tech"}, [RawItem(title="t", url="https://e.com/1")],
                              tmp_env, first_run=False)
    assert get_item(ids[0])["extra"] == {}


# ---------------------------------------------------------------- 调度：每天一次，早于打分；补抓
def test_x_schedule(tmp_env, monkeypatch):
    from app import scheduler

    assert tmp_env.x.time == "07:00" and tmp_env.x.time < tmp_env.scoring.time
    assert scheduler._x_time(tmp_env, 0) == (7, 0) and scheduler._x_time(tmp_env, 2) == (7, 40)
    db.init_db()
    monkeypatch.setattr(scheduler, "now", lambda: datetime(2026, 11, 11, 6, 0, tzinfo=TZ))
    assert not scheduler.x_catch_up_due("x-test", tmp_env)  # 还没到点
    monkeypatch.setattr(scheduler, "now", lambda: datetime(2026, 11, 11, 9, 0, tzinfo=TZ))
    assert scheduler.x_catch_up_due("x-test", tmp_env)  # 过了点、今天没跑 → 补抓
    with db.get_conn() as c:
        c.execute("INSERT INTO source_state(source_id, last_run) VALUES('x-test', '2026-11-11T07:01:00+08:00')")
    assert not scheduler.x_catch_up_due("x-test", tmp_env)  # 今天跑过了
    with db.get_conn() as c:
        c.execute("UPDATE source_state SET last_run='2026-11-10T07:01:00+08:00'")
    xmod._set_status(blocked=True, state="login", message="登录失效")
    assert not scheduler.x_catch_up_due("x-test", tmp_env)  # 被拦过：不自动重试


def test_sync_jobs_x_is_daily_cron(tmp_env, monkeypatch):
    from apscheduler.triggers.cron import CronTrigger

    from app import scheduler

    db.init_db()
    monkeypatch.setattr(config, "load_sources", lambda: [{**SRC, "enabled": True},
                                                         {"id": "r", "type": "rss", "enabled": True, "url": "u"}])
    monkeypatch.setattr(scheduler, "now", lambda: datetime(2026, 11, 11, 6, 0, tzinfo=TZ))
    try:
        scheduler.sync_jobs()
        job = scheduler.scheduler.get_job("source:x-test")
        assert isinstance(job.trigger, CronTrigger)
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "7" and fields["minute"] == "0"
        assert job.func is scheduler.run_x_scheduled
        assert scheduler.scheduler.get_job("xcatch:x-test") is None
    finally:
        for j in scheduler.scheduler.get_jobs():
            j.remove()


async def test_scheduled_run_skipped_when_blocked(tmp_env, monkeypatch):
    from app import scheduler

    db.init_db()
    called = []

    async def fake_run(sid, **kw):
        called.append(sid)

    monkeypatch.setattr(scheduler, "run_source", fake_run)
    xmod._set_status(blocked=True, state="challenge", message="需要验证")
    await scheduler.run_x_scheduled("x-test")
    assert called == []
    xmod.clear_block()
    await scheduler.run_x_scheduled("x-test")
    assert called == ["x-test"]


# ---------------------------------------------------------------- API
def test_x_api(tmp_env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    with TestClient(app) as c:
        bad = c.post("/api/sources", json={"id": "x1", "name": "X", "type": "x", "category": "news",
                                           "extra": {"keywords": [], "accounts": ["not valid!"]}})
        assert bad.status_code == 400 and "账号格式" in bad.json()["detail"]
        assert c.post("/api/sources", json={"id": "x1", "name": "X", "type": "x", "category": "news",
                                            "extra": {}}).status_code == 400
        r = c.post("/api/sources", json={"id": "x1", "name": "X", "type": "x", "category": "news", "enabled": False,
                                         "extra": {"keywords": ["LLM", " LLM ", ""], "accounts": ["@OpenAI", "https://x.com/karpathy"]}})
        assert r.status_code == 200
        src = next(s for s in c.get("/api/sources").json() if s["id"] == "x1")
        assert src["extra"]["keywords"] == ["LLM"] and src["extra"]["accounts"] == ["OpenAI", "karpathy"]
        assert src["extra"]["use_proxy"] is True and src["url"] is None
        # 试抓不打开 x.com，只列出要访问的页面
        pv = c.post("/api/sources/preview", json={"id": "x1", "name": "X", "type": "x", "extra": src["extra"]}).json()
        assert pv["ok"] and pv["preview_only"] and pv["count"] == 3
        st = c.get("/api/x/status").json()
        assert st["state"] == "unknown" and st["proxy"] == "http://127.0.0.1:7897" and st["time"] == "07:00"
        assert st["sources"][0]["keywords"] == 1 and st["sources"][0]["accounts"] == 2
        s = c.put("/api/settings", json={"fetch": {"proxy_url": "http://127.0.0.1:7890", "system_proxy": "x"},
                                         "x": {"time": "06:30"}}).json()
        assert s["fetch"]["proxy_url"] == "http://127.0.0.1:7890" and s["x"]["time"] == "06:30"
        assert c.put("/api/settings", json={"fetch": {"proxy_url": "not a proxy"}}).status_code == 422
        xmod._set_status(blocked=True, state="login", message="m")
        assert not c.post("/api/x/clear-block").json()["blocked"]
        # “全部抓取”不包含 X
        called = []

        async def fake_run(sid, **kw):
            called.append(sid)
            return {"ok": True, "new": 0}

        import app.main as main_mod

        monkeypatch.setattr(main_mod, "run_source", fake_run)
        c.put("/api/sources/x1", json={**{k: src[k] for k in ("id", "name", "type", "category")}, "enabled": True,
                                       "extra": src["extra"]})
        c.post("/api/sources/run-all")
        assert "x1" not in called
