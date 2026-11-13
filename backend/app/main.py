"""FastAPI 入口：只监听 127.0.0.1；可选本机 token（环境变量 INFO_STATION_TOKEN）。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import __version__, brief, config, db, queries, scheduler
from .config import CATEGORIES, SOURCE_TYPES, Settings, deepseek_key, load_settings, mask, save_settings
from .fetchers import browser
from .pipeline import batch, llm
from .pipeline.runner import (
    apply_rules_to,
    ingest_external,
    llm_active,
    preview_source,
    refresh_rule_scores,
    run_source,
)
from .textutil import TZ, now, now_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
TOKEN = os.environ.get("INFO_STATION_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    with db.get_conn() as c:  # 上次异常退出时遗留的 running 标记
        c.execute("UPDATE source_state SET running=0")
    try:  # 一次性补上 4 个默认停用的邮箱示例信源
        from .fetchers import mail

        config.ensure_example_sources(mail.EXAMPLES, "seeded:email_examples")
    except Exception:  # noqa: BLE001
        logging.getLogger("main").exception("补充邮箱示例信源失败")
    try:  # 规则变了（含本次升级）：重算只有规则分的旧条目，修正虚高分与截止时间
        refresh_rule_scores()
    except Exception:  # noqa: BLE001
        logging.getLogger("main").exception("重算规则分失败")
    if load_settings().scheduler_enabled and os.environ.get("INFO_STATION_NO_SCHEDULER") != "1":
        scheduler.start()
    yield
    scheduler.shutdown()


class UTF8JSONResponse(JSONResponse):
    """显式声明 charset=utf-8：Windows PowerShell 5.1 的 Invoke-RestMethod 等客户端在缺少 charset 时
    会按 ISO-8859-1 解码，中文信源名会显示成乱码（数据本身没问题）。"""

    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Info Station 本地后端", version=__version__, lifespan=lifespan,
              default_response_class=UTF8JSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(tauri://localhost|https?://tauri\.localhost|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


def auth(x_local_token: str | None = Header(default=None)) -> None:
    if TOKEN and x_local_token != TOKEN:
        raise HTTPException(401, "token 不正确")


api = Depends(auth)


# ---------------------------------------------------------------- 基础
@app.get("/api/health")
def health() -> dict[str, Any]:
    s = load_settings()
    return {"ok": True, "version": __version__, "time": now_iso(), "llm": llm_active(s),
            "llm_paused": llm.paused(), "llm_error": llm.STATE.get("last_error", ""),
            "playwright": browser.available(), "scheduler": scheduler.scheduler.running}


@app.get("/api/meta", dependencies=[api])
def meta() -> dict[str, Any]:
    return {"categories": CATEGORIES, "source_types": SOURCE_TYPES, "tiers": ["T1", "T2"]}


@app.get("/api/stats", dependencies=[api])
def get_stats() -> dict[str, Any]:
    return queries.stats(load_settings())


# ---------------------------------------------------------------- 条目
@app.get("/api/items", dependencies=[api])
def items(view: Literal["featured", "all"] = "all", category: str | None = None, source: str | None = None,
          read: Literal["all", "unread", "read"] = "all", q: str | None = None,
          sort: Literal["score", "time"] = "score", days: int | None = None, starred: bool = False,
          limit: int = Query(50, le=200), offset: int = 0) -> dict[str, Any]:
    return queries.list_items(load_settings(), view=view, category=category or None, source_id=source or None,
                              read=read, q=q, sort=sort, days=days, starred=starred, limit=limit, offset=offset)


@app.get("/api/items/{item_id}", dependencies=[api])
def item_detail(item_id: int) -> dict[str, Any]:
    it = queries.get_item(item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    return it


class ItemPatch(BaseModel):
    is_read: bool | None = None
    starred: bool | None = None
    feedback: Literal[-1, 0, 1] | None = None


@app.patch("/api/items/{item_id}", dependencies=[api])
def patch_item(item_id: int, body: ItemPatch) -> dict[str, Any]:
    fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump(exclude_none=True).items()}
    if fields:
        with db.get_conn() as c:
            c.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), item_id))
            if "is_read" in fields:  # 同一事件的其他报道一起标记
                c.execute("UPDATE items SET is_read=? WHERE cluster_id=(SELECT cluster_id FROM items WHERE id=?)",
                          (fields["is_read"], item_id))
    return {"ok": True}


class MarkRead(BaseModel):
    ids: list[int] | None = None
    category: str | None = None


@app.post("/api/items/mark-read", dependencies=[api])
def mark_read(body: MarkRead) -> dict[str, Any]:
    with db.get_conn() as c:
        if body.ids:
            q = ",".join("?" * len(body.ids))
            n = c.execute(f"UPDATE items SET is_read=1 WHERE cluster_id IN (SELECT cluster_id FROM items WHERE id IN ({q}))",
                          body.ids).rowcount
        elif body.category:
            n = c.execute("UPDATE items SET is_read=1 WHERE category=?", (body.category,)).rowcount
        else:
            n = c.execute("UPDATE items SET is_read=1").rowcount
    return {"ok": True, "updated": n}


@app.get("/api/deadlines", dependencies=[api])
def get_deadlines(horizon: int = 120) -> list[dict[str, Any]]:
    return queries.deadlines(horizon_days=horizon)


_bg_tasks: set[asyncio.Task] = set()


def _start_now(force: bool) -> dict[str, Any]:
    """手动“立即打分”：高峰时段先提醒价格，用户确认（force）后才调用模型。"""
    st = batch.status()
    if st["now_is_peak"] and not force:
        return {"started": False, "needs_confirm": True, "now_is_peak": True, "next_offpeak": st["next_offpeak"],
                "pending": st["pending"],
                "message": "现在是 DeepSeek 高峰时段，价格是谷时的 2 倍。"}
    if st["running"]:
        return {"started": False, "running": True, "pending": st["pending"]}
    # 后台跑：可能有上百条，接口立即返回，前端轮询 /api/score/schedule 看进度
    task = asyncio.get_running_loop().create_task(batch.run_batch("manual", allow_peak=True, notify=False))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"started": True, "pending": st["pending"], "now_is_peak": st["now_is_peak"]}


@app.get("/api/score/schedule", dependencies=[api])
def score_schedule() -> dict[str, Any]:
    return batch.status()


@app.post("/api/score/run", dependencies=[api])
async def score_run(force: bool = False) -> dict[str, Any]:
    return _start_now(force)


@app.post("/api/rescore", dependencies=[api])
async def rescore(only_pending: bool = True, days: int = 0, categories: str | None = None,
                  force: bool = False, queue_only: bool = False) -> dict[str, Any]:
    """重新打分：先把条目放回队列（规则分），再走和“立即打分”相同的流程（高峰需确认）。

    - only_pending=true：只处理已在队列中的条目
    - only_pending=false：今天抓到的条目放回队列
    - days=N：近 N 天发布（或截止还没到）的条目按新规则重算后放回队列；categories 逗号分隔，限定分类
    - queue_only=true：只放回队列，等每日谷时批量
    """
    requeued = 0
    if days > 0:
        since = (now() - timedelta(days=days)).isoformat(timespec="seconds")
        cats = [c for c in (categories or "").split(",") if c in CATEGORIES]
        sql = ("SELECT id FROM items WHERE (COALESCE(published_at, fetched_at) >= ? OR deadline >= ?)"
               + (f" AND category IN ({','.join('?' * len(cats))})" if cats else ""))
        with db.get_conn() as c:
            ids = [r[0] for r in c.execute(sql, (since, now().isoformat(timespec="seconds"), *cats)).fetchall()]
        apply_rules_to(ids, load_settings())
        requeued = len(ids)
    elif not only_pending:
        with db.get_conn() as c:
            requeued = c.execute("UPDATE items SET status='new' WHERE status IN ('rule','scored') AND fetched_at >= ?",
                                 ((now().replace(hour=0, minute=0)).isoformat(),)).rowcount
    if queue_only:
        return {"started": False, "requeued": requeued, **{k: batch.status()[k] for k in ("next_at", "pending")}}
    return {**_start_now(force), "requeued": requeued}


# ---------------------------------------------------------------- 信源
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


class SourceIn(BaseModel):
    id: str
    name: str
    type: str
    url: str | None = None
    tier: Literal["T1", "T2"] = "T2"
    category: str = "news"
    enabled: bool = True
    interval_minutes: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict, description="类型相关配置：list/detail/repo/mode/query/fields …")

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.model_dump(exclude={"extra"}).items() if v is not None}
        d.update(self.extra or {})
        return d


def _source_view(src: dict[str, Any], st: dict[str, Any], nxt: dict[str, Any]) -> dict[str, Any]:
    known = {"id", "name", "type", "url", "tier", "category", "enabled", "interval_minutes"}
    return {**{k: src.get(k) for k in known}, "extra": {k: v for k, v in src.items() if k not in known},
            "state": st, "next_run": nxt.get(src["id"])}


@app.get("/api/sources", dependencies=[api])
def list_sources() -> list[dict[str, Any]]:
    with db.get_conn() as c:
        states = {r["source_id"]: dict(r) for r in c.execute("SELECT * FROM source_state").fetchall()}
        counts = {r[0]: r[1] for r in c.execute("SELECT source_id, COUNT(*) FROM items GROUP BY source_id").fetchall()}
    nxt = scheduler.next_runs()
    out = []
    for src in config.load_sources():
        st = states.get(src["id"], {}) | {"items": counts.get(src["id"], 0)}
        out.append(_source_view(src, st, nxt))
    return out


def _validate(body: SourceIn) -> None:
    if not _ID_RE.match(body.id):
        raise HTTPException(400, "id 只能用小写字母、数字、-、_")
    if body.type not in SOURCE_TYPES:
        raise HTTPException(400, f"type 必须是 {SOURCE_TYPES}")
    if body.category not in CATEGORIES and body.category != "feishu":
        raise HTTPException(400, "未知分类")
    if body.type == "x":
        _validate_x(body)
    if body.type == "email":
        bad = {"password", "secret", "token", "auth_code", "app_password", "refresh_token"} & set(body.extra or {})
        if bad:
            raise HTTPException(400, "授权码 / 密码不能写进 sources.yaml：请在信源卡片里填写，会存进 Windows 凭据管理器")


def _validate_x(body: SourceIn) -> None:
    """X 信源：关键词与账号去空去重；账号统一成不带 @ 的用户名。"""
    from .fetchers.x_parse import normalize_handle

    ex = body.extra
    kws = ex.get("keywords") or []
    accs = ex.get("accounts") or []
    if isinstance(kws, str):
        kws = kws.splitlines()
    if isinstance(accs, str):
        accs = accs.replace(",", "\n").splitlines()
    kws = list(dict.fromkeys(str(k).strip() for k in kws if str(k).strip()))
    bad = [str(a) for a in accs if str(a).strip() and not normalize_handle(str(a))]
    if bad:
        raise HTTPException(400, f"X 账号格式不对：{'、'.join(bad[:5])}（只能是字母、数字、下划线，最多 15 位）")
    handles = list(dict.fromkeys(h for h in (normalize_handle(str(a)) for a in accs) if h))
    if not kws and not handles:
        raise HTTPException(400, "X 信源至少要有一个关键词或账号")
    if len(kws) + len(handles) > 40:
        raise HTTPException(400, "关键词 + 账号最多 40 个（每天一次、慢慢翻，太多容易被限制）")
    ex["keywords"], ex["accounts"] = kws, handles
    ex.setdefault("use_proxy", True)
    body.url = None


@app.post("/api/sources", dependencies=[api])
def create_source(body: SourceIn) -> dict[str, Any]:
    _validate(body)
    srcs = config.load_sources()
    if any(s["id"] == body.id for s in srcs):
        raise HTTPException(409, "id 已存在")
    srcs.append(body.to_dict())
    config.save_sources(srcs)
    scheduler.sync_jobs(initial_delay=False) if scheduler.scheduler.running else None
    return {"ok": True}


@app.put("/api/sources/{source_id}", dependencies=[api])
def update_source(source_id: str, body: SourceIn) -> dict[str, Any]:
    _validate(body)
    srcs = config.load_sources()
    for i, s in enumerate(srcs):
        if s["id"] == source_id:
            srcs[i] = body.to_dict()
            break
    else:
        raise HTTPException(404, "信源不存在")
    config.save_sources(srcs)
    if scheduler.scheduler.running:
        scheduler.sync_jobs(initial_delay=True)
    return {"ok": True}


class Toggle(BaseModel):
    enabled: bool


@app.post("/api/sources/{source_id}/toggle", dependencies=[api])
def toggle_source(source_id: str, body: Toggle) -> dict[str, Any]:
    srcs = config.load_sources()
    for s in srcs:
        if s["id"] == source_id:
            s["enabled"] = body.enabled
    config.save_sources(srcs)
    if scheduler.scheduler.running:
        scheduler.sync_jobs()
    return {"ok": True}


@app.delete("/api/sources/{source_id}", dependencies=[api])
def delete_source(source_id: str, purge: bool = False) -> dict[str, Any]:
    srcs = [s for s in config.load_sources() if s["id"] != source_id]
    config.save_sources(srcs)
    with db.get_conn() as c:
        c.execute("DELETE FROM source_state WHERE source_id=?", (source_id,))
        if purge:
            c.execute("DELETE FROM items WHERE source_id=?", (source_id,))
    if scheduler.scheduler.running:
        scheduler.sync_jobs()
    # 一并删掉它在凭据管理器里的邮箱密码 / 令牌（没有就忽略）
    from . import mail_oauth, vault
    from .fetchers import mail

    vault.delete(mail.secret_key(source_id))
    mail_oauth.forget(source_id)
    return {"ok": True}


@app.post("/api/sources/{source_id}/run", dependencies=[api])
async def run_now(source_id: str) -> dict[str, Any]:
    try:
        return await run_source(source_id, manual=True)
    except KeyError:
        raise HTTPException(404, "信源不存在") from None


@app.post("/api/sources/run-all", dependencies=[api])
async def run_all() -> list[dict[str, Any]]:
    import asyncio

    # X 每天只按计划跑一次，不参与“全部抓取”（需要时在 X 卡片上单独点）
    srcs = [s for s in config.load_sources() if s.get("enabled") and s.get("type") not in ("feishu", "x")]
    return list(await asyncio.gather(*[run_source(s["id"]) for s in srcs]))


@app.post("/api/sources/preview", dependencies=[api])
async def preview(body: SourceIn) -> dict[str, Any]:
    return await preview_source(body.to_dict())


@app.post("/api/sources/reload", dependencies=[api])
def reload_sources() -> dict[str, Any]:
    if scheduler.scheduler.running:
        scheduler.sync_jobs()
    return {"ok": True, "count": len(config.load_sources())}


class LoginReq(BaseModel):
    url: str


@app.post("/api/browser/login", dependencies=[api])
async def browser_login(body: LoginReq) -> dict[str, Any]:
    """弹出可见浏览器让你自己登录（如统一身份认证），关闭窗口后会话保存在本机 profile。"""
    try:
        await browser.open_login_window(body.url, load_settings())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# ---------------------------------------------------------------- X
def _x_view() -> dict[str, Any]:
    from . import proxy
    from .fetchers import x as x_mod

    s = load_settings()
    st = x_mod.get_status()
    srcs = x_mod.x_sources()
    purl, pfrom = proxy.resolve(s, srcs[0] if srcs else {"type": "x"})
    nxt = scheduler.next_runs()
    return {**st, "playwright": browser.available(), "time": s.x.time, "headless": s.x.headless,
            "show_avatars": s.x.show_avatars, "daily_max": s.x.daily_max,
            "proxy": proxy.describe(purl), "proxy_from": pfrom,
            "sources": [{"id": x["id"], "name": x.get("name"), "enabled": bool(x.get("enabled")),
                         "keywords": len(x.get("keywords") or []), "accounts": len(x.get("accounts") or []),
                         "next_run": nxt.get(x["id"])} for x in srcs]}


@app.get("/api/x/status", dependencies=[api])
def x_status() -> dict[str, Any]:
    return _x_view()


@app.post("/api/x/login", dependencies=[api])
async def x_login() -> dict[str, Any]:
    """弹出可见浏览器打开 x.com 登录页，你自己登录（含两步验证）；关闭窗口后会话保存在本机 profile。"""
    from . import proxy
    from .fetchers import x as x_mod

    s = load_settings()
    if x_mod.get_status()["running"]:
        raise HTTPException(409, "X 抓取正在运行，请等它结束后再登录")
    srcs = x_mod.x_sources()
    purl, _ = proxy.resolve(s, srcs[0] if srcs else {"type": "x"})
    try:
        last_url = await browser.open_login_window("https://x.com/login", s, timeout_s=900, proxy_url=purl)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    x_mod.mark_login_result(last_url)
    if scheduler.scheduler.running:
        scheduler.sync_jobs()
    return _x_view()


@app.post("/api/x/clear-block", dependencies=[api])
def x_clear_block() -> dict[str, Any]:
    from .fetchers import x as x_mod

    x_mod.clear_block()
    return _x_view()


# ---------------------------------------------------------------- 邮件信源
def _email_source(source_id: str) -> dict[str, Any]:
    src = config.get_source(source_id)
    if not src or src.get("type") != "email":
        raise HTTPException(404, "没有这个邮件信源")
    return src


def _email_view(src: dict[str, Any]) -> dict[str, Any]:
    from . import mail_oauth, proxy
    from .fetchers import mail

    p = mail.preset(src)
    purl, pfrom = proxy.resolve(load_settings(), src)
    return {"id": src["id"], "provider": src.get("provider") or "custom", "address": src.get("address") or "",
            "address_ok": mail.address_ok(str(src.get("address") or "")), "host": p["host"], "port": p["port"],
            "ssl": p["ssl"], "auth": p["auth"], "has_secret": mail.has_secret(src),
            "client_id": src.get("client_id") or "", "folders": src.get("folders") or ["INBOX"],
            "days": int(src.get("days") or 7), "proxy": proxy.describe(purl), "proxy_from": pfrom,
            "oauth": mail_oauth.status(src["id"]) if p["auth"] == "oauth2_ms" else None}


@app.get("/api/email/providers", dependencies=[api])
def email_providers() -> dict[str, Any]:
    from . import vault
    from .fetchers import mail

    return {"providers": mail.PROVIDERS, "vault": vault.backend_name()}


@app.get("/api/email/{source_id}", dependencies=[api])
def email_status(source_id: str) -> dict[str, Any]:
    return _email_view(_email_source(source_id))


class SecretIn(BaseModel):
    secret: str = Field(min_length=1, max_length=512)


@app.put("/api/email/{source_id}/secret", dependencies=[api])
def email_set_secret(source_id: str, body: SecretIn) -> dict[str, Any]:
    """授权码只进 Windows 凭据管理器；接口永远不会把它返回给前端。"""
    from . import vault
    from .fetchers import mail

    src = _email_source(source_id)
    try:
        vault.set(mail.secret_key(source_id), body.secret.strip().replace(" ", "") if src.get("provider") in ("gmail", "qq")
                  else body.secret.strip())
    except vault.VaultError as e:
        raise HTTPException(500, str(e)) from e
    return _email_view(src)


@app.delete("/api/email/{source_id}/secret", dependencies=[api])
def email_delete_secret(source_id: str) -> dict[str, Any]:
    from . import mail_oauth, vault
    from .fetchers import mail

    src = _email_source(source_id)
    vault.delete(mail.secret_key(source_id))
    mail_oauth.forget(source_id)
    return _email_view(src)


@app.post("/api/email/{source_id}/test", dependencies=[api])
async def email_test(source_id: str) -> dict[str, Any]:
    from .fetchers import mail

    src = _email_source(source_id)
    try:
        r = await asyncio.to_thread(mail.test_connection, src, load_settings())
        return {**r, "message": f"连接成功：收件箱共 {r['count']} 封（只读，没有改动任何邮件）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/email/{source_id}/oauth/start", dependencies=[api])
async def email_oauth_start(source_id: str) -> dict[str, Any]:
    from . import mail_oauth

    src = _email_source(source_id)
    try:
        return await mail_oauth.start(src)
    except mail_oauth.OAuthError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"连不上 Microsoft 登录服务：{type(e).__name__}: {str(e)[:160]}") from e


@app.post("/api/email/{source_id}/reset", dependencies=[api])
def email_reset(source_id: str) -> dict[str, Any]:
    """清掉 UID 游标：下次重新按“最近 N 天”收取（已入库的邮件按 Message-ID 去重，不会重复）。"""
    from .fetchers import mail

    mail.reset_state(_email_source(source_id)["id"])
    return {"ok": True}


@app.post("/api/settings/test-proxy", dependencies=[api])
async def test_proxy() -> dict[str, Any]:
    """经当前代理访问 x.com 首页（不登录、不带 cookie），只看网络通不通。"""
    import time

    import httpx

    from . import proxy

    s = load_settings()
    purl, pfrom = proxy.resolve(s, force=True)
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=purl, timeout=12, follow_redirects=True) as c:
            r = await c.get("https://x.com/", headers={"User-Agent": s.fetch.user_agent})
        return {"ok": r.status_code < 500, "status": r.status_code, "ms": int((time.monotonic() - t0) * 1000),
                "proxy": proxy.describe(purl), "proxy_from": pfrom}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "proxy": proxy.describe(purl),
                "proxy_from": pfrom}


# ---------------------------------------------------------------- 简报
@app.get("/api/briefs", dependencies=[api])
def briefs() -> list[str]:
    return brief.list_briefs()


@app.get("/api/briefs/today", dependencies=[api])
def brief_today(refresh: bool = True) -> dict[str, Any]:
    today = now().strftime("%Y-%m-%d")
    if not refresh:
        b = brief.get_brief(today)
        if b:
            return b
    return brief.build_brief(load_settings())


@app.get("/api/briefs/{date}", dependencies=[api])
def brief_by_date(date: str) -> dict[str, Any]:
    b = brief.get_brief(date)
    if not b:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ, hour=23)
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD") from None
        b = brief.build_brief(load_settings(), day)
    return b


# ---------------------------------------------------------------- 设置
def _settings_view(s: Settings) -> dict[str, Any]:
    d = s.model_dump()
    key = deepseek_key(s)
    d["llm"]["api_key"] = ""
    d["llm"]["api_key_masked"] = mask(key)
    d["llm"]["api_key_from_env"] = bool(not s.llm.api_key and key)
    d["fetch"]["github_token"] = ""
    d["fetch"]["github_token_masked"] = mask(config.github_token(s))
    from . import proxy

    sys_p = proxy.system_proxy() if s.fetch.proxy_use_system else None
    d["fetch"]["system_proxy"] = proxy.describe(sys_p)
    d["paths"] = {"data": str(config.DATA_DIR), "config": str(config.CONFIG_DIR), "prompts": str(config.PROMPTS_DIR)}
    return d


@app.get("/api/settings", dependencies=[api])
def get_settings() -> dict[str, Any]:
    return _settings_view(load_settings())


@app.put("/api/settings", dependencies=[api])
def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    cur = load_settings()
    merged = cur.model_dump()
    for section, val in body.items():
        if section in merged and isinstance(val, dict):
            merged[section].update({k: v for k, v in val.items() if not k.endswith(("_masked", "_from_env")) and k != "system_proxy"})
        elif section in merged:
            merged[section] = val
    # 密钥：空字符串表示“不修改”，特殊值 "__clear__" 表示清除
    for sec, key in (("llm", "api_key"), ("fetch", "github_token")):
        v = (body.get(sec) or {}).get(key)
        old = getattr(getattr(cur, sec), key)
        merged[sec][key] = "" if v == "__clear__" else (v.strip() if v else old)
    try:
        s = Settings.model_validate(merged)
    except ValidationError as e:
        raise HTTPException(422, "；".join(str(x.get("msg", "")).removeprefix("Value error, ") for x in e.errors())) from None
    save_settings(s)
    if scheduler.scheduler.running:
        scheduler.sync_jobs()
    return _settings_view(s)


@app.post("/api/settings/test-llm", dependencies=[api])
async def test_llm() -> dict[str, Any]:
    try:
        return await llm.test_connection(load_settings())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


# ---------------------------------------------------------------- 通知
def _in_quiet(s: Settings) -> bool:
    t = now().strftime("%H:%M")
    a, b = s.notify.quiet_start, s.notify.quiet_end
    return (a <= t or t < b) if a > b else (a <= t < b)


@app.get("/api/notifications/pending", dependencies=[api])
def pending_notifications() -> list[dict[str, Any]]:
    s = load_settings()
    if not s.notify.enabled or _in_quiet(s):
        return []
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM notifications WHERE delivered=0 ORDER BY id LIMIT 5").fetchall()]


@app.post("/api/notifications/{nid}/ack", dependencies=[api])
def ack_notification(nid: int) -> dict[str, Any]:
    with db.get_conn() as c:
        c.execute("UPDATE notifications SET delivered=1 WHERE id=?", (nid,))
    return {"ok": True}


# ---------------------------------------------------------------- 外部推送
class IngestItem(BaseModel):
    title: str
    url: str
    content: str = ""
    summary: str = ""


class IngestBody(BaseModel):
    source_id: str
    items: list[IngestItem]


@app.post("/api/ingest/items", dependencies=[api])
def ingest(body: IngestBody) -> dict[str, Any]:
    ids = ingest_external(body.source_id, [i.model_dump() for i in body.items])
    return {"ok": True, "inserted": len(ids)}


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):  # pragma: no cover
    logging.getLogger("api").exception("未处理异常")
    return UTF8JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)
