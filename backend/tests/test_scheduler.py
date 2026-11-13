"""调度：关机 / 休眠醒来后的补抓（X 每日任务、普通信源、打分）。"""
from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import config, db, scheduler
from app.fetchers import x as xmod
from app.pipeline import batch
from app.textutil import TZ

X_SRC = {"id": "x-test", "type": "x", "enabled": True, "keywords": ["DeepSeek"]}
RSS = {"id": "rss1", "type": "rss", "enabled": True, "url": "u", "interval_minutes": 60}
RSS2 = {"id": "rss2", "type": "rss", "enabled": True, "url": "u", "interval_minutes": 60}


def T(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture()
def sched(tmp_env, monkeypatch):
    db.init_db()
    clock = Clock(T("2026-11-11 06:00"))
    monkeypatch.setattr(scheduler, "now", clock)
    monkeypatch.setattr(scheduler, "_last_tick", None)
    monkeypatch.setattr(config, "load_sources", lambda: [X_SRC, RSS, RSS2])
    monkeypatch.setattr(scheduler, "scheduler", AsyncIOScheduler(timezone=TZ))  # 每个测试一个干净的调度器
    yield clock


def _set_last_run(sid: str, iso: str) -> None:
    with db.get_conn() as c:
        c.execute("INSERT INTO source_state(source_id, last_run) VALUES(?, ?) "
                  "ON CONFLICT(source_id) DO UPDATE SET last_run=excluded.last_run", (sid, iso))


# ---------------------------------------------------------------- X：休眠跨过 07:00
async def test_x_catch_up_after_wake_at_0840(sched):
    scheduler.sync_jobs()  # 06:00 启动：还没到点，不补抓
    scheduler.scheduler.start(paused=True)
    assert scheduler.scheduler.get_job("xcatch:x-test") is None
    _set_last_run("x-test", "2026-11-10T07:01:00+08:00")  # 上次是昨天
    sched.t = T("2026-11-11 08:40")  # 07:00 在休眠，08:40 醒来
    assert scheduler.check_x_catch_up() == ["x-test"]
    job = scheduler.scheduler.get_job("xcatch:x-test")
    assert job is not None and job.func is scheduler.run_x_catch_up
    assert scheduler.check_x_catch_up() == []  # 已经排上了，不重复排
    assert scheduler.next_runs()["x-test"] == job.trigger.run_date.isoformat(timespec="seconds")  # 界面显示补抓时间
    scheduler.scheduler.shutdown(wait=False)


def test_x_watch_leaves_misfire_window_to_cron_job(sched):
    """07:00~07:35 之间醒来：APScheduler 会自己补跑定时任务，看门狗不插手。"""
    scheduler.sync_jobs()
    sched.t = T("2026-11-11 07:20")
    assert scheduler.check_x_catch_up() == []
    sched.t = T("2026-11-11 07:36")
    assert scheduler.check_x_catch_up() == ["x-test"]


def test_x_no_catch_up_if_ran_today_or_blocked(sched, tmp_env):
    scheduler.sync_jobs()
    sched.t = T("2026-11-11 08:40")
    _set_last_run("x-test", "2026-11-11T07:02:00+08:00")
    assert scheduler.check_x_catch_up() == []  # 今天已经抓过
    _set_last_run("x-test", "2026-11-10T07:02:00+08:00")
    xmod._set_status(blocked=True, state="login", message="登录失效")
    assert scheduler.check_x_catch_up() == []  # 被拦：等你重新登录
    xmod.clear_block()
    tmp_env.x.catch_up = False
    assert scheduler.check_x_catch_up() == []  # 关闭了补抓


async def test_x_catch_up_rechecks_before_running(sched, monkeypatch):
    called = []

    async def fake_run(sid, **kw):
        called.append(sid)

    monkeypatch.setattr(scheduler, "run_source", fake_run)
    sched.t = T("2026-11-11 08:45")
    _set_last_run("x-test", "2026-11-11T08:41:00+08:00")  # 排上补抓后定时任务 / 手动已经跑过
    await scheduler.run_x_catch_up("x-test")
    assert called == []
    _set_last_run("x-test", "2026-11-10T07:01:00+08:00")
    await scheduler.run_x_catch_up("x-test")
    assert called == ["x-test"]


async def test_watch_tick_schedules_x_catch_up(sched):
    scheduler.sync_jobs()
    sched.t = T("2026-11-11 08:40")
    await scheduler.tick()
    assert scheduler.scheduler.get_job("xcatch:x-test") is not None


# ---------------------------------------------------------------- 普通信源：休眠醒来补抓一轮
def test_detect_resume_gap():
    scheduler._last_tick = None
    t = T("2026-11-11 08:00")
    assert not scheduler.detect_resume(t)  # 第一次检查不算
    assert not scheduler.detect_resume(t + timedelta(minutes=1))
    assert not scheduler.detect_resume(t + timedelta(minutes=3))
    assert scheduler.detect_resume(t + timedelta(hours=2))  # 隔了两小时：刚醒来
    assert not scheduler.detect_resume(t + timedelta(hours=2, minutes=1))  # 之后恢复正常，不再触发
    scheduler._last_tick = None


async def test_resume_triggers_one_staggered_round(sched):
    sched.t = T("2026-11-11 08:00")
    scheduler.sync_jobs()
    scheduler.scheduler.start(paused=True)  # 只算时间，不真的跑任务
    _set_last_run("rss1", "2026-11-11T07:00:00+08:00")  # 一小时前（间隔 60 分钟）→ 该抓
    _set_last_run("rss2", "2026-11-11T08:50:00+08:00")  # 刚抓过 → 跳过
    await scheduler.tick()  # 第一次检查
    sched.t = T("2026-11-11 09:00")
    for j in ("source:rss1", "source:rss2"):  # 原定的下一次放到很后面，看是否被提前
        scheduler.scheduler.get_job(j).modify(next_run_time=sched.t + timedelta(minutes=50))
    await scheduler.tick()  # 隔了一小时
    nr1 = scheduler.scheduler.get_job("source:rss1").next_run_time
    nr2 = scheduler.scheduler.get_job("source:rss2").next_run_time
    assert timedelta(seconds=5) <= nr1 - sched.t <= timedelta(seconds=90)
    assert nr2 - sched.t == timedelta(minutes=50)
    assert scheduler.scheduler.get_job("source:x-test").func is scheduler.run_x_scheduled  # X 不走这条
    # 下一分钟：不再重复触发
    scheduler.scheduler.get_job("source:rss1").modify(next_run_time=sched.t + timedelta(minutes=50))
    sched.t = T("2026-11-11 09:01")
    await scheduler.tick()
    assert scheduler.scheduler.get_job("source:rss1").next_run_time - sched.t > timedelta(minutes=40)
    scheduler.scheduler.shutdown(wait=False)


async def test_resume_round_skips_job_already_due(sched):
    sched.t = T("2026-11-11 09:00")
    scheduler.sync_jobs()
    scheduler.scheduler.start(paused=True)
    scheduler.scheduler.get_job("source:rss1").modify(next_run_time=sched.t + timedelta(seconds=2))
    assert "rss1" not in scheduler.resume_round()  # 本来马上就要跑：不动
    assert scheduler.scheduler.get_job("source:rss1").next_run_time == sched.t + timedelta(seconds=2)
    scheduler.scheduler.shutdown(wait=False)


def test_watch_and_score_jobs_registered(sched):
    scheduler.sync_jobs()
    assert scheduler.scheduler.get_job("watch").func is scheduler.tick
    assert scheduler.scheduler.get_job("score-batch").func is batch.tick


# ---------------------------------------------------------------- 打分：醒来后补跑（逻辑没改，这里只确认）
def test_score_batch_catches_up_after_wake():
    from app import offpeak
    from app.config import ScoringSettings

    R, SC = offpeak.parse_rules(None), ScoringSettings()
    # 周三 07:30 在休眠，08:40 醒来（谷时）→ 立即补跑
    p = batch.plan(T("2026-11-11 08:40"), T("2026-11-10 07:35"), SC, R)
    assert p.due and p.reason == "catch_up"
    # 10:30 醒来（高峰）→ 等到 12:00
    p = batch.plan(T("2026-11-11 10:30"), T("2026-11-10 07:35"), SC, R)
    assert not p.due and p.reason == "wait_offpeak" and p.next_at.hour == 12
