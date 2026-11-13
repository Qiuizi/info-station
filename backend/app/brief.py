"""每日简报：纯规则编排，不调用模型（一事一条，按分数与截止日期排序）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from . import config, db
from .config import CATEGORIES, Settings
from .textutil import TZ, from_iso, now, now_iso, repair_gbk_mojibake


def _pick(conn, since: str, until: str, extra: str = "", args: tuple = (), limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY score DESC) rn FROM items
            WHERE status != 'blocked' AND fetched_at >= ? AND fetched_at < ?
              AND COALESCE(published_at, fetched_at) >= ? {extra}) WHERE rn = 1
            ORDER BY score DESC LIMIT ?""",
        (since, until, (now() - timedelta(days=MUST_RECENT_DAYS)).isoformat(timespec="seconds"), *args, limit)).fetchall()
    return [db.row_to_item(r) for r in rows]


MUST_RECENT_DAYS = 3


def _pick_must(conn, s: Settings, since: str, until: str, fresh_only: bool, limit: int = 6) -> list[dict[str, Any]]:
    """今日必看：与“今日精选”同一套口径（分类门槛 + 截止临近加权 + 必须经模型打分或规则识别出近期截止），
    且只取近 3 天发布的、或截止还没到的（首次导入的几个月前的旧文不会进来）。"""
    from .queries import featured_threshold_expr, rank_expr, recent_sql, vetted_sql

    nw = now().isoformat(timespec="seconds")
    recent = (now() - timedelta(days=MUST_RECENT_DAYS)).isoformat(timespec="seconds")
    rank = rank_expr(nw)
    rows = conn.execute(
        f"""WITH scored AS (SELECT items.*, {rank} AS rank_score FROM items)
            SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY rank_score DESC) rn FROM scored
              WHERE status != 'blocked' AND rank_score >= {featured_threshold_expr(s)} AND {vetted_sql(s)}
                AND {recent_sql(':recent')}
                AND ((fetched_at >= :since AND fetched_at < :until) OR julianday(deadline) >= julianday(:now))
                {"AND is_backfill = 0" if fresh_only else ""}) WHERE rn = 1
            ORDER BY rank_score DESC LIMIT :limit""",
        {"since": since, "until": until, "now": nw, "recent": recent, "limit": limit}).fetchall()
    return [db.row_to_item(r) for r in rows]


def _slim(it: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {k: it.get(k) for k in ("id", "title", "url", "score", "summary", "deadline", "category", "tags", "published_at", "raw_summary")} | {
        "source_name": names.get(it["source_id"], it["source_id"])}


def build_brief(s: Settings, day: datetime | None = None) -> dict[str, Any]:
    day = (day or now()).astimezone(TZ)
    until_dt = min(day.replace(hour=23, minute=59, second=59), now())
    since_dt = until_dt - timedelta(hours=36)
    since, until = since_dt.isoformat(timespec="seconds"), until_dt.isoformat(timespec="seconds")
    names = {x["id"]: x.get("name", x["id"]) for x in config.load_sources()}
    with db.get_conn() as conn:
        must = _pick_must(conn, s, since, until, True)
        if len(must) < 3:  # 新装第一天没有“非存量”数据时，退而求其次
            must = _pick_must(conn, s, since, until, False)
        dl_rows = conn.execute(
            """SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY score DESC) rn FROM items
               WHERE status!='blocked' AND deadline BETWEEN ? AND ?) WHERE rn=1 ORDER BY deadline LIMIT 8""",
            (now().isoformat(timespec="seconds"), (now() + timedelta(days=10)).isoformat(timespec="seconds"))).fetchall()
        by_cat = {c: _pick(conn, since, until, "AND category = ? AND score >= ?", (c, s.thresholds.browse_floor), 4)
                  for c in CATEGORIES}
        n_new = conn.execute("SELECT COUNT(*) FROM items WHERE fetched_at BETWEEN ? AND ?", (since, until)).fetchone()[0]
    must_ids = {m["id"] for m in must}
    sections = [
        {"key": "must", "title": SECTION_TITLES["must"], "items": [_slim(x, names) for x in must]},
        {"key": "deadline", "title": SECTION_TITLES["deadline"], "items": [_slim(db.row_to_item(r), names) for r in dl_rows]},
    ]
    for c, label in CATEGORIES.items():
        rest = [x for x in by_cat[c] if x["id"] not in must_ids]
        if rest:
            sections.append({"key": c, "title": label, "items": [_slim(x, names) for x in rest]})
    brief = {
        "date": day.strftime("%Y-%m-%d"),
        "generated_at": now_iso(),
        "window": [since, until],
        "stats": {"new_items": n_new, "must": len(must), "deadlines": len(dl_rows)},
        "greeting": _greeting(s, len(must), len(dl_rows)),
        "sections": sections,
    }
    brief["markdown"] = to_markdown(brief)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO briefs(date, content, created_at) VALUES(?,?,?) ON CONFLICT(date) DO UPDATE SET "
                     "content=excluded.content, created_at=excluded.created_at",
                     (brief["date"], json.dumps(brief, ensure_ascii=False), now_iso()))
    return brief


def _greeting(s: Settings, must: int, dls: int) -> str:
    h = now().hour
    hi = "早上好" if h < 11 else ("下午好" if 13 <= h < 18 else ("晚上好" if h >= 18 else "中午好"))
    parts = [f"{hi}，{s.profile.name}。"]
    parts.append(f"今天有 {must} 条值得看的信息" if must else "今天没有特别重要的新信息")
    if dls:
        parts.append(f"，{dls} 个截止日期在十天内")
    return "".join(parts) + "。"


def to_markdown(b: dict[str, Any]) -> str:
    lines = [f"# 每日简报 · {b['date']}", "", b["greeting"], ""]
    for sec in b["sections"]:
        if not sec["items"]:
            continue
        lines.append(f"## {sec['title']}")
        for it in sec["items"]:
            dl = from_iso(it.get("deadline"))
            dl_s = f"（截止 {dl:%m-%d %H:%M}）" if dl else ""
            lines.append(f"- [{it['title']}]({it['url']}) · {it['source_name']} · {it['score']}分{dl_s}")
            if it.get("summary"):
                lines.append(f"  - {it['summary']}")
        lines.append("")
    return "\n".join(lines)


SECTION_TITLES = {"must": "今日必看", "deadline": "十天内截止", **CATEGORIES}


def _normalize_titles(b: dict[str, Any]) -> dict[str, Any]:
    """分区标题一律由 key 决定（不信任存档里的文字），顺带修复以前存进去的编码乱码。"""
    changed = False
    for sec in b.get("sections") or []:
        want = SECTION_TITLES.get(sec.get("key"), repair_gbk_mojibake(sec.get("title") or ""))
        if sec.get("title") != want:
            sec["title"] = want
            changed = True
    if changed:
        b["markdown"] = to_markdown(b)
    return b


def get_brief(date: str) -> dict[str, Any] | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT content FROM briefs WHERE date=?", (date,)).fetchone()
    return _normalize_titles(json.loads(row["content"])) if row else None


def list_briefs(limit: int = 30) -> list[str]:
    with db.get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT date FROM briefs ORDER BY date DESC LIMIT ?", (limit,)).fetchall()]
