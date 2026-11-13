"""规则预筛：关键词加减分、屏蔽词、截止日期临近加分。没有 API Key 时它就是最终分。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import Settings
from ..textutil import extract_deadline, now

DEFAULT_RULES: dict[str, Any] = {
    "category_base": {"school": 42, "competition": 50, "tech": 35, "news": 28, "feishu": 45, "email": 40},
    "tier_bonus": {"T1": 8, "T2": 0},
    "boost": {},
    "penalty": {},
    "block": [],
    "tags": {},
    "audience": {},
    "llm_blend": {"dims_weight": 0.5, "eligibility_cap": 40},
    "dims_weights": {},
    "deadline_rank": {"steps": [[3, 12], [7, 8], [14, 4]], "expired_penalty": 15},
    "rule_cap": 75,
    "content_boost_cap": 12,
    "expired_penalty": 15,
    "age_penalty": [[14, -5], [30, -12], [90, -25]],
    "rule_notify_urgent": 65,
    # 推文互动（点赞 + 3×转发 + 2×引用）达到阈值加分，取最高一档
    "engagement_bonus": [[100, 3], [500, 6], [2000, 10], [10000, 14]],
    # 邮件：按发件人域名加分（含子域名，如 stu.scu.edu.cn 算 scu.edu.cn）
    "sender_boost": {"scu.edu.cn": 15},
    # 只对某个分类生效的加分词（和 boost 同样的计分方式）
    "category_boost": {"email": {"通知": 6, "截止": 8, "报名": 8, "面试": 12, "offer": 12, "录用": 10, "录取": 10}},
}


def sender_domain(author: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", author or "")
    return m.group(1).lower().rstrip(".") if m else ""


def engagement_heat(extra: dict[str, Any] | None) -> int:
    if not extra:
        return 0
    try:
        return int(extra.get("likes") or 0) + 3 * int(extra.get("retweets") or 0) + 2 * int(extra.get("quotes") or 0)
    except (TypeError, ValueError):
        return 0

DIMS = ("relevance", "action", "weight", "novelty", "credibility")
_DEFAULT_DIMS_W = {"relevance": 0.3, "action": 0.25, "weight": 0.2, "novelty": 0.15, "credibility": 0.1}
_COHORT_RE = re.compile(r"(20\d{2})\s*级")


@dataclass
class RuleResult:
    score: int
    blocked: bool
    hits: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    deadline: datetime | None = None
    deadline_note: str = ""


def _interest_terms(settings: Settings) -> list[str]:
    terms: list[str] = []
    for it in settings.profile.interests:
        for t in re.split(r"[\s/、,，]+", it):
            t = t.strip()
            if len(t) >= 2:
                terms.append(t)
    return terms


def profile_cohort(settings: Settings) -> str | None:
    """从画像年级里取出入学年份，例如 “2025级（大一）” → "2025"。"""
    m = _COHORT_RE.search(settings.profile.grade or "")
    return m.group(1) if m else None


_YEAR_NAMES = {1: "大一", 2: "大二", 3: "大三", 4: "大四", 5: "大五"}


def study_year(settings: Settings, today: datetime | None = None) -> tuple[int, str] | None:
    """按入学年份和今天推算当前年级：9 月起算新学年。返回 (n, "大二") ，推算不了返回 None。"""
    cohort = profile_cohort(settings)
    if not cohort:
        return None
    t = today or now()
    n = t.year - int(cohort) + (1 if t.month >= 9 else 0)
    if n < 1:
        return None
    return n, _YEAR_NAMES.get(n, f"第{n}年")


def grade_text(settings: Settings, today: datetime | None = None) -> str:
    """画像里的年级描述，例如 “2025级，当前大二（2026-2027学年）”。"""
    g = re.sub(r"[（(].*?[)）]", "", settings.profile.grade or "").strip()  # 去掉旧写法里的“（大一）”
    sy = study_year(settings, today)
    if not sy:
        return settings.profile.grade
    t = today or now()
    start = t.year if t.month >= 9 else t.year - 1
    return f"{g}，当前{sy[1]}（{start}-{start + 1}学年）"


def audience_adjust(title: str, settings: Settings, rules: dict[str, Any]) -> tuple[int, list[str]]:
    """只看标题判断适用对象：同年级/本科生加分，其他年级/研究生/教职工扣分。"""
    aud = rules.get("audience") or {}
    hits: list[str] = []
    delta = 0
    me = profile_cohort(settings)
    cohorts = set(_COHORT_RE.findall(title))
    if me and cohorts:
        if me in cohorts:
            delta += 12
            hits.append(f"+12 本年级:{me}级")
        else:
            delta -= 12
            hits.append(f"-12 其他年级:{'/'.join(sorted(cohorts))}级")
    sy = study_year(settings)
    if sy and sy[1] in title and not cohorts:
        delta += 8
        hits.append(f"+8 本年级:{sy[1]}")
    excl = [w for w in aud.get("exclude") or [] if w and w in title]
    # “本科生与研究生”这类同时面向本科的，不算排除
    incl = [w for w in aud.get("include") or [] if w and w in title]
    if excl and not incl and not (me and me in cohorts):
        p = int(aud.get("exclude_penalty", -14))
        delta += p
        hits.append(f"{p} 适用对象:{excl[0]}")
    elif incl and not (me and cohorts and me not in cohorts):  # “2023级本科生…”不算面向自己
        b = int(aud.get("include_bonus", 6))
        delta += b
        hits.append(f"+{b} 适用对象:{incl[0]}")
    return delta, hits


def blend_llm_score(raw: int, dims: dict[str, float] | None, eligible: bool | None,
                    category: str, rules: dict[str, Any] | None = None) -> int:
    """模型总分与五维加权分合成；学校/竞赛条目若读者不在适用对象内则封顶。"""
    r = {**DEFAULT_RULES, **(rules or {})}
    blend = {**DEFAULT_RULES["llm_blend"], **(r.get("llm_blend") or {})}
    score = float(raw)
    if dims:
        w = (r.get("dims_weights") or {}).get(category) or _DEFAULT_DIMS_W
        total_w = sum(float(w.get(k, 0)) for k in DIMS) or 1.0
        weighted = sum(float(w.get(k, 0)) * max(0.0, min(10.0, float(dims.get(k, 0) or 0))) for k in DIMS)
        dim_score = weighted / total_w * 10
        x = float(blend.get("dims_weight", 0.5))
        score = (1 - x) * score + x * dim_score
    if eligible is False and category in ("school", "competition"):
        score = min(score, float(blend.get("eligibility_cap", 40)))
    return int(round(max(0, min(100, score))))


def deadline_rank_params(rules: dict[str, Any] | None = None) -> tuple[list[tuple[float, int]], int]:
    r = {**DEFAULT_RULES, **(rules or {})}
    dr = {**DEFAULT_RULES["deadline_rank"], **(r.get("deadline_rank") or {})}
    steps = sorted((float(d), int(b)) for d, b in dr.get("steps") or [])
    return steps, int(dr.get("expired_penalty", 0))


_ASCII_WORD = re.compile(r"^[A-Za-z0-9+#.\- ]+$")


def kw_in(word: str, text: str) -> bool:
    """关键词匹配：英文词按字母边界匹配（“AI” 不再命中 “DETAIL”“MAIL”，但仍命中 “AI4Science”），中文按子串。"""
    if not word:
        return False
    if _ASCII_WORD.match(word):
        return re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.I) is not None
    return word in text


def _num(r: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(r.get(key, default))
    except (TypeError, ValueError):
        return default


def apply_rules(title: str, text: str, category: str, tier: str, settings: Settings,
                rules: dict[str, Any] | None = None, ref: datetime | None = None,
                source_adjust: int = 0, engagement: dict[str, Any] | None = None, author: str = "") -> RuleResult:
    """规则分：只作为模型打分前的临时分与兜底，不追求精细。

    - 标题命中的关键词按全额计分；只在正文出现的按一半计，且正文加分总额封顶（避免长通知堆出 100 分）
    - 英文关键词按词边界匹配；标签只看标题
    - 发布越久越降权（除非还有未到的截止日期），已过截止的扣分
    - 总分封顶 rule_cap（默认 75）：规则分不会高过模型给出的“必看”分
    """
    r = {**DEFAULT_RULES, **(rules or {})}
    base = r["category_base"].get(category, DEFAULT_RULES["category_base"].get(category, 30))
    score = float(base) + float(r["tier_bonus"].get(tier, 0)) + source_adjust
    hits: list[str] = [f"{source_adjust:+d} 信源"] if source_adjust else []
    dom = sender_domain(author) if category == "email" else ""
    if dom:
        for d, w in (r.get("sender_boost") or {}).items():
            d = str(d).lower().lstrip("@.")
            if dom == d or dom.endswith("." + d):
                score += float(w)
                hits.append(f"+{float(w):g} 发件人:{d}")
                break
    body = text or ""

    for word in r.get("block") or []:
        if word and kw_in(word, title):
            return RuleResult(score=0, blocked=True, hits=[f"屏蔽:{word}"])

    content_gain = 0.0
    content_cap = _num(r, "content_boost_cap", 12)
    boosts = {**(r.get("boost") or {}), **((r.get("category_boost") or {}).get(category) or {})}
    for word, w in boosts.items():
        w = float(w)
        if kw_in(word, title):
            score += w
            hits.append(f"+{w:g} {word}")
        elif kw_in(word, body) and content_gain < content_cap:
            add = min(w / 2, content_cap - content_gain)
            content_gain += add
            score += add
            hits.append(f"+{add:g} 正文:{word}")
    for word, w in (r.get("penalty") or {}).items():
        if kw_in(word, title):
            score += float(w)
            hits.append(f"{w} {word}")
    d, h = audience_adjust(title, settings, r)
    score += d
    hits += h
    for word in settings.profile.dislikes:
        if word and kw_in(word, title):
            score -= 15
            hits.append(f"-15 不感兴趣:{word}")
    interest = 0.0
    boosted = {w.lower() for w in boosts}
    for t in _interest_terms(settings):
        if interest >= 12:
            break
        if t.lower() in boosted:
            continue  # 已经作为加分词算过，不重复加
        if kw_in(t, title):
            interest += 6
            hits.append(f"+6 兴趣:{t}")
        elif kw_in(t, body):
            interest += 3
            hits.append(f"+3 正文兴趣:{t}")
    score += min(interest, 12)

    deadline, note = extract_deadline(f"{title}\n{body}", ref)
    t_now = now()
    future_deadline = bool(deadline and deadline >= t_now)
    if deadline:
        days = (deadline - t_now).total_seconds() / 86400
        if 0 <= days <= 3:
            score += 15
            hits.append("+15 三天内截止")
        elif 0 <= days <= 14:
            score += 8
            hits.append("+8 两周内截止")
        elif days < 0:
            p = _num(r, "expired_penalty", 15)
            score -= p
            hits.append(f"-{p:g} 已过截止")
    if ref and not future_deadline:
        age = (t_now - ref).total_seconds() / 86400
        for days_, pen in sorted(((float(a), float(b)) for a, b in (r.get("age_penalty") or []) ), reverse=True):
            if age > days_:
                score += pen
                hits.append(f"{pen:g} 发布超过{days_:g}天")
                break

    heat = engagement_heat(engagement)
    if heat:
        for need, bonus in sorted(((float(a), float(b)) for a, b in (r.get("engagement_bonus") or [])), reverse=True):
            if heat >= need:
                score += bonus
                hits.append(f"+{bonus:g} 互动{heat}")
                break

    tags = [tag for tag, kws in (r.get("tags") or {}).items() if any(kw_in(k, title) for k in kws)]
    cap = _num(r, "rule_cap", 75)
    return RuleResult(score=int(round(max(0, min(cap, score)))), blocked=False, hits=hits, tags=tags[:5],
                      deadline=deadline, deadline_note=note)
