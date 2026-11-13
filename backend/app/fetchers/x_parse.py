"""X（Twitter）页面数据解析：不依赖 Playwright，便于用录制 / 构造的样例测试。

数据来自浏览器打开 x.com 搜索页、用户主页时，页面自己请求到的 GraphQL 响应
（SearchTimeline / UserTweets）。这里只解析，不发起任何请求。
解析失败时回退到页面 DOM（article[data-testid="tweet"]）。
"""
from __future__ import annotations

import html as html_mod
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from ..textutil import TZ

# 2026-11 起网页版的 GraphQL 请求从 x.com/i/api/graphql/… 改到了 api.x.com/graphql/…，两种都认
GRAPHQL_RE = re.compile(
    r"^https://(?:[a-z0-9-]+\.)?(?:x|twitter)\.com/(?:i/api/)?graphql/[^/?]+/"
    r"(SearchTimeline|UserTweets|UserTweetsAndReplies|UserByScreenName)(?:[/?#]|$)")
TWITTER_EPOCH_MS = 1288834974657
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass
class Tweet:
    id: str
    text: str
    created_at: datetime | None
    handle: str
    name: str = ""
    avatar: str = ""
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    views: int | None = None
    lang: str = ""
    is_reply: bool = False
    retweeted_by: str = ""  # 非空表示这是转推，原帖作者在 handle
    media: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://x.com/{self.handle or 'i'}/status/{self.id}"


# ---------------------------------------------------------------- 工具
def parse_x_time(value: str | None) -> datetime | None:
    """X 的时间：GraphQL 是 "Wed Nov 11 01:23:45 +0000 2026"，DOM 是 ISO（带 Z）。统一转北京时间。"""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            return datetime.strptime(value, fmt).astimezone(TZ)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # X 给的无时区时间都是 UTC

        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ)


def snowflake_time(tweet_id: str | int | None) -> datetime | None:
    """推文 id 是 Snowflake：高位就是毫秒时间戳。新版页面不再给 <time datetime>，用它还原发帖时间（北京时间）。"""
    try:
        ms = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS
    except (TypeError, ValueError):
        return None
    if ms <= TWITTER_EPOCH_MS:
        return None

    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(TZ)


def parse_count(text: str | int | None) -> int:
    """'1,234' / '1.2K' / '3.4M' / '1.2万' / '12 Likes. Like' → int。"""
    if text is None:
        return 0
    if isinstance(text, int):
        return text
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMm万亿]?)", str(text))
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "万": 1e4, "亿": 1e8}.get(m.group(2).lower(), 1)
    return int(round(num * mult))


def search_url(keyword: str) -> str:
    return f"https://x.com/search?q={quote(keyword.strip(), safe='')}&src=typed_query&f=live"


def normalize_handle(value: str) -> str | None:
    """'@OpenAI' / 'https://x.com/OpenAI' / 'OpenAI' → 'OpenAI'；不合法返回 None。"""
    v = (value or "").strip()
    if v.startswith(("http://", "https://")):
        path = urlsplit(v).path.strip("/")
        v = path.split("/")[0] if path else ""
    v = v.lstrip("@").strip()
    return v if HANDLE_RE.match(v) else None


def user_url(handle: str) -> str:
    return f"https://x.com/{handle}"


def make_title(text: str, limit: int = 70) -> str:
    t = re.sub(r"https?://\S+", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


# ---------------------------------------------------------------- GraphQL
def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _find_instructions(obj: Any, depth: int = 0) -> list[dict[str, Any]] | None:
    """在不同版本的响应里找 timeline.instructions（timeline_v2 / timeline / search_timeline 都有）。"""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        ins = obj.get("instructions")
        if isinstance(ins, list):
            return ins
        for v in obj.values():
            r = _find_instructions(v, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_instructions(v, depth + 1)
            if r is not None:
                return r
    return None


def _entry_item_contents(entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if str(entry.get("entryId", "")).startswith(("promoted", "cursor-", "who-to-follow", "superhero")):
        return
    content = entry.get("content") or {}
    if isinstance(content.get("itemContent"), dict):
        yield content["itemContent"]
    for it in content.get("items") or []:  # TimelineTimelineModule（对话串等）
        ic = _get(it, "item", "itemContent")
        if isinstance(ic, dict):
            yield ic


def _iter_tweet_results(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for ins in _find_instructions(payload) or []:
        entries = list(ins.get("entries") or [])
        if isinstance(ins.get("entry"), dict):  # TimelinePinEntry / TimelineReplaceEntry
            entries.append(ins["entry"])
        for entry in entries:
            for ic in _entry_item_contents(entry):
                if ic.get("promotedMetadata"):  # 广告
                    continue
                res = _get(ic, "tweet_results", "result")
                if isinstance(res, dict):
                    yield res


def _unwrap(res: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(res, dict):
        return None
    if res.get("__typename") == "TweetWithVisibilityResults" or ("tweet" in res and "legacy" not in res):
        res = res.get("tweet")
    if not isinstance(res, dict) or res.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
        return None
    return res if isinstance(res.get("legacy"), dict) else None


def _user(res: dict[str, Any]) -> tuple[str, str, str]:
    u = _get(res, "core", "user_results", "result") or {}
    if u.get("__typename") == "UserUnavailable":
        return "", "", ""
    legacy = u.get("legacy") or {}
    core = u.get("core") or {}
    handle = core.get("screen_name") or legacy.get("screen_name") or ""
    name = core.get("name") or legacy.get("name") or ""
    avatar = _get(u, "avatar", "image_url") or legacy.get("profile_image_url_https") or ""
    return handle, name, avatar


def _expand(text: str, url_entities: Iterable[dict[str, Any]], media: Iterable[dict[str, Any]]) -> str:
    for u in url_entities or []:
        if u.get("url") and u.get("expanded_url"):
            text = text.replace(u["url"], u["expanded_url"])
    for m in media or []:  # 媒体的 t.co 链接只是占位，去掉
        if m.get("url"):
            text = text.replace(m["url"], "")
    return html_mod.unescape(text).strip()


def tweet_from_result(res: dict[str, Any], *, include_retweets: bool = False) -> Tweet | None:
    t = _unwrap(res)
    if t is None:
        return None
    legacy = t["legacy"]
    retweeted_by = ""
    rt = _unwrap(_get(legacy, "retweeted_status_result", "result"))
    if rt is not None:
        if not include_retweets:
            return None
        retweeted_by = _user(t)[0]
        t, legacy = rt, rt["legacy"]
    handle, name, avatar = _user(t)
    note = _get(t, "note_tweet", "note_tweet_results", "result")
    media = list(_get(legacy, "extended_entities", "media") or _get(legacy, "entities", "media") or [])
    if isinstance(note, dict) and note.get("text"):  # 长推文
        text = _expand(note["text"], _get(note, "entity_set", "urls") or [], [])
    else:
        text = _expand(legacy.get("full_text") or legacy.get("text") or "", _get(legacy, "entities", "urls") or [], media)
    tid = str(t.get("rest_id") or legacy.get("id_str") or "")
    if not tid:
        return None
    views = _get(t, "views", "count")
    return Tweet(
        id=tid,
        text=text,
        created_at=parse_x_time(legacy.get("created_at")) or snowflake_time(tid),
        handle=handle,
        name=name,
        avatar=avatar,
        likes=int(legacy.get("favorite_count") or 0),
        retweets=int(legacy.get("retweet_count") or 0),
        replies=int(legacy.get("reply_count") or 0),
        quotes=int(legacy.get("quote_count") or 0),
        views=int(views) if str(views or "").isdigit() else None,
        lang=legacy.get("lang") or "",
        is_reply=bool(legacy.get("in_reply_to_status_id_str")),
        retweeted_by=retweeted_by,
        media=[m.get("media_url_https", "") for m in media if m.get("media_url_https")],
    )


def parse_timeline(payload: dict[str, Any], *, include_retweets: bool = False,
                   include_replies: bool = True) -> list[Tweet]:
    """解析一份 SearchTimeline / UserTweets 响应，按出现顺序去重。"""
    out: dict[str, Tweet] = {}
    for res in _iter_tweet_results(payload):
        tw = tweet_from_result(res, include_retweets=include_retweets)
        if tw is None or tw.id in out:
            continue
        if tw.is_reply and not include_replies:
            continue
        out[tw.id] = tw
    return list(out.values())


# 需要立即停止、不能重试的错误码（GraphQL errors[].code）
_ERROR_KINDS = {
    32: "login", 215: "login", 239: "login",  # 认证失败
    88: "rate_limited", 420: "rate_limited",  # 速率限制
    326: "challenge", 353: "challenge",  # 账号被锁 / 需要验证
    64: "suspended", 63: "suspended",
}


def graphql_error_kind(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for e in payload.get("errors") or []:
        try:
            kind = _ERROR_KINDS.get(int(e.get("code")))
        except (TypeError, ValueError):
            kind = None
        if kind:
            return kind
    return None


# ---------------------------------------------------------------- 页面状态（登录失效 / 验证 / 限制）
BLOCK_MESSAGES = {
    "login": "X 登录已失效：请在信源管理的 X 卡片里点「打开浏览器登录 X」重新登录",
    "challenge": "X 要求人机验证或账号确认：请打开浏览器登录 X，在窗口里手动完成验证",
    "rate_limited": "X 提示请求过于频繁（限流）：今天已停止，明天按计划再抓；如反复出现请减少关键词 / 账号",
    "suspended": "X 账号被限制或冻结：已停止抓取，请在浏览器里查看账号状态",
    "error_page": "X 页面显示“出错了 / 重试”：可能是临时限制，已停止本次抓取",
}


LOGGED_OUT_MARKERS = (
    'data-interaction="mobile-top-bar-log-in"',  # 2026-11 新版页面：未登录时顶部的“登录”按钮
    "/i/jf/onboarding/web?mode=login",
    'data-testid="loginbutton"',  # 旧版页面
)


def detect_block(url: str, html: str = "", statuses: Iterable[int] = ()) -> str | None:
    """根据当前地址、页面内容与 GraphQL 状态码判断是否被拦。返回 BLOCK_MESSAGES 的 key 或 None。"""
    parts = urlsplit(url or "")
    path = parts.path.lower()
    if path.startswith(("/i/flow/login", "/login", "/logout", "/i/flow/signup", "/i/flow/single_sign_on",
                        "/i/jf/onboarding")) or path == "/":
        return "login"  # 未登录：搜索页会被跳到 /i/jf/onboarding/web?mode=login
    if path.startswith(("/account/access", "/i/flow/consent", "/account/login_challenge", "/i/flow/verify")):
        return "challenge"
    if path.startswith(("/account/suspended", "/account/locked")):
        return "suspended"
    statuses = list(statuses)
    if 429 in statuses:
        return "rate_limited"
    if 401 in statuses:
        return "login"
    low = (html or "").lower()
    if any(m.lower() in low.replace("&amp;", "&") for m in LOGGED_OUT_MARKERS):
        return "login"  # 公开页面（如用户主页）未登录也能打开，但页面上会有“登录 / 注册”入口
    if "arkoselabs.com" in low or "funcaptcha" in low or "challenges.cloudflare.com" in low:
        return "challenge"
    if "rate limit exceeded" in low:
        return "rate_limited"
    return None


def looks_like_error_page(html: str) -> bool:
    """X 的“Something went wrong. Try reloading.” / “出错了。请尝试重新加载。”"""
    low = (html or "").lower()
    return ("something went wrong" in low and "try reloading" in low) or ("出错了" in (html or "") and "重新加载" in (html or ""))


# ---------------------------------------------------------------- DOM 回退
_STATUS_HREF = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)/?$")


def _outer_article(node) -> Any:
    p = node.parent
    while p is not None:
        if p.tag == "article":
            return p
        p = p.parent
    return None


def _own(art, nodes):
    """只要属于这条推文本身的节点（排除引用推文里嵌套的 <article>）。"""
    me = art.mem_id  # selectolax 每次访问都会新建 Python 节点对象，不能用 is 比较
    out = []
    for n in nodes:
        if n.mem_id == me:
            out.append(n)
            continue
        outer = _outer_article(n)
        if outer is not None and outer.mem_id == me:
            out.append(n)
    return out


def _count_from(node) -> int:
    if node is None:
        return 0
    label = node.attributes.get("aria-label")
    if label is None:
        inner = node.css_first("[aria-label]")
        label = inner.attributes.get("aria-label") if inner is not None else None
    return parse_count(label or node.text(strip=True))


def parse_dom(html: str) -> list[Tweet]:
    """GraphQL 拿不到时，从渲染后的页面解析推文卡片。

    兼容两种页面：旧版（article[data-testid=tweet]、data-testid=tweetText、<time datetime>）和
    2026-11 的新版（没有 data-testid：<article> + div[dir=auto] 正文 + data-engagement-action 互动按钮，
    时间只显示“11月8日”，用推文 id 还原）。
    """
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(html or "")
    out: dict[str, Tweet] = {}
    for art in tree.css("article"):
        if _outer_article(art) is not None:
            continue  # 引用推文：算在外层推文里
        if art.css_first('[data-testid="placementTracking"]') is not None:
            continue  # 广告（旧版）
        social = art.css_first('[data-testid="socialContext"]')
        social_text = social.text(strip=True) if social is not None else ""
        if social_text and ("reposted" in social_text.lower() or "转帖" in social_text or "转推" in social_text):
            continue  # 转推默认不要（新版主页上的转推由抓取时“作者不是该账号”过滤）
        handle = tid = ""
        time_node = None
        for t in _own(art, art.css("time")):
            a = t.parent
            m = _STATUS_HREF.match((a.attributes.get("href") or "") if a is not None and a.tag == "a" else "")
            if m:
                handle, tid, time_node = m.group(1), m.group(2), t
                break
        if not tid:
            for a in _own(art, art.css('a[href*="/status/"]')):
                m = _STATUS_HREF.match(a.attributes.get("href") or "")
                if m:
                    handle, tid = m.group(1), m.group(2)
                    break
        if not tid:
            continue
        text_node = next(iter(_own(art, art.css('[data-testid="tweetText"]'))), None)
        if text_node is None:
            text_node = next(iter(_own(art, art.css('div[dir="auto"]'))), None)
        text = text_node.text(separator="", strip=True) if text_node is not None else ""
        name = ""
        un = art.css_first('[data-testid="User-Name"]')
        if un is not None:
            first = un.css_first("span")
            name = first.text(strip=True) if first is not None else ""
        else:
            for a in _own(art, art.css(f'a[href="/{handle}"]')):
                t = a.text(strip=True)
                if t and not t.startswith("@"):
                    name = t
                    break
        avatar_img = (art.css_first('[data-testid="Tweet-User-Avatar"] img') or art.css_first("[data-post-avatar] img")
                      or art.css_first(f'img[alt="@{handle}"]'))

        def _cnt(*keys: str, art=art) -> int:  # art 绑定到本轮循环的推文节点
            for k in keys:
                for sel in (f'[data-testid="{k}"]', f'[data-engagement-action="{k}"]'):
                    nodes = _own(art, art.css(sel))
                    if nodes:
                        n = _count_from(nodes[0])
                        if n:
                            return n
            return 0

        created = parse_x_time(time_node.attributes.get("datetime") if time_node is not None else None)
        out[tid] = Tweet(
            id=tid,
            text=text,
            created_at=created or snowflake_time(tid),
            handle=handle,
            name=name,
            avatar=(avatar_img.attributes.get("src") or "") if avatar_img is not None else "",
            likes=_cnt("like", "unlike"),
            retweets=_cnt("retweet", "unretweet"),
            replies=_cnt("reply"),
        )
    return list(out.values())
