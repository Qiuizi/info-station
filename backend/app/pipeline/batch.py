"""每日一次的批量模型打分（放在 DeepSeek 谷时）。

- 抓取照常按各信源间隔进行，新条目先只有规则分（规则能看出三天内截止的仍按规则提醒）。
- 每天在设置的时刻（默认北京时间 07:30，任何日子都是谷时）把所有待打分条目批量送模型，
  完成后生成当日简报，并发一条汇总的 Windows 通知。
- 定时点电脑没开机：开机后若处于谷时就补跑；若处于高峰，顺延到这个高峰窗口结束（下一个谷时）。
- 批量进行中进入高峰会暂停，余下的在下一个谷时继续（用户在高峰时手动强制打分除外）。

调度器每分钟调用一次 tick()；是否该跑由纯函数 plan() 决定，便于测试。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import brief as brief_mod
from .. import config, db, offpeak
from ..config import Settings, load_settings
from ..textutil import TZ, from_iso, now, now_iso
from . import llm as llm_mod
from .runner import llm_active, score_pending, threshold_for

log = logging.getLogger("batch")

STATE_KEY = "score_batch"
ON_TIME_MINUTES = 10  # 定时点后 10 分钟内算“按时”，用户选的时刻即使在高峰也照跑（设置页已提示）

_lock = asyncio.Lock()
_running: dict[str, Any] = {"running": False, "trigger": None, "started_at": None}


@dataclass
class Plan:
    due: bool
    reason: str  # scheduled / catch_up / wait_offpeak / near_next / done / no_catch_up
    next_at: datetime  # 下一次会跑的时刻（due 时为现在）
    next_is_peak: bool
    slot: datetime  # 最近一个已到的定时点


def _slot_on(d, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def plan(t: datetime, last_done: datetime | None, sc: config.ScoringSettings,
         r: offpeak.PeakRules | None = None) -> Plan:
    r = r or offpeak.rules()
    t = t.astimezone(TZ)
    today = _slot_on(t.date(), sc.time)
    slot = today if t >= today else _slot_on((t - timedelta(days=1)).date(), sc.time)
    next_slot = _slot_on((slot + timedelta(days=1)).date(), sc.time)

    def mk(due: bool, reason: str, at: datetime) -> Plan:
        return Plan(due, reason, at, offpeak.is_peak(at, r), slot)

    if last_done is not None and last_done >= slot:
        return mk(False, "done", next_slot)
    if t - slot <= timedelta(minutes=ON_TIME_MINUTES):
        return mk(True, "scheduled", t)
    if not sc.catch_up:
        return mk(False, "no_catch_up", next_slot)
    if next_slot - t <= timedelta(minutes=max(0, sc.skip_catchup_within_minutes)):
        return mk(False, "near_next", next_slot)
    if offpeak.is_peak(t, r):
        return mk(False, "wait_offpeak", offpeak.next_offpeak(t, r))
    return mk(True, "catch_up", t)


def _last_done() -> datetime | None:
    st = db.get_state(STATE_KEY, {}) or {}
    return from_iso(st.get("last_done_at"))


def _pending_count() -> int:
    with db.get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM items WHERE status='new'").fetchone()[0]


def _summary_notification(s: Settings, stats: dict[str, Any], b: dict[str, Any], since: str) -> None:
    """一条汇总通知；本轮打出的高分条目标记为已提醒，不再逐条弹出。"""
    with db.get_conn() as c:
        hot = c.execute(
            "SELECT id, title, tier, score FROM items WHERE scored_by != 'rule' AND status='scored' AND notified=0 "
            "AND is_backfill=0 AND (eligible IS NULL OR eligible=1) AND fetched_at >= ? ORDER BY score DESC", (since,)
        ).fetchall()
        hot = [h for h in hot if h["score"] >= threshold_for(h["tier"], s.thresholds.notify, s)]
        for h in hot:
            c.execute("UPDATE items SET notified=1, should_notify=1 WHERE id=?", (h["id"],))
        must = (b.get("sections") or [{}])[0].get("items") or []
        n_dl = (b.get("stats") or {}).get("deadlines", 0)
        parts = []
        if stats.get("scored"):
            parts.append(f"已打分 {stats['scored']} 条")
        if stats.get("error"):
            parts.append(stats["error"])
        if must:
            parts.append(f"必看 {len(must)} 条：" + "；".join(m["title"][:18] for m in must[:3]))
        if n_dl:
            parts.append(f"⏰ 十天内截止 {n_dl} 项")
        if not parts:
            return
        title = "今日信息已整理好" if stats.get("scored") or must else "今日简报已生成"
        c.execute("INSERT INTO notifications(item_id, title, body, created_at) VALUES(?,?,?,?)",
                  (must[0]["id"] if must else None, title, " · ".join(parts)[:220], now_iso()))


async def run_batch(trigger: str = "scheduled", *, allow_peak: bool = False, notify: bool = True) -> dict[str, Any]:
    """把所有待打分条目批量送模型，然后生成简报 + 汇总通知。"""
    if _lock.locked():
        return {"started": False, "running": True}
    async with _lock:
        s = load_settings()
        started = now()
        _running.update(running=True, trigger=trigger, started_at=started.isoformat(timespec="seconds"))
        stats: dict[str, Any] = {"scored": 0, "failed": 0, "trigger": trigger, "started_at": _running["started_at"]}
        complete = True
        try:
            if not llm_active(s):
                stats["error"] = "未配置模型 Key，使用规则分"
            else:
                per = max(1, s.llm.max_items_per_run)
                for _ in range(max(1, s.llm.daily_call_budget // per + 2)):
                    if _pending_count() == 0:
                        break
                    if not allow_peak and s.scoring.stop_at_peak and offpeak.is_peak(now()):
                        complete = False
                        stats["paused_at_peak"] = True
                        break
                    r = await score_pending(per, notify=False)
                    stats["scored"] += r.get("scored", 0)
                    stats["failed"] += r.get("failed", 0)
                    if r.get("paused") or llm_mod.paused():
                        stats["error"] = f"模型暂停：{llm_mod.STATE.get('last_error') or '熔断中'}"
                        break
                    if not r.get("scored") and not r.get("failed"):
                        stats["error"] = "已达每日调用上限" if not r.get("skipped") else "模型未启用"
                        break
            stats["pending_left"] = _pending_count()
            b = brief_mod.build_brief(s)
            stats["brief_date"] = b.get("date")
            if notify:
                _summary_notification(s, stats, b, (started - timedelta(days=2)).isoformat(timespec="seconds"))
        finally:
            _running.update(running=False, trigger=None, started_at=None)
        stats["finished_at"] = now_iso()
        st = db.get_state(STATE_KEY, {}) or {}
        st["last_result"] = stats
        if complete:
            st["last_done_at"] = stats["finished_at"]
        db.set_state(STATE_KEY, st)
        log.info("批量打分（%s）：%s", trigger, stats)
        return {"started": True, **stats}


async def tick() -> dict[str, Any] | None:
    """调度器每分钟调用：到点、补跑、或继续被高峰打断的批次。"""
    s = load_settings()
    p = plan(now(), _last_done(), s.scoring)
    if not p.due or _lock.locked():
        return None
    return await run_batch(p.reason, allow_peak=p.reason == "scheduled")


def status(s: Settings | None = None) -> dict[str, Any]:
    s = s or load_settings()
    t = now()
    r = offpeak.rules()
    p = plan(t, _last_done(), s.scoring, r)
    st = db.get_state(STATE_KEY, {}) or {}
    return {
        "time": s.scoring.time,
        "catch_up": s.scoring.catch_up,
        "plan": {**asdict(p), "next_at": p.next_at.isoformat(timespec="seconds"), "slot": p.slot.isoformat(timespec="seconds")},
        "next_at": (t if p.due else p.next_at).isoformat(timespec="seconds"),
        "next_is_peak": p.next_is_peak,
        "time_is_peak_on_workdays": offpeak.time_is_peak_on_workdays(s.scoring.time, r),
        "now_is_peak": offpeak.is_peak(t, r),
        "next_offpeak": offpeak.next_offpeak(t, r).isoformat(timespec="seconds"),
        "peak_windows": r.windows_text(),
        "pricing_url": offpeak.PRICING_URL,
        "pending": _pending_count(),
        "running": _running["running"],
        "last_done_at": st.get("last_done_at"),
        "last_result": st.get("last_result"),
        "llm_active": llm_active(s),
    }
