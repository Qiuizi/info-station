"""fix1：学校通知打分校准——适用对象、五维合成、截止临近排序、分类门槛。"""
import json
from datetime import datetime, timedelta

from app import brief, db, queries
from app.config import Settings, load_rules
from app.fetchers.base import RawItem
from app.pipeline.llm import LLMVerdict, build_messages, parse_verdict
from app.pipeline.rules import apply_rules, audience_adjust, blend_llm_score, grade_text, study_year
from app.pipeline.runner import insert_items, save_verdicts
from app.textutil import TZ, now

RULES = load_rules()


# ---------------------------------------------------------------- 年级与适用对象
def test_study_year_rolls_over_in_september():
    s = Settings()  # 默认 2025级
    assert study_year(s, datetime(2025, 10, 1, tzinfo=TZ)) == (1, "大一")
    assert study_year(s, datetime(2026, 8, 31, tzinfo=TZ)) == (1, "大一")
    assert study_year(s, datetime(2026, 11, 11, tzinfo=TZ)) == (2, "大二")
    s.profile.grade = "2025级（大一）"  # 旧设置里的括号写法不影响推算
    assert "当前大二" in grade_text(s, datetime(2026, 11, 11, tzinfo=TZ))
    assert "（大一）" not in grade_text(s, datetime(2026, 11, 11, tzinfo=TZ))


def test_audience_rules_are_grade_aware():
    s = Settings()
    same, _ = audience_adjust("关于2025级本科生专业分流的通知", s, RULES)
    other, hits = audience_adjust("关于2023级本科生毕业论文师生双选的通知", s, RULES)
    grad, _ = audience_adjust("研究生学业奖学金评定通知", s, RULES)
    both, _ = audience_adjust("面向本科生和研究生的讲座", s, RULES)
    assert same > 0 > other and any("其他年级" in h for h in hits)
    assert grad < 0 and both > 0  # 同时面向本科的不算排除
    # 换一个年级，同一条结论翻转
    s.profile.grade = "2023级"
    assert audience_adjust("关于2023级本科生毕业论文师生双选的通知", s, RULES)[0] > 0


def test_rule_scores_rank_own_cohort_above_other_cohorts():
    s = Settings()
    mine = apply_rules("2025级本科生大创项目申报通知", "", "competition", "T1", s, RULES)
    theirs = apply_rules("2023级本科生毕业论文答辩通知", "", "school", "T1", s, RULES)
    leader = apply_rules("学院领导接待日安排", "", "school", "T1", s, RULES)
    assert mine.score >= 75 and theirs.score < 40 and leader.score < 25


# ---------------------------------------------------------------- 模型输出与合成
def test_parse_verdict_reads_dims_and_eligibility():
    v = parse_verdict(json.dumps({
        "audience": "全体本科生", "eligible": "true", "score": 70,
        "dims": {"relevance": 9, "action": 8, "weight": 7, "novelty": 6, "credibility": 12},
        "summary": "x", "tags": [], "deadline": "2026-11-24", "relevant": True}))
    assert v.eligible is True and v.audience == "全体本科生"
    assert v.dims["credibility"] == 10  # 越界截断
    assert v.deadline.endswith("23:59:00+08:00") or "23:59" in v.deadline
    old = parse_verdict('{"score": 50, "summary": "旧格式"}')  # 旧提示词的输出仍可解析
    assert old.dims is None and old.eligible is None


def test_blend_uses_category_weights_and_caps_ineligible():
    dims = {"relevance": 9, "action": 9, "weight": 7, "novelty": 6, "credibility": 9}
    # 模型总分偏保守（55），五维显示相关且要行动 → 合成后上调
    assert blend_llm_score(55, dims, True, "school", RULES) > 65
    # 同样的五维，在技术类权重下（看重份量/增量）合成分更低
    assert blend_llm_score(55, dims, True, "tech", RULES) < blend_llm_score(55, dims, True, "school", RULES)
    # 读者不在适用对象内：学校/竞赛封顶；技术类不受影响
    cap = RULES["llm_blend"]["eligibility_cap"]
    assert blend_llm_score(90, dims, False, "school", RULES) == cap
    assert blend_llm_score(90, dims, False, "tech", RULES) > cap
    assert blend_llm_score(66, None, None, "school", RULES) == 66  # 没有维度时就是模型分


def test_prompt_has_audience_rubric_and_current_grade():
    msgs, _ = build_messages({"title": "t", "url": "u", "category": "school", "tier": "T1"}, Settings())
    sys_prompt = msgs[0]["content"]
    assert "eligible" in sys_prompt and "dims" in sys_prompt and "适用对象" in sys_prompt
    assert "当前大" in sys_prompt  # 年级按日期推算
    assert "权重：相关 35%" in sys_prompt  # 学校类权重


def _verdict(score, eligible=True, dims=None, deadline=None, should_notify=False):
    return LLMVerdict(score=score, summary="s", tags=[], deadline=deadline, deadline_note="", relevant=bool(eligible),
                      should_notify=should_notify, reason="r", action="", dims=dims, audience="本科生", eligible=eligible)


def test_save_verdicts_stores_blend_and_skips_notify_for_ineligible(tmp_env):
    s: Settings = tmp_env
    s.thresholds.notify = 50
    ids = insert_items({"id": "a", "category": "school", "tier": "T1"},
                       [RawItem("2023级毕业论文开题", "https://a.edu/x", now() - timedelta(hours=1))], s, first_run=False)
    with db.get_conn() as c:
        item = db.row_to_item(c.execute("SELECT * FROM items WHERE id=?", ids).fetchone())
    save_verdicts(item, [_verdict(88, eligible=False, should_notify=True)], "v", s)
    with db.get_conn() as c:
        row = db.row_to_item(c.execute("SELECT * FROM items WHERE id=?", ids).fetchone())
        n = c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert row["score"] == RULES["llm_blend"]["eligibility_cap"] and row["llm_raw"] == [88]
    assert row["eligible"] == 0 and row["audience"] == "本科生"
    assert n == 0


# ---------------------------------------------------------------- 精选：分类门槛 + 截止临近
def _seed(s, rows):
    """rows: (title, category, score, published_days_ago, deadline_in_days|None)"""
    out = {}
    for i, (title, cat, score, ago, dl) in enumerate(rows):
        ids = insert_items({"id": f"s{i}", "category": cat, "tier": "T1"},
                           [RawItem(title, f"https://x/{i}", now() - timedelta(days=ago))], s, first_run=True)
        deadline = (now() + timedelta(days=dl)).isoformat(timespec="seconds") if dl is not None else None
        with db.get_conn() as c:
            c.execute("UPDATE items SET score=?, status='scored', deadline=?, fetched_at=published_at WHERE id=?",
                      (score, deadline, ids[0]))
        out[title] = ids[0]
    return out


def test_featured_uses_category_thresholds_and_deadline_urgency(tmp_env):
    s: Settings = tmp_env
    s.thresholds.by_category = {"school": 55, "competition": 55, "tech": 62, "news": 65}
    _seed(s, [
        ("选课通知", "school", 57, 1, None),           # 57 ≥ 学校门槛 55
        ("技术小更新", "tech", 60, 1, None),            # 60 < 技术门槛 62
        ("竞赛报名两天后截止", "competition", 60, 3, 2),  # 60 + 12 = 72，排在最前
        ("奖学金上周已截止", "school", 62, 5, -3),        # 62 - 15 = 47，掉出精选
        ("交换项目下月截止", "school", 70, 30, 20),       # 发布超过 14 天，但截止还没到 → 仍在精选
        ("大模型发布", "tech", 66, 1, None),
    ])
    res = queries.list_items(s, view="featured", days=14)
    titles = [x["title"] for x in res["items"]]
    assert titles[0] == "竞赛报名两天后截止" and res["items"][0]["rank_bonus"] == 12
    assert set(titles) == {"竞赛报名两天后截止", "选课通知", "交换项目下月截止", "大模型发布"}
    # 全部视图不受排序加权影响，仍按存储分过滤
    all_titles = [x["title"] for x in queries.list_items(s, view="all", days=14)["items"]]
    assert "奖学金上周已截止" in all_titles and "交换项目下月截止" not in all_titles


def test_brief_must_matches_featured_logic(tmp_env):
    s: Settings = tmp_env
    _seed(s, [("竞赛报名明天截止", "competition", 48, 0.1, 1), ("技术小更新", "tech", 60, 0.1, None),
              ("选课通知", "school", 58, 0.1, None), ("考试安排", "school", 80, 0.1, None)])
    b = brief.build_brief(s)
    assert b["stats"]["must"] == 3
    must = [x["title"] for x in b["sections"][0]["items"]]
    assert must[0] == "考试安排" and "竞赛报名明天截止" in must and "技术小更新" not in must


def test_settings_by_category_roundtrip(tmp_env):
    s = Settings.model_validate({"thresholds": {"featured": 60, "t2_offset": 10}})  # 旧设置文件没有该字段
    assert s.thresholds.featured_for("school") == 55 and s.thresholds.featured_for("feishu") == 60
