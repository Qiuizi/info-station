"""流水线：抓取 → 判重入库 → 事件归并 → 规则预筛 → 即时提醒；模型打分由 batch.py 每日谷时统一进行。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import config, db
from ..config import Settings, deepseek_key, load_rules, load_settings
from ..fetchers import FETCHERS, FetchContext, FetchError, RawItem
from ..fetchers.html_list import fetch_detail_http
from ..textutil import extract_deadline, from_iso, normalize_url, now, now_iso, sha1, similarity, title_key, to_iso
from . import llm as llm_mod
from .llm import LLMError, LLMFatal, score_item
from .rules import apply_rules, blend_llm_score, kw_in

log = logging.getLogger("pipeline")
_source_locks: dict[str, asyncio.Lock] = {}
_score_lock = asyncio.Lock()


def _lock_for(source_id: str) -> asyncio.Lock:
    return _source_locks.setdefault(source_id, asyncio.Lock())


def llm_active(s: Settings) -> bool:
    return bool(s.llm.enabled and deepseek_key(s))


def threshold_for(tier: str, base: int, s: Settings) -> int:
    return base + (s.thresholds.t2_offset if tier == "T2" else 0)


# ---------------------------------------------------------------- 入库与归并
def find_cluster(conn, title: str, source_id: str) -> int | None:
    since = (now() - timedelta(days=10)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, title, cluster_id, source_id FROM items WHERE fetched_at >= ? AND source_id != ? ORDER BY id DESC LIMIT 400",
        (since, source_id),
    ).fetchall()
    best, best_sim = None, 0.0
    for r in rows:
        sim = similarity(title, r["title"])
        if sim > best_sim:
            best, best_sim = r, sim
    if best is not None and best_sim >= 0.62:
        return best["cluster_id"] or best["id"]
    return None


def item_key(it: RawItem) -> str:
    """去重键：推文等有稳定 id 的条目用 extra.dedup_key（如 x:<推文id>），其余用规范化后的 URL。"""
    dk = (it.extra or {}).get("dedup_key")
    return sha1(str(dk)) if dk else sha1(normalize_url(it.url))


def _extra_json(extra: dict[str, Any] | None) -> str | None:
    keep = {k: v for k, v in (extra or {}).items() if k not in ("detail_fetched",) and not k.startswith("_")}
    return json.dumps(keep, ensure_ascii=False) if keep else None


def _row_extra(row) -> dict[str, Any]:
    try:
        return json.loads(row["extra"]) if "extra" in row.keys() and row["extra"] else {}
    except (TypeError, ValueError):
        return {}


def insert_items(source: dict[str, Any], raw: list[RawItem], s: Settings, first_run: bool) -> list[int]:
    ids: list[int] = []
    stale_before = now() - timedelta(hours=s.thresholds.stale_hours)
    with db.get_conn() as conn:
        for it in raw:
            url_key = item_key(it)
            if conn.execute("SELECT 1 FROM items WHERE url_key=?", (url_key,)).fetchone():
                continue
            pub = to_iso(it.published_at)
            backfill = first_run or (it.published_at is not None and it.published_at < stale_before)
            cluster = find_cluster(conn, it.title, source["id"])
            cur = conn.execute(
                """INSERT INTO items(source_id, url, url_key, title_key, title, raw_summary, content, author,
                   published_at, fetched_at, category, tier, status, is_backfill, cluster_id, extra)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?, ?)""",
                (source["id"], it.url, url_key, title_key(it.title), it.title, it.summary or "", it.content or "",
                 it.author or "", pub, now_iso(), source.get("category", "news"), source.get("tier", "T2"),
                 int(backfill), cluster, _extra_json(it.extra)),
            )
            new_id = cur.lastrowid
            if cluster is None:
                conn.execute("UPDATE items SET cluster_id=? WHERE id=?", (new_id, new_id))
            ids.append(new_id)
    return ids


def _queue_for_llm(published: datetime | None, deadline: datetime | None, s: Settings) -> bool:
    """只把近期的条目送模型：发布在 max_age_days 天内，或还有未到的截止日期；发布时间未知的也送。"""
    if deadline and deadline >= now():
        return True
    if published is None or s.llm.max_age_days <= 0:
        return True
    return published >= now() - timedelta(days=s.llm.max_age_days)


def apply_rules_to(ids: list[int], s: Settings, *, notify: bool = True) -> None:
    """按规则（重新）打分；notify=False 用于启动时批量修正旧数据，不发通知。"""
    if not ids:
        return
    rules = load_rules()
    llm_on = llm_active(s)
    sources = {x["id"]: x for x in config.load_sources()}
    src_adj = rules.get("source_adjust") or {}
    urgent_min = int(rules.get("rule_notify_urgent", 65))
    with db.get_conn() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            rows = conn.execute(f"SELECT * FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for row in rows:
                published = from_iso(row["published_at"])
                ref = published or from_iso(row["fetched_at"]) or now()
                rr = apply_rules(row["title"], row["content"] or row["raw_summary"] or "", row["category"], row["tier"], s,
                                 rules, ref, source_adjust=int(src_adj.get(row["source_id"], 0)),
                                 engagement=_row_extra(row), author=row["author"] or "")
                src = sources.get(row["source_id"], {})
                allow_llm = src.get("allow_llm", (src.get("extra") or {}).get("allow_llm", row["category"] != "feishu"))
                queue = llm_on and allow_llm and _queue_for_llm(published, rr.deadline, s)
                status = "blocked" if rr.blocked else ("new" if queue else "rule")
                reason = "；".join(rr.hits[:6])
                if llm_on and allow_llm and not queue and not rr.blocked:
                    reason = f"发布超过 {s.llm.max_age_days} 天的旧文，未送模型；" + reason
                conn.execute(
                    """UPDATE items SET rule_score=?, score=?, status=?, tags=?, deadline=?, deadline_note=?, reason=?,
                       scored_by=? WHERE id=?""",
                    (rr.score, rr.score, status, json.dumps(rr.tags, ensure_ascii=False), to_iso(rr.deadline),
                     rr.deadline_note, reason, "rule", row["id"]),
                )
                # 还没经模型打分的条目只在“规则识别出三天内截止”时提醒（规则分已封顶，普通高分等模型确认）
                if notify and status != "blocked" and _urgent(rr.deadline):
                    _maybe_notify(conn, row["id"], s, need=urgent_min)


RULES_ENGINE_VERSION = "3"  # 规则算法变化时改这里，启动时会重算所有未经模型打分的条目


def refresh_rule_scores(s: Settings | None = None, *, force: bool = False) -> dict[str, int]:
    """启动时调用：规则（rules.yaml 或算法版本）变化后，重算所有“只有规则分”的条目，修正旧库里的虚高分。

    - 只动 scored_by='rule' 的条目（含排队待打分、被规则屏蔽的）；模型打过分的不动
    - 对模型打过分的条目，用模型给出的“截止原文依据”按新的时间归一化规则重算截止时间
    - 不发通知
    """
    s = s or load_settings()
    sig = sha1(json.dumps(load_rules(), ensure_ascii=False, sort_keys=True, default=str) + RULES_ENGINE_VERSION
               + str(s.llm.max_age_days) + json.dumps(s.profile.model_dump(), ensure_ascii=False, sort_keys=True))[:12]
    if not force and db.get_state("rules_sig") == sig:
        return {"rescored": 0, "deadlines_fixed": 0, "skipped": 1}
    with db.get_conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM items WHERE scored_by='rule' OR scored_by IS NULL OR scored_by=''")]
        llm_rows = c.execute("SELECT id, title, tags, deadline, deadline_note, published_at, fetched_at FROM items "
                             "WHERE scored_by NOT IN ('rule', '')").fetchall()
    apply_rules_to(ids, s, notify=False)
    fixed = 0
    rules = load_rules()
    rule_tag_names = set((rules.get("tags") or {}).keys())
    with db.get_conn() as c:
        for r in llm_rows:
            # 1) 截止时间：只修正模型给过截止的条目，按它引用的原文用新的归一化规则重算（24 点、括号里的补充时间等）
            if r["deadline"] and r["deadline_note"]:
                ref = from_iso(r["published_at"]) or from_iso(r["fetched_at"]) or now()
                dl, _ = extract_deadline(r["deadline_note"], ref)
                new = to_iso(dl) if dl else None
                if new and new != r["deadline"]:
                    c.execute("UPDATE items SET deadline=? WHERE id=?", (new, r["id"]))
                    fixed += 1
            # 2) 标签：去掉旧规则按正文子串乱打、新规则（只看标题）不再认可的规则标签
            try:
                tags = json.loads(r["tags"] or "[]")
            except ValueError:
                continue
            title_tags = {t for t, kws in (rules.get("tags") or {}).items() if any(kw_in(k, r["title"]) for k in kws)}
            # 保存时是“模型标签 + 规则标签”拼接，规则标签在末尾：只清理末尾连续的那段规则标签
            tail = len(tags)
            while tail > 0 and tags[tail - 1] in rule_tag_names:
                tail -= 1
            keep = tags[:tail] + [t for t in tags[tail:] if t in title_tags]
            if keep != tags:
                c.execute("UPDATE items SET tags=? WHERE id=?", (json.dumps(keep, ensure_ascii=False), r["id"]))
    db.set_state("rules_sig", sig)
    log.info("规则变化：重算 %d 条规则分，修正 %d 条截止时间", len(ids), fixed)
    return {"rescored": len(ids), "deadlines_fixed": fixed}


# ---------------------------------------------------------------- 通知
URGENT_DAYS = 3


def _urgent(deadline) -> bool:
    if not deadline:
        return False
    days = (deadline - now()).total_seconds() / 86400
    return 0 <= days <= URGENT_DAYS


def _maybe_notify(conn, item_id: int, s: Settings, need: int | None = None) -> None:
    if not s.notify.enabled:
        return
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row or row["notified"] or row["is_backfill"] or row["status"] == "blocked":
        return
    need = threshold_for(row["tier"], s.thresholds.notify if need is None else need, s)
    if row["score"] < need:
        return
    # 同一事件只提醒一次
    dup = conn.execute("SELECT 1 FROM items WHERE cluster_id=? AND notified=1", (row["cluster_id"],)).fetchone()
    if dup:
        return
    body = row["summary"] or row["raw_summary"] or ""
    if row["deadline"]:
        dl = from_iso(row["deadline"])
        body = f"⏰ 截止 {dl:%m-%d %H:%M} · " + body if dl else body
    conn.execute(
        "INSERT INTO notifications(item_id, title, body, created_at) VALUES(?,?,?,?)",
        (item_id, row["title"][:80], body[:160], now_iso()),
    )
    conn.execute("UPDATE items SET notified=1, should_notify=1 WHERE id=?", (item_id,))


# ---------------------------------------------------------------- LLM 打分
async def score_pending(limit: int | None = None, *, notify: bool = True) -> dict[str, int]:
    """给 status='new' 的条目打一轮分（最多 limit 条）。

    只由每日批量任务（pipeline/batch.py）和手动“立即打分”调用；抓取后不再自动触发。
    notify=False 时不逐条弹通知（批量结束后由批量任务发一条汇总通知）。
    """
    s = load_settings()
    if not llm_active(s):
        return {"scored": 0, "failed": 0, "skipped": 1}
    if llm_mod.paused():
        return {"scored": 0, "failed": 0, "paused": 1}
    async with _score_lock:
        limit = limit or s.llm.max_items_per_run
        names = {x["id"]: x.get("name", x["id"]) for x in config.load_sources()}
        with db.get_conn() as conn:
            rows = [db.row_to_item(r) for r in conn.execute(
                "SELECT * FROM items WHERE status='new' ORDER BY is_backfill ASC, id DESC LIMIT ?", (limit,)).fetchall()]
        sem = asyncio.Semaphore(max(1, s.llm.concurrency))
        stats = {"scored": 0, "failed": 0}
        async with httpx.AsyncClient(timeout=s.llm.timeout_seconds) as client:
            async def one(item: dict[str, Any]) -> None:
                item["source_name"] = names.get(item["source_id"], item["source_id"])
                async with sem:
                    if llm_mod.paused():
                        return  # 熔断中：保持 new，恢复后再打分（当前展示的是规则分）
                    try:
                        verdicts, version = await score_item(item, s, client)
                    except LLMFatal as e:
                        llm_mod.trip(str(e))
                        stats["failed"] += 1
                        log.warning("模型调用熔断 30 分钟：%s", e)
                        return
                    except (LLMError, httpx.HTTPError) as e:
                        stats["failed"] += 1
                        log.warning("打分失败 %s: %s", item["id"], e)
                        with db.get_conn() as conn:
                            # 失败降级为规则分，保留 new 以便下次重试的话可改成 'new'
                            conn.execute("UPDATE items SET status='rule', reason=? WHERE id=?",
                                         (f"模型打分失败，已用规则分：{str(e)[:120]}", item["id"]))
                            if notify:
                                _maybe_notify(conn, item["id"], s)
                        return
                save_verdicts(item, verdicts, version, s, notify=notify)
                stats["scored"] += 1

            await asyncio.gather(*[one(it) for it in rows])
        return stats


def save_verdicts(item: dict[str, Any], verdicts, version: str, s: Settings, *, notify: bool = True) -> None:
    rules = load_rules()
    cat = item.get("category") or "news"
    scores = [blend_llm_score(v.score, v.dims, v.eligible, cat, rules) for v in verdicts]
    final = sum(scores) // len(scores)
    v0 = verdicts[0]
    ineligible = v0.eligible is False and cat in ("school", "competition")
    deadline = v0.deadline or item.get("deadline")
    tags = list(dict.fromkeys((v0.tags or []) + (item.get("tags") or [])))[:5]
    status = "blocked" if v0.blocked and final < 30 else "scored"
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE items SET llm_scores=?, score=?, summary=?, tags=?, deadline=?, deadline_note=?, relevant=?,
               reason=?, action=?, status=?, prompt_version=?, scored_by=?, audience=?, eligible=?, dims=?,
               llm_raw=? WHERE id=?""",
            (json.dumps(scores), final, v0.summary, json.dumps(tags, ensure_ascii=False), deadline,
             v0.deadline_note or item.get("deadline_note") or "", int(v0.relevant), v0.reason, v0.action, status,
             version, s.llm.model, v0.audience, None if v0.eligible is None else int(v0.eligible),
             json.dumps(v0.dims) if v0.dims else None, json.dumps([v.score for v in verdicts]), item["id"]),
        )
        if ineligible or not notify:
            return
        if all(v.should_notify for v in verdicts) or final >= threshold_for(item["tier"], s.thresholds.notify, s):
            _maybe_notify(conn, item["id"], s)


# ---------------------------------------------------------------- 单源运行
async def run_source(source_id: str, *, manual: bool = False) -> dict[str, Any]:
    """抓一次信源。manual=True 表示你在界面上点的“立即抓取”（X 信源据此区分手动与定时）。"""
    source = config.get_source(source_id)
    if not source:
        raise KeyError(source_id)
    source["_manual"] = manual
    lock = _lock_for(source_id)
    if lock.locked():
        return {"source_id": source_id, "ok": False, "error": "正在抓取中"}
    async with lock:
        s = load_settings()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO source_state(source_id, last_run, running) VALUES(?,?,1) "
                "ON CONFLICT(source_id) DO UPDATE SET last_run=excluded.last_run, running=1",
                (source_id, now_iso()),
            )
            has_items = conn.execute("SELECT 1 FROM items WHERE source_id=? LIMIT 1", (source_id,)).fetchone()
        first_run = not has_items
        ctx = FetchContext.create(s, source)
        result: dict[str, Any] = {"source_id": source_id, "ok": True, "fetched": 0, "new": 0}
        stop_error: FetchError | None = None
        try:
            fetcher = FETCHERS.get(source.get("type", ""))
            if fetcher is None:
                raise FetchError(f"不支持的信源类型：{source.get('type')}")
            if source.get("type") in ("playwright", "x"):  # 浏览器只给新条目补抓正文 / X 碰到抓过的推文就停
                with db.get_conn() as conn:
                    source["_known_keys"] = {r[0] for r in conn.execute(
                        "SELECT url_key FROM items WHERE source_id=?", (source_id,)).fetchall()}
            try:
                raw = await fetcher(source, ctx)
            except FetchError as e:
                if not getattr(e, "partial", None):
                    raise
                raw, stop_error = e.partial, e  # X 中途被拦：已拿到的照常入库，再报错
            result["fetched"] = len(raw)
            # 只给“新”条目补抓正文（静态页面）
            detail = source.get("detail") or {}
            if source.get("type") == "html_list" and detail.get("enabled"):
                with db.get_conn() as conn:
                    fresh = [r for r in raw if not conn.execute(
                        "SELECT 1 FROM items WHERE url_key=?", (sha1(normalize_url(r.url)),)).fetchone()]
                cap = int(detail.get("max", s.fetch.max_detail_fetch))
                sem = asyncio.Semaphore(2)  # 对校内站点温和一些

                async def _one(r: RawItem) -> None:
                    async with sem:
                        await fetch_detail_http(ctx, r, detail)
                        await asyncio.sleep(0.3)

                await asyncio.gather(*[_one(r) for r in fresh[:cap]])
                result["detail_ok"] = sum(1 for r in fresh[:cap] if r.content)
            ids = insert_items(source, raw, s, first_run)
            apply_rules_to(ids, s)
            result["new"] = len(ids)
            if stop_error is not None:
                with db.get_conn() as conn:
                    conn.execute("UPDATE source_state SET last_count=?, last_new=?, total=total+? WHERE source_id=?",
                                 (len(raw), len(ids), len(ids), source_id))
                raise stop_error
            with db.get_conn() as conn:
                conn.execute(
                    """UPDATE source_state SET last_success=?, last_error=NULL, last_count=?, last_new=?, total=total+?,
                       runs=runs+1, failures=0, running=0 WHERE source_id=?""",
                    (now_iso(), len(raw), len(ids), len(ids), source_id),
                )
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, FetchError) else f"{type(e).__name__}: {e}"
            log.warning("信源 %s 失败：%s", source_id, msg)
            result.update(ok=False, error=msg)
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE source_state SET last_error=?, runs=runs+1, failures=failures+1, running=0 WHERE source_id=?",
                    (msg[:500], source_id),
                )
        finally:
            await ctx.aclose()
        # 抓取后不调用模型：新条目先用规则分，等每日谷时批量打分（pipeline/batch.py）
        return result


async def preview_source(source: dict[str, Any]) -> dict[str, Any]:
    """试抓（不入库）：用于在保存前检查选择器是否写对。"""
    s = load_settings()
    if source.get("type") == "x":  # X 不试抓：避免为了预览额外打开 x.com，只检查配置
        from ..fetchers.x import targets_of

        targets = targets_of(source)
        return {"ok": bool(targets), "count": len(targets), "preview_only": True,
                "error": "" if targets else "没有有效的关键词或账号",
                "items": [{"title": f"{'搜索' if k == 'search' else '主页'} · {label}", "url": url,
                           "published_at": None, "summary": ""} for k, label, url in targets]}
    if source.get("type") == "email":  # 邮件不试抓（会推进 UID 游标）：只测连接
        from ..fetchers import mail

        try:
            r = await asyncio.to_thread(mail.test_connection, source, s)
            return {"ok": True, "count": r["count"], "preview_only": True, "items": [
                {"title": f"连接成功：{r['host']} 收件箱共 {r['count']} 封", "url": "", "published_at": None, "summary": ""}]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "items": []}
    ctx = FetchContext.create(s, source)
    try:
        fetcher = FETCHERS[source.get("type", "")]
        raw = await fetcher(source, ctx)
        return {"ok": True, "count": len(raw), "items": [
            {"title": r.title, "url": r.url, "published_at": to_iso(r.published_at), "summary": r.summary[:200]}
            for r in raw[:15]]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "items": []}
    finally:
        await ctx.aclose()


def ingest_external(source_id: str, items: list[dict[str, Any]]) -> list[int]:
    """外部推送入口（飞书长连接、自定义脚本等），与其他信源走同一流水线。"""
    s = load_settings()
    source = config.get_source(source_id) or {"id": source_id, "category": "news", "tier": "T2"}
    raw = [RawItem(title=i["title"], url=i["url"], content=i.get("content", ""), summary=i.get("summary", ""),
                   published_at=None) for i in items if i.get("title") and i.get("url")]
    ids = insert_items(source, raw, s, first_run=False)
    apply_rules_to(ids, s)
    return ids
