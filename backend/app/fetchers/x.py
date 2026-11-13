"""X（Twitter）信源：用你自己登录过的浏览器，像人一样打开搜索页 / 用户主页并向下滚动几屏。

- 复用 data/browser-profile（和其他“浏览器”信源同一个持久化 profile），你在可见窗口里登录 x.com 一次即可。
- 数据来自页面自己发出的 GraphQL 请求（SearchTimeline / UserTweets）的响应——只“听”，
  不导出 cookie、不自己构造请求；拿不到时回退为解析页面 DOM。
- 防封号：每天一次；关键词 / 账号之间随机等待数秒到数十秒；每页最多滚几屏、取 30 条；单次与每天都有总量上限；
  同一时间只跑一个 X 任务；遇到登录失效、验证、限流页立即停止，不重试，并在界面和通知里提示。
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from .. import config, db
from ..textutil import now, now_iso, sha1
from . import browser
from .base import FetchContext, FetchError, RawItem
from .x_parse import (
    BLOCK_MESSAGES,
    GRAPHQL_RE,
    Tweet,
    detect_block,
    graphql_error_kind,
    looks_like_error_page,
    make_title,
    normalize_handle,
    parse_dom,
    parse_timeline,
    search_url,
    user_url,
)

log = logging.getLogger("x")

STATUS_KEY = "x_status"
DAILY_KEY = "x_daily"
_x_lock = asyncio.Lock()  # 同一时间只开一个 X 任务
FIRST_RESPONSE_TIMEOUT = 20.0  # 打开页面后最多等多久页面自己的第一份 GraphQL 响应


class XBlocked(FetchError):
    """登录失效 / 验证 / 限流：立即停止，不重试。partial 是停止前已经拿到的推文。"""

    def __init__(self, kind: str, partial: list[RawItem] | None = None):
        super().__init__(BLOCK_MESSAGES.get(kind, kind))
        self.kind = kind
        self.partial = partial or []


# ---------------------------------------------------------------- 状态
def get_status() -> dict[str, Any]:
    st = db.get_state(STATUS_KEY, None) or {}
    st.setdefault("state", "unknown")
    st.setdefault("message", "")
    st.setdefault("blocked", False)
    st["running"] = _x_lock.locked()
    daily = db.get_state(DAILY_KEY, None) or {}
    today = now().strftime("%Y-%m-%d")
    st["today_count"] = int(daily.get("count", 0)) if daily.get("date") == today else 0
    return st


def _set_status(**kw: Any) -> dict[str, Any]:
    st = db.get_state(STATUS_KEY, None) or {}
    st.update(kw, updated_at=now_iso())
    db.set_state(STATUS_KEY, st)
    return st


def mark_login_result(last_url: str) -> dict[str, Any]:
    """登录窗口关闭后：停在 /home 等页面视为已登录，清除“已停止”标记。"""
    host = urlsplit(last_url or "").hostname or ""
    if host.endswith(("x.com", "twitter.com")) and detect_block(last_url) is None:
        return _set_status(state="logged_in", blocked=False, message="已登录（关闭窗口时停在 X 页面）")
    return _set_status(state="unknown", blocked=False,
                       message="登录窗口已关闭；未能确认是否登录成功，下次抓取时会自动检查")


def _record_block(kind: str, source: dict[str, Any]) -> None:
    msg = BLOCK_MESSAGES.get(kind, kind)
    _set_status(state=kind, blocked=True, message=msg, blocked_at=now_iso())
    with db.get_conn() as c:  # 无条目通知：前端轮询后弹 Windows 通知
        c.execute("INSERT INTO notifications(item_id, title, body, created_at) VALUES(NULL,?,?,?)",
                  (f"X 抓取已停止 · {source.get('name', source.get('id'))}", msg, now_iso()))


def _add_daily(n: int) -> None:
    today = now().strftime("%Y-%m-%d")
    d = db.get_state(DAILY_KEY, None) or {}
    count = int(d.get("count", 0)) if d.get("date") == today else 0
    db.set_state(DAILY_KEY, {"date": today, "count": count + n})


def clear_block() -> dict[str, Any]:
    return _set_status(blocked=False, state="unknown", message="已手动清除停止标记")


# ---------------------------------------------------------------- 配置
def targets_of(source: dict[str, Any]) -> list[tuple[str, str, str]]:
    """[(kind, 展示名, url)]：先关键词后账号，配置里的顺序。"""
    out: list[tuple[str, str, str]] = []
    for kw in source.get("keywords") or []:
        kw = str(kw).strip()
        if kw:
            out.append(("search", kw, search_url(kw)))
    for acc in source.get("accounts") or []:
        h = normalize_handle(str(acc))
        if h:
            out.append(("user", f"@{h}", user_url(h)))
    return out


def tweet_to_raw(tw: Tweet, target: str) -> RawItem:
    title = make_title(tw.text) or f"@{tw.handle} 的推文（图片 / 视频）"
    head = f"{tw.name} (@{tw.handle})" if tw.name else f"@{tw.handle}"
    return RawItem(
        title=title,
        url=tw.url,
        published_at=tw.created_at,
        content=tw.text,
        author=head,
        extra={
            "kind": "tweet", "tweet_id": tw.id, "dedup_key": f"x:{tw.id}", "handle": tw.handle, "name": tw.name,
            "avatar": tw.avatar, "likes": tw.likes, "retweets": tw.retweets, "replies": tw.replies,
            "quotes": tw.quotes, "views": tw.views, "lang": tw.lang, "media": tw.media[:4],
            "retweeted_by": tw.retweeted_by, "is_reply": tw.is_reply, "query": target,
        },
    )


def dedup_key(tweet_id: str) -> str:
    return sha1(f"x:{tweet_id}")


# ---------------------------------------------------------------- 抓取
async def _sleep(a: float, b: float) -> None:
    await asyncio.sleep(random.uniform(a, b))


async def fetch_x(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    if not browser.available():
        raise FetchError("未安装 Playwright：uv sync --extra browser 后再试")
    targets = targets_of(source)
    if not targets:
        raise FetchError("X 信源没有配置关键词或账号")
    if source.get("_manual") and _x_lock.locked():
        raise FetchError("已有一个 X 任务在运行，请等它结束")
    async with _x_lock:
        st = get_status()
        if st.get("blocked"):  # 上次被拦后不自动重试，等你重新登录 / 确认
            raise FetchError(f"{st.get('message') or 'X 抓取已停止'}（已暂停，登录 X 后自动恢复）")
        xs = ctx.settings.x
        remaining = xs.daily_max - st["today_count"]
        if remaining <= 0:
            raise FetchError(f"今天的 X 抓取量已达上限（{xs.daily_max} 条），明天再抓")
        return await _run(source, ctx, targets, min(xs.run_max, remaining))


def _playwright():
    from playwright.async_api import async_playwright

    return async_playwright()


async def _run(source: dict[str, Any], ctx: FetchContext, targets: list[tuple[str, str, str]], cap: int) -> list[RawItem]:
    xs = ctx.settings.x
    per_target = int(source.get("per_target_max") or xs.per_target_max)
    include_rt = bool(source.get("include_retweets", False))
    include_replies = bool(source.get("include_replies", False))
    known: set[str] = source.get("_known_keys") or set()
    oldest = now() - timedelta(days=int(source.get("max_age_days") or xs.max_age_days))
    results: list[RawItem] = []
    seen_ids: set[str] = set()
    report: dict[str, Any] = {"at": now_iso(), "source_id": source.get("id"), "targets": len(targets),
                              "done_targets": 0, "by_target": {}, "via_dom": 0, "stopped": None,
                              "proxy": ctx.proxy_from}
    buckets: dict[str, list[Tweet]] = {}
    statuses: dict[str, list[int]] = {}
    errors: dict[str, str] = {}
    pending: set[asyncio.Task] = set()
    current = {"t": ""}

    async def on_response(resp) -> None:
        if not GRAPHQL_RE.search(resp.url):
            return
        name = current["t"]
        statuses.setdefault(name, []).append(resp.status)
        if resp.status != 200:
            return
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001
            return
        kind = graphql_error_kind(data)
        if kind:
            errors[name] = kind
        buckets.setdefault(name, []).extend(parse_timeline(data, include_retweets=include_rt,
                                                           include_replies=include_replies))

    def _on(resp) -> None:
        t = asyncio.ensure_future(on_response(resp))
        pending.add(t)
        t.add_done_callback(pending.discard)

    async def _drain() -> None:
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)

    def _stop(kind: str) -> XBlocked:
        report["stopped"] = kind
        _record_block(kind, source)
        _finish(report, results)
        return XBlocked(kind, partial=results)

    try:
        pw_cm = _playwright()
        p = await pw_cm.__aenter__()
    except NotImplementedError as e:
        raise FetchError("当前事件循环不支持子进程：请不要用 --reload 启动后端") from e
    try:
        async with browser._lock:  # 与其他浏览器信源共用 profile，串行
            ctx_b = await browser._launch(p, ctx.settings, xs.headless, ctx.proxy_url)
            try:
                page = ctx_b.pages[0] if ctx_b.pages else await ctx_b.new_page()
                page.on("response", _on)
                for idx, (kind, label, url) in enumerate(targets):
                    if len(results) >= cap:
                        break
                    if idx > 0:  # 关键词 / 账号之间随机等待，不连续翻页
                        await _sleep(xs.min_delay_seconds, xs.max_delay_seconds)
                    current["t"] = label
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:  # noqa: BLE001
                        msg = str(e)
                        if "ERR_PROXY" in msg or "ERR_TUNNEL" in msg or "ERR_CONNECTION" in msg or "Timeout" in msg:
                            raise FetchError(f"打不开 x.com（{'代理 ' + ctx.proxy_url if ctx.proxy_url else '未用代理'}）："
                                             f"请检查代理是否开启 · {msg[:120]}") from e
                        raise
                    got = await _wait_first(page, buckets, statuses, errors, label)
                    block = errors.get(label) or detect_block(page.url, "", statuses.get(label, []))
                    if block:
                        await _drain()
                        raise _stop(block)
                    # 有限滚动：取够 / 没有新内容 / 碰到已经抓过的推文就停
                    for _ in range(xs.max_scrolls):
                        n_before = len(buckets.get(label, []))
                        if n_before >= per_target or any(dedup_key(t.id) in known for t in buckets.get(label, [])):
                            break
                        await page.mouse.wheel(0, random.randint(700, 1500))
                        await _sleep(1.6, 3.8)
                        await _drain()
                        if errors.get(label) or 429 in statuses.get(label, []):
                            raise _stop(errors.get(label) or "rate_limited")
                        if len(buckets.get(label, [])) == n_before:
                            break
                    await _drain()
                    tweets = buckets.get(label, [])
                    if not tweets:
                        html = await page.content()
                        block = detect_block(page.url, html, statuses.get(label, []))
                        if block:
                            raise _stop(block)
                        if looks_like_error_page(html) and not got:
                            raise _stop("error_page")
                        tweets = parse_dom(html)
                        if tweets:
                            report["via_dom"] += 1
                    owner = label[1:].lower() if kind == "user" else ""
                    n = 0
                    for tw in tweets:
                        if n >= per_target or len(results) >= cap:
                            break
                        if tw.id in seen_ids or (tw.created_at and tw.created_at < oldest):
                            continue
                        if tw.retweeted_by and not include_rt:
                            continue
                        if owner and tw.handle.lower() != owner and not include_rt:
                            continue  # 主页上别人的推文 = 转推（新版页面没有“已转帖”标记）
                        seen_ids.add(tw.id)
                        results.append(tweet_to_raw(tw, label))
                        n += 1
                    report["by_target"][label] = n
                    report["done_targets"] += 1
            finally:
                try:
                    await ctx_b.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        await pw_cm.__aexit__(None, None, None)
    _finish(report, results)
    _set_status(state="logged_in", blocked=False, message="")
    return results


async def _wait_first(page, buckets, statuses, errors, label: str, timeout_s: float | None = None) -> bool:
    """等这一页的第一份 GraphQL 响应（或被跳去登录页）；返回是否等到了。"""
    loop = asyncio.get_running_loop()
    end = loop.time() + (FIRST_RESPONSE_TIMEOUT if timeout_s is None else timeout_s)
    while loop.time() < end:
        if statuses.get(label) or errors.get(label) or detect_block(page.url):
            await asyncio.sleep(0.8)  # 让响应解析完
            return bool(statuses.get(label))
        await asyncio.sleep(0.5)
    return False


def _finish(report: dict[str, Any], results: list[RawItem]) -> None:
    report["fetched"] = len(results)
    report["finished_at"] = now_iso()
    _add_daily(len(results))
    _set_status(last_run=report)


def scheduled_skip_reason() -> str | None:
    """定时任务是否应跳过：已被拦（等用户处理）或正在运行。"""
    st = get_status()
    if st.get("blocked"):
        return st.get("message") or "X 抓取已停止"
    if st["running"]:
        return "已有 X 任务在运行"
    return None


def x_sources() -> list[dict[str, Any]]:
    return [s for s in config.load_sources() if s.get("type") == "x"]
