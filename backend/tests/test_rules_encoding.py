"""规则与编码：规则分虚高、旧文进必看、截止时间归一化、编码。"""
import ast
import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from app import brief, db, queries
from app.config import Settings, load_rules
from app.fetchers.base import RawItem
from app.pipeline import runner
from app.pipeline.llm import parse_verdict
from app.pipeline.rules import apply_rules, kw_in
from app.textutil import (
    decode_bytes,
    extract_deadline,
    normalize_deadline,
    now,
    parse_date_loose,
    repair_gbk_mojibake,
)

MOJIBAKE = "\u0467\u0423\u0368\u05aa"  # 用户在 Windows 上看到的“ѧУ֪ͨ”
ROOT = Path(__file__).resolve().parents[2]
RULES = load_rules()


# ---------------------------------------------------------------- 截止时间
REF = now().replace(year=2026, month=10, day=28, hour=9, minute=0)


@pytest.mark.parametrize("raw,want", [
    ("2026-11-12 24:00", "2026-11-12T23:59"),
    ("2026-11-12T24:00:00", "2026-11-12T23:59"),
    ("2026年11月12日24点", "2026-11-12T23:59"),
    ("11月12日24点", "2026-11-12T23:59"),        # 没写年份：按参考日期所在年，不会被 dateutil 当成 2024 年
    ("11月12日24:00", "2026-11-12T23:59"),
    ("2026-11-12", "2026-11-12T23:59"),          # 只有日期 → 当天 23:59
    ("2026-11-12 18:00", "2026-11-12T18:00"),
    ("2026-11-12T10:00:00Z", "2026-11-12T18:00"),  # 带 Z 的按 UTC 换算成北京时间
    ("2026-11-12T23:59:00+08:00", "2026-11-12T23:59"),
    ("2026-11-12 08:00+00:00", "2026-11-12T16:00"),
])
def test_normalize_deadline(raw, want):
    dt = normalize_deadline(raw, REF)
    assert dt.isoformat().startswith(want) and dt.utcoffset() == timedelta(hours=8)


def test_model_deadline_24h_and_rule_parenthetical():
    v = parse_verdict(json.dumps({"score": 70, "deadline": "2026-11-12 24:00"}))
    assert v.deadline == "2026-11-12T23:59:00+08:00"
    text = "（一）报名时间：即日起至2026年11月12日24点（学院审核时间截止11月13日中午12点）"
    dl, note = extract_deadline(text, REF)
    assert dl.isoformat() == "2026-11-12T23:59:00+08:00" and "11月12日24点" in note


def test_published_dates_with_timezone_are_converted():
    assert parse_date_loose("2026-11-10T03:00:00Z").isoformat() == "2026-11-10T11:00:00+08:00"  # GitHub API
    assert parse_date_loose("Tue, 10 Nov 2026 03:00:00 GMT").hour == 11
    assert parse_date_loose("2026-11-10").tzinfo is not None
    assert parse_date_loose("11月12日24点") is None  # 没有年份不瞎猜


# ---------------------------------------------------------------- 规则分
def test_keyword_matching_uses_word_boundaries():
    assert kw_in("AI", "AI 赋能教学") and kw_in("AI", "关于AI+教育大赛")
    assert not kw_in("AI", "DETAIL") and not kw_in("AI", "mail@scu.edu.cn")
    assert kw_in("CSP", "CCF CSP 认证") and not kw_in("CSP", "CSPRING")


def _rule(title, body="", cat="school", tier="T1", days_ago=1, src=""):
    s = Settings()
    adj = int((RULES.get("source_adjust") or {}).get(src, 0))
    return apply_rules(title, body, cat, tier, s, RULES, now() - timedelta(days=days_ago), source_adjust=adj)


LONG_BODY = ("本次班会介绍了竞赛、考试纪律、选课流程、AI 课程、奖学金评选、大创项目、实验室开放、CSP 认证报名、"
             "交换项目、暑期课程、四六级考试安排等内容。") * 3


def test_rule_scores_are_capped_and_stale_news_sinks():
    cap = RULES["rule_cap"]
    banhui = _rule("师生携手启新程——2026级新生班会", LONG_BODY, days_ago=42)
    assert banhui.score <= 45 and set(banhui.tags) == set()  # 标签只看标题，不再乱打 竞赛/考试/选课/AI
    xibao = _rule("从2392支队伍中突围！我院“灵犀”团队问鼎全国软件创新大赛", LONG_BODY, "competition", days_ago=150, src="sw-kjcx")
    csp_old = _rule("第42次CCF CSP软件能力认证成绩查询、答卷下载及复议通知", LONG_BODY, "competition", days_ago=120)
    wenchuang = _rule("关于开展四川大学130周年校庆文创作品设计大赛的通知", "作品提交截止时间：2026年3月31日。" + LONG_BODY,
                      days_ago=270)
    for r in (xibao, csp_old, wenchuang):
        assert r.score < 55, r.hits
    fresh = _rule("关于2025级本科生大创项目申报的通知", LONG_BODY, "competition", days_ago=1)
    assert fresh.score <= cap and fresh.score > 60
    # 正文加分有上限：同一标题正文再长也不会无限加
    assert _rule("一般通知", LONG_BODY * 5).score == _rule("一般通知", LONG_BODY).score


# ---------------------------------------------------------------- 送模型的范围 / 启动修正
@pytest.fixture()
def llm_on(tmp_env, monkeypatch):
    tmp_env.llm.enabled = True
    tmp_env.llm.api_key = "sk-test-not-used"  # 只用于判断“已启用模型”，测试里不会发请求
    monkeypatch.setattr(runner, "llm_active", lambda s: True)
    return tmp_env


def _insert(s, title, *, days_ago=1, content="", first_run=False, src="a", cat="school"):
    return runner.insert_items({"id": src, "category": cat, "tier": "T1"},
                               [RawItem(title, f"https://x/{abs(hash(title))}", now() - timedelta(days=days_ago),
                                        content=content)], s, first_run=first_run)[0]


def test_old_items_are_not_queued_for_llm(llm_on):
    s = llm_on
    soon = now() + timedelta(days=20)
    old = _insert(s, "半年前的旧通知", days_ago=180, first_run=True)
    old_open = _insert(s, "旧通知但截止还没到", days_ago=60, content=f"报名截止：{soon.year}年{soon.month}月{soon.day}日")
    fresh = _insert(s, "昨天的新通知", days_ago=1)
    runner.apply_rules_to([old, old_open, fresh], s)
    with db.get_conn() as c:
        st = {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM items")}
    assert st[old] == "rule" and st[old_open] == "new" and st[fresh] == "new"


def test_refresh_rule_scores_fixes_inflated_rows_once(llm_on):
    s = llm_on
    a = _insert(s, "师生携手启新程——2026级新生班会", days_ago=42, content=LONG_BODY)
    b = _insert(s, "关于选派本科生参加中希学分项目的通知", days_ago=14)
    note = "（一）报名时间：即日起至2026年11月12日24点（学院审核时间截止11月13日中午12点）"
    with db.get_conn() as c:  # 模拟旧版本留下的数据
        c.execute("UPDATE items SET score=100, rule_score=100, status='new', scored_by='rule', "
                  "tags='[\"竞赛\",\"考试\",\"选课\",\"AI\"]' WHERE id=?", (a,))
        c.execute("UPDATE items SET score=72, status='scored', scored_by='deepseek-flash', summary='x', "
                  "tags='[\"交换项目\",\"竞赛\",\"考试\",\"交流\"]', "
                  "deadline='2026-11-13T12:00:00+08:00', deadline_note=? WHERE id=?", (note, b))
    res = runner.refresh_rule_scores(s)
    assert res["rescored"] >= 1 and res["deadlines_fixed"] == 1
    with db.get_conn() as c:
        ra = c.execute("SELECT * FROM items WHERE id=?", (a,)).fetchone()
        rb = c.execute("SELECT * FROM items WHERE id=?", (b,)).fetchone()
        n = c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert ra["score"] <= 45 and json.loads(ra["tags"]) == [] and ra["status"] == "rule"  # 42 天前 → 不再排队
    assert rb["score"] == 72 and rb["deadline"] == "2026-11-12T23:59:00+08:00"  # 模型分不动，只修截止时间
    assert json.loads(rb["tags"]) == ["交换项目", "交流"]  # 末尾规则标签只留标题仍命中的“交流”，乱打的“竞赛/考试”被清掉，模型自己的标签保留
    assert n == 0
    assert runner.refresh_rule_scores(s).get("skipped") == 1  # 规则没变就不重复跑


# ---------------------------------------------------------------- 精选 / 必看口径
def _seed(s, title, *, score, scored_by="deepseek-flash", days_ago=0.2, deadline_days=None, summary="摘要"):
    i = _insert(s, title, days_ago=days_ago)
    dl = (now() + timedelta(days=deadline_days)).isoformat(timespec="seconds") if deadline_days is not None else None
    with db.get_conn() as c:
        c.execute("UPDATE items SET score=?, status=?, scored_by=?, summary=?, deadline=?, is_backfill=0, "
                  "fetched_at=? WHERE id=?",
                  (score, "scored" if scored_by != "rule" else "new", scored_by, summary if scored_by != "rule" else "",
                   dl, (now() - timedelta(hours=1)).isoformat(timespec="seconds"), i))
    return i


def test_must_and_featured_exclude_rule_only_and_stale(llm_on):
    s = llm_on
    _seed(s, "只有规则分的高分条目", score=75, scored_by="rule")
    _seed(s, "只有规则分但两天后截止", score=70, scored_by="rule", deadline_days=2)
    _seed(s, "模型打过分的新通知", score=80)
    _seed(s, "模型打过分但发布于两个月前", score=85, days_ago=60)
    _seed(s, "两个月前发布但截止还没到", score=78, days_ago=60, deadline_days=5)
    must = [x["title"] for x in brief.build_brief(s)["sections"][0]["items"]]
    featured = [x["title"] for x in queries.list_items(s, view="featured")["items"]]
    for got in (must, featured):
        assert "只有规则分的高分条目" not in got and "模型打过分但发布于两个月前" not in got
        assert {"只有规则分但两天后截止", "模型打过分的新通知", "两个月前发布但截止还没到"} <= set(got)


def test_rule_only_mode_still_has_featured(tmp_env):
    s = tmp_env  # 没有配置模型：规则分就是最终分，不做“必须经模型”的限制
    _seed(s, "纯规则模式的条目", score=70, scored_by="rule")
    assert [x["title"] for x in queries.list_items(s, view="featured")["items"]] == ["纯规则模式的条目"]


# ---------------------------------------------------------------- 编码
def test_decode_bytes_prefers_declared_charset():
    gbk_page = "<html><head><meta charset=\"gb2312\"></head><body>学校通知</body></html>".encode("gbk")
    assert "学校通知" in decode_bytes(gbk_page)
    assert decode_bytes("学校通知".encode("gbk"), "GBK") == "学校通知"
    assert decode_bytes("学校通知".encode()) == "学校通知"
    # 没有任何声明时，纯 GBK 的“学校通知”恰好也是合法 UTF-8 —— 这正是乱码 ѧУ֪ͨ 的成因
    assert "学校通知".encode("gbk").decode("utf-8") == MOJIBAKE


def test_repair_mojibake_and_brief_titles_from_key(tmp_env):
    assert repair_gbk_mojibake(MOJIBAKE) == "学校通知"
    assert repair_gbk_mojibake("学校通知") == "学校通知" and repair_gbk_mojibake("Hello") == "Hello"
    b = brief.build_brief(tmp_env)
    b["sections"].append({"key": "school", "title": MOJIBAKE, "items": []})
    with db.get_conn() as c:
        c.execute("UPDATE briefs SET content=? WHERE date=?", (json.dumps(b, ensure_ascii=False), b["date"]))
    got = brief.get_brief(b["date"])
    assert [x["title"] for x in got["sections"] if x["key"] == "school"] == ["学校通知"]
    assert "ѧУ" not in got["markdown"]


def _py_files():
    return [p for p in (ROOT / "backend").rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts]


def test_every_text_io_call_specifies_utf8():
    """open() / read_text() / write_text() 必须显式 encoding（二进制模式除外），否则中文 Windows 会按 GBK 读写。"""
    bad = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name not in ("open", "read_text", "write_text"):
                continue
            if isinstance(f, ast.Attribute) and name == "open" and not isinstance(f.value, ast.Name):
                continue  # page.open() 之类
            kws = {k.arg for k in node.keywords}
            mode = next((a.value for a in node.args[1:2] if isinstance(a, ast.Constant)), "")
            mode = next((k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode)
            if "b" in str(mode) or "encoding" in kws:
                continue
            bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not bad, bad


def test_all_tracked_text_files_are_utf8():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.split()
    bad = []
    for f in files:
        data = (ROOT / f).read_bytes()
        if b"\0" in data[:4096]:
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            bad.append(f)
    assert not bad, bad
