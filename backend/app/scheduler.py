"""APScheduler 调度：信源抓取、X 每日抓取、每日谷时批量打分，以及关机 / 休眠后的补抓。

任务一览（全部在内存里，后端重启时由 sync_jobs() 按 sources.yaml 重建）：

- ``source:<id>``  普通信源：按各自间隔（IntervalTrigger）抓取，启动时错峰 5~90 秒先抓一轮。
- ``source:<id>``  X 信源：每天一次（默认 07:00，CronTrigger），多个 X 信源依次错开 20 分钟。
- ``xcatch:<id>``  X 补抓：今天的定时点错过了（关机 / 休眠）且今天还没抓过，就补抓一次。
- ``score-batch``  每分钟检查一次是否该做每日谷时批量打分（逻辑见 pipeline/batch.py）。
- ``watch``        每分钟一次的看门狗：
    1. 发现两次检查之间的墙钟间隔超过 RESUME_GAP（电脑刚从休眠 / 睡眠醒来），
       把“已经过了间隔还没抓”的普通信源错峰提前到 5~90 秒内各抓一轮；
    2. 检查 X 今天是否需要补抓（休眠跨过了 07:00、醒来时已过 07:30 的情况）。

关机则什么都不跑；下次开机时启动错峰抓一轮 + X 补抓 + 打分补跑都会接上。
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db
from .pipeline import batch
from .pipeline.runner import run_source
from .textutil import TZ, from_iso, now

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler(timezone=TZ)

RESUME_GAP = timedelta(minutes=5)  # 每分钟一次的检查之间隔了这么久，就认为电脑刚醒来
# X 定时任务的 misfire_grace_time 是 30 分钟：这段时间内醒来由 APScheduler 自己补跑定时任务，
# 看门狗只在定时点 35 分钟以后才接手补抓，避免两边同时跑
X_MISFIRE_GRACE_S = 1800
X_WATCH_AFTER_MINUTES = 35

_last_tick: datetime | None = None


def _job_id(source_id: str) -> str:
    return f"source:{source_id}"


def _catch_id(source_id: str) -> str:
    return f"xcatch:{source_id}"


def _wanted_sources() -> dict[str, dict[str, Any]]:
    """需要调度的信源：已启用、不是飞书（飞书走长连接）。key 为任务 id，保持 sources.yaml 中的顺序。"""
    return {_job_id(src["id"]): src for src in config.load_sources()
            if src.get("enabled") and src.get("type") != "feishu"}


def _x_sources_indexed() -> list[tuple[int, dict[str, Any]]]:
    """已启用的 X 信源及其序号（序号决定错开的时间，和 sync_jobs 一致）。"""
    xs = [src for src in _wanted_sources().values() if src.get("type") == "x"]
    return list(enumerate(xs))


def _interval_minutes(src: dict[str, Any], s: config.Settings) -> int:
    return int(src.get("interval_minutes") or s.fetch.default_interval_minutes)


def _stagger(i: int) -> timedelta:
    """错峰：第 i 个信源在 5~90 秒内分散启动，避免同时打满校内站点。"""
    return timedelta(seconds=5 + i * 4 + random.randint(0, 10))


def sync_jobs(initial_delay: bool = True) -> None:
    """根据 sources.yaml 重建信源任务（启动、保存信源后调用）。"""
    s = config.load_settings()
    wanted = _wanted_sources()
    for job in scheduler.get_jobs():
        if job.id.startswith("source:") and job.id not in wanted:
            job.remove()
        elif job.id.startswith("xcatch:") and _job_id(job.id.removeprefix("xcatch:")) not in wanted:
            job.remove()
    x_index = 0
    for i, (jid, src) in enumerate(wanted.items()):
        if src.get("type") == "x":
            _add_x_job(jid, src, s, x_index)
            x_index += 1
            continue
        start = now() + (_stagger(i) if initial_delay else timedelta(seconds=1))
        scheduler.add_job(run_source, IntervalTrigger(minutes=_interval_minutes(src, s), start_date=start, jitter=60,
                                                      timezone=TZ),
                          id=jid, args=[src["id"]], replace_existing=True, max_instances=1, coalesce=True,
                          misfire_grace_time=600)
    # 打分：启动 3 分钟后才开始检查，先让信源抓一轮
    if not scheduler.get_job("score-batch"):
        scheduler.add_job(batch.tick, IntervalTrigger(minutes=1, start_date=now() + timedelta(minutes=3), timezone=TZ),
                          id="score-batch", max_instances=1, coalesce=True, misfire_grace_time=120)
    if not scheduler.get_job("watch"):
        scheduler.add_job(tick, IntervalTrigger(minutes=1, start_date=now() + timedelta(minutes=1), timezone=TZ),
                          id="watch", max_instances=1, coalesce=True, misfire_grace_time=120)


# ---------------------------------------------------------------- X：每天一次 + 补抓
def _x_time(s: config.Settings, index: int) -> tuple[int, int]:
    h, m = (int(x) for x in s.x.time.split(":"))
    total = (h * 60 + m + 20 * index) % (24 * 60)
    return total // 60, total % 60


def x_catch_up_due(src_id: str, s: config.Settings, index: int = 0, *, after_minutes: int = 0) -> bool:
    """今天的定时点（再加 after_minutes）已过、今天还没跑过、也没有被拦：需要补抓一次。"""
    from .fetchers import x as x_mod

    if not s.x.catch_up or x_mod.scheduled_skip_reason():
        return False
    h, m = _x_time(s, index)
    t = now()
    slot = t.replace(hour=h, minute=m, second=0, microsecond=0)
    if t < slot + timedelta(minutes=after_minutes):
        return False
    with db.get_conn() as c:
        row = c.execute("SELECT last_run FROM source_state WHERE source_id=?", (src_id,)).fetchone()
    return not (row and row[0] and str(row[0])[:10] == t.strftime("%Y-%m-%d"))


def _schedule_x_catch_up(src_id: str, index: int) -> bool:
    """排一个补抓任务（已经排了就不重复排）；多个 X 信源依次错开 3 分钟。"""
    cid = _catch_id(src_id)
    if scheduler.get_job(cid):
        return False
    at = now() + timedelta(minutes=2 + 3 * index + random.random())
    scheduler.add_job(run_x_catch_up, DateTrigger(at), id=cid, args=[src_id, index], replace_existing=True)
    log.info("X 今天还没抓：%s 将在 %s 补抓", src_id, at.strftime("%H:%M:%S"))
    return True


def _add_x_job(jid: str, src: dict[str, Any], s: config.Settings, index: int) -> None:
    h, m = _x_time(s, index)
    scheduler.add_job(run_x_scheduled, CronTrigger(hour=h, minute=m, jitter=180, timezone=TZ), id=jid,
                      args=[src["id"]], replace_existing=True, max_instances=1, coalesce=True,
                      misfire_grace_time=X_MISFIRE_GRACE_S)
    # 刚启动 / 改了信源：定时点今天已过、今天没抓过 → 补抓（新加的 CronTrigger 只会从明天开始）
    if x_catch_up_due(src["id"], s, index):
        _schedule_x_catch_up(src["id"], index)
    elif scheduler.get_job(_catch_id(src["id"])):
        scheduler.remove_job(_catch_id(src["id"]))


async def run_x_scheduled(source_id: str) -> None:
    """定时的 X 任务：被拦过就不自动重试，等你在界面上重新登录。"""
    from .fetchers import x as x_mod

    reason = x_mod.scheduled_skip_reason()
    if reason and "运行" not in reason:
        log.info("跳过 X 定时抓取 %s：%s", source_id, reason)
        return
    await run_source(source_id)  # 正在运行的话会在 X 锁上排队，保证同一时间只有一个 X 任务


async def run_x_catch_up(source_id: str, index: int = 0) -> None:
    """X 补抓：执行前再确认一次今天确实还没抓过（期间定时任务可能已经跑了），保证一天只抓一次。"""
    if not x_catch_up_due(source_id, config.load_settings(), index):
        log.info("X 补抓 %s 取消：今天已抓过、已被拦或关闭了补抓", source_id)
        return
    await run_x_scheduled(source_id)


def check_x_catch_up() -> list[str]:
    """看门狗调用：休眠跨过了 X 定时点、醒来时已超出定时任务的补跑窗口 → 补抓。返回新排的信源。"""
    s = config.load_settings()
    out = []
    for index, src in _x_sources_indexed():
        if x_catch_up_due(src["id"], s, index, after_minutes=X_WATCH_AFTER_MINUTES) and \
                _schedule_x_catch_up(src["id"], index):
            out.append(src["id"])
    return out


# ---------------------------------------------------------------- 休眠醒来：普通信源补抓一轮
def resume_round() -> list[str]:
    """把已经过了抓取间隔的普通信源错峰提前到 5~90 秒内各抓一轮（不新增任务，只提前原任务的下次运行时间，
    所以不会和原任务重复跑）。刚抓过、或本来马上就要跑的跳过。返回被提前的信源。"""
    s = config.load_settings()
    t = now()
    with db.get_conn() as c:
        last = {r[0]: from_iso(r[1]) for r in c.execute("SELECT source_id, last_run FROM source_state").fetchall()}
    out: list[str] = []
    for jid, src in _wanted_sources().items():
        if src.get("type") == "x":
            continue
        job = scheduler.get_job(jid)
        if job is None:
            continue
        lr = last.get(src["id"])
        if lr is not None and t - lr < timedelta(minutes=_interval_minutes(src, s)):
            continue  # 间隔还没到：没错过什么
        at = t + _stagger(len(out))
        if job.next_run_time is not None and job.next_run_time <= at:
            continue
        job.modify(next_run_time=at)
        out.append(src["id"])
    return out


def detect_resume(t: datetime) -> bool:
    """记录本次检查时间；和上次相隔超过 RESUME_GAP 返回 True（第一次检查不算）。"""
    global _last_tick
    resumed = _last_tick is not None and t - _last_tick > RESUME_GAP
    _last_tick = t
    return resumed


async def tick() -> None:
    """每分钟一次的看门狗。"""
    t = now()
    if detect_resume(t):
        ids = resume_round()
        log.info("检测到电脑刚从休眠恢复，补抓 %d 个信源：%s", len(ids), ", ".join(ids) or "无")
    check_x_catch_up()


# ---------------------------------------------------------------- 生命周期
def start() -> None:
    if not scheduler.running:
        sync_jobs()
        scheduler.start()
        log.info("调度器已启动：%d 个任务", len(scheduler.get_jobs()))


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def next_runs() -> dict[str, str | None]:
    """各信源下次运行时间（X 有补抓排着时显示补抓时间）。"""
    out: dict[str, datetime | None] = {}
    jobs = [(j.id, getattr(j, "next_run_time", None)) for j in scheduler.get_jobs()]  # 调度器启动前没有这个属性
    for jid, nrt in jobs:
        if jid.startswith("source:"):
            out[jid.removeprefix("source:")] = nrt
    for jid, nrt in jobs:
        sid = jid.removeprefix("xcatch:")
        if jid.startswith("xcatch:") and nrt and sid in out and (out[sid] is None or nrt < out[sid]):
            out[sid] = nrt
    return {k: (v.isoformat(timespec="seconds") if v else None) for k, v in out.items()}
