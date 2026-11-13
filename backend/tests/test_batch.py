"""fix2：每日一次谷时批量打分——峰谷判断、定时/补跑/顺延、汇总通知、规则紧急提醒、接口。"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

os.environ["INFO_STATION_NO_SCHEDULER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, offpeak  # noqa: E402
from app.config import ScoringSettings, Settings  # noqa: E402
from app.fetchers.base import RawItem  # noqa: E402
from app.pipeline import batch, runner  # noqa: E402
from app.textutil import TZ, now  # noqa: E402

R = offpeak.parse_rules(None)  # 内置默认 = 官方规则


def T(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


# ---------------------------------------------------------------- 峰谷规则
@pytest.mark.parametrize("ts,peak", [
    ("2026-11-11 07:30", False),  # 周三早上
    ("2026-11-11 09:00", True),
    ("2026-11-11 11:59", True),
    ("2026-11-11 12:00", False),  # 午间谷时
    ("2026-11-11 13:59", False),
    ("2026-11-11 14:00", True),
    ("2026-11-11 18:00", False),
    ("2026-11-14 10:00", False),  # 周六
    ("2026-11-15 15:00", False),  # 周日
    ("2026-10-05 10:00", False),  # 国庆放假（周一）
    ("2026-10-10 10:00", False),  # 调休上班的周六：DeepSeek 仍按空闲计费
    ("2027-06-09 10:00", False),  # 2027 端午（周三）
    ("2027-06-10 10:00", True),
])
def test_is_peak_matches_official_rules(ts, peak):
    assert offpeak.is_peak(T(ts), R) is peak


def test_next_offpeak_and_peak_start():
    assert offpeak.next_offpeak(T("2026-11-11 10:15"), R) == T("2026-11-11 12:00")
    assert offpeak.next_offpeak(T("2026-11-11 16:00"), R) == T("2026-11-11 18:00")
    assert offpeak.next_offpeak(T("2026-11-11 20:00"), R) == T("2026-11-11 20:00")
    assert offpeak.next_peak_start(T("2026-11-13 19:00"), R) == T("2026-11-16 09:00")  # 周五晚 → 下周一
    assert offpeak.time_is_peak_on_workdays("07:30", R) is False
    assert offpeak.time_is_peak_on_workdays("09:30", R) is True


def test_rules_file_overrides(tmp_path, monkeypatch):
    (tmp_path / "deepseek_offpeak.yaml").write_text(
        'peak_weekdays: [0,1,2,3,4]\npeak_windows: [["08:00", "20:00"]]\nholidays: [2026-11-11]\n', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    r = offpeak.rules()
    assert not offpeak.is_peak(T("2026-11-11 10:00"), r)  # 当作节假日
    assert offpeak.is_peak(T("2026-11-12 19:00"), r)


# ---------------------------------------------------------------- 计划：定时 / 补跑 / 顺延
SC = ScoringSettings()  # 07:30


def test_plan_scheduled_and_done():
    p = batch.plan(T("2026-11-11 07:31"), T("2026-11-10 07:40"), SC, R)
    assert p.due and p.reason == "scheduled"
    p = batch.plan(T("2026-11-11 08:00"), T("2026-11-11 07:35"), SC, R)
    assert not p.due and p.reason == "done" and p.next_at == T("2026-11-12 07:30") and not p.next_is_peak


def test_plan_catch_up_when_offpeak():
    # 07:30 没开机，08:40 开机（工作日，仍是谷时）→ 立即补跑
    p = batch.plan(T("2026-11-11 08:40"), T("2026-11-10 07:35"), SC, R)
    assert p.due and p.reason == "catch_up"
    # 周六 10:00 开机也是谷时
    assert batch.plan(T("2026-11-14 10:00"), T("2026-11-13 07:35"), SC, R).reason == "catch_up"
    # 首次安装（从没跑过）同样补跑
    assert batch.plan(T("2026-11-11 20:00"), None, SC, R).due


def test_plan_defers_to_next_offpeak_window():
    p = batch.plan(T("2026-11-11 10:00"), T("2026-11-10 07:35"), SC, R)
    assert not p.due and p.reason == "wait_offpeak" and p.next_at == T("2026-11-11 12:00")
    # 到了 12:00 就补跑
    assert batch.plan(T("2026-11-11 12:00"), T("2026-11-10 07:35"), SC, R).reason == "catch_up"
    p = batch.plan(T("2026-11-11 15:00"), T("2026-11-10 07:35"), SC, R)
    assert p.next_at == T("2026-11-11 18:00")


def test_plan_skips_catch_up_close_to_next_slot_and_can_be_disabled():
    p = batch.plan(T("2026-11-12 06:30"), T("2026-11-10 07:35"), SC, R)  # 昨天漏了，但一小时后就是定时点
    assert not p.due and p.reason == "near_next" and p.next_at == T("2026-11-12 07:30")
    off = ScoringSettings(catch_up=False)
    assert batch.plan(T("2026-11-11 20:00"), T("2026-11-10 07:35"), off, R).reason == "no_catch_up"


def test_plan_user_chosen_peak_time_still_runs_on_time():
    sc = ScoringSettings(time="10:00")
    p = batch.plan(T("2026-11-11 10:02"), T("2026-11-10 10:05"), sc, R)
    assert p.due and p.reason == "scheduled"
    assert batch.plan(T("2026-11-11 08:00"), T("2026-11-10 10:05"), sc, R).next_is_peak  # 设置页据此提示


def test_scoring_time_validation():
    assert ScoringSettings(time="7:05").time == "07:05"
    with pytest.raises(ValueError):
        ScoringSettings(time="25:00")


# ---------------------------------------------------------------- 批量执行
def _insert(s, title, *, deadline_days=None, ago_h=1):
    content = ""
    if deadline_days is not None:
        d = now() + timedelta(days=deadline_days)
        content = f"请于{d.month}月{d.day}日前报名。"
    return runner.insert_items({"id": "a", "category": "competition", "tier": "T1"},
                               [RawItem(title, f"https://a.edu/{abs(hash(title))}", now() - timedelta(hours=ago_h),
                                        content=content)], s, first_run=False)[0]


@pytest.fixture()
def llm_on(tmp_env, monkeypatch):
    monkeypatch.setattr(runner, "llm_active", lambda s: True)
    monkeypatch.setattr(batch, "llm_active", lambda s: True)
    return tmp_env


def test_new_items_keep_rule_score_and_only_urgent_rule_notify(llm_on):
    s: Settings = llm_on
    s.thresholds.notify = 60
    urgent = _insert(s, "2025级本科生大创项目申报报名通知", deadline_days=2)
    normal = _insert(s, "2025级本科生大创项目申报通知（第二批）")
    runner.apply_rules_to([urgent, normal], s)
    with db.get_conn() as c:
        rows = {r["id"]: r for r in c.execute("SELECT id, status, notified FROM items")}
        n = c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert rows[urgent]["status"] == rows[normal]["status"] == "new"  # 等批量打分
    assert rows[urgent]["notified"] == 1 and rows[normal]["notified"] == 0 and n == 1


def _fake_scorer(score=90, calls=None):
    async def fake(limit=None, *, notify=True):
        assert notify is False  # 批量模式不逐条弹通知
        with db.get_conn() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM items WHERE status='new' LIMIT ?", (limit,))]
            for i in ids:
                c.execute("UPDATE items SET status='scored', score=?, scored_by='deepseek-flash' WHERE id=?", (score, i))
        if calls is not None:
            calls.append(len(ids))
        return {"scored": len(ids), "failed": 0}
    return fake


def test_run_batch_scores_all_builds_brief_and_one_summary(llm_on, monkeypatch):
    s: Settings = llm_on
    s.llm.max_items_per_run = 2
    for i in range(5):
        _insert(s, f"测试竞赛报名通知 第{i}个完全不同的标题{i * 7919}")
    with db.get_conn() as c:
        c.execute("UPDATE items SET status='new'")
    calls = []
    monkeypatch.setattr(batch, "score_pending", _fake_scorer(90, calls))
    monkeypatch.setattr(offpeak, "is_peak", lambda t, r=None: False)
    res = asyncio.run(batch.run_batch("scheduled"))
    assert res["scored"] == 5 and calls == [2, 2, 1] and res["pending_left"] == 0
    with db.get_conn() as c:
        notes = c.execute("SELECT title, body FROM notifications").fetchall()
        assert c.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM items WHERE notified=1").fetchone()[0] == 5  # 已被汇总覆盖
    assert len(notes) == 1 and "已打分 5 条" in notes[0]["body"]
    assert db.get_state(batch.STATE_KEY)["last_done_at"]


def test_run_batch_pauses_when_peak_starts(llm_on, monkeypatch):
    s: Settings = llm_on
    s.llm.max_items_per_run = 1
    for i in range(3):
        _insert(s, f"另一组标题 {i} 号 {i * 104729}")
    with db.get_conn() as c:
        c.execute("UPDATE items SET status='new'")
    flips = iter([False, True, True, True])
    monkeypatch.setattr(offpeak, "is_peak", lambda t, r=None: next(flips))
    monkeypatch.setattr(batch, "score_pending", _fake_scorer())
    res = asyncio.run(batch.run_batch("catch_up"))
    assert res["scored"] == 1 and res["paused_at_peak"] and res["pending_left"] == 2
    assert not (db.get_state(batch.STATE_KEY) or {}).get("last_done_at")  # 没跑完 → 下个谷时继续


def test_run_source_no_longer_triggers_llm():
    import inspect
    assert "score_pending" not in inspect.getsource(runner.run_source)


# ---------------------------------------------------------------- 接口
def test_score_api_peak_confirm_and_schedule(tmp_env, monkeypatch, tmp_path):
    from app.main import app

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    monkeypatch.setattr(offpeak, "is_peak", lambda t, r=None: True)
    monkeypatch.setattr(offpeak, "next_offpeak", lambda t, r=None: T("2026-11-11 12:00"))
    with TestClient(app) as c:
        st = c.get("/api/score/schedule").json()
        assert st["time"] == "07:30" and st["now_is_peak"] and st["next_at"] and st["peak_windows"][0] == ["09:00", "12:00"]
        r = c.post("/api/score/run").json()
        assert r["needs_confirm"] and not r["started"] and r["next_offpeak"].startswith("2026-11-11T12:00")
        assert c.post("/api/rescore?only_pending=false").json()["needs_confirm"]
        assert c.post("/api/score/run?force=true").json()["started"]
        s = c.put("/api/settings", json={"scoring": {"time": "10:00"}}).json()
        assert s["scoring"]["time"] == "10:00"
        assert c.get("/api/score/schedule").json()["time_is_peak_on_workdays"] is True
        assert c.put("/api/settings", json={"scoring": {"time": "99:00"}}).status_code in (400, 422)
