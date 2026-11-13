"""读取层：信息流、截止看板、统计。打开页面只读库，不触发模型调用（借鉴 AIHOT 的硬规则）。"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from . import config, db
from .config import Settings, deepseek_key
from .pipeline.rules import deadline_rank_params
from .textutil import from_iso, now


def _hotness(conn, cluster_ids: list[int]) -> dict[int, dict[str, Any]]:
    """按事件算热度：48h 内每个独立信源只计一次，24h 后减半。"""
    if not cluster_ids:
        return {}
    q = ",".join("?" * len(cluster_ids))
    rows = conn.execute(
        f"SELECT cluster_id, source_id, MAX(COALESCE(published_at, fetched_at)) AS t, COUNT(*) AS n "
        f"FROM items WHERE cluster_id IN ({q}) GROUP BY cluster_id, source_id", cluster_ids).fetchall()
    out: dict[int, dict[str, Any]] = {}
    t_now = now()
    for r in rows:
        d = out.setdefault(r["cluster_id"], {"heat": 0.0, "sources": [], "size": 0})
        d["sources"].append(r["source_id"])
        d["size"] += r["n"]
        t = from_iso(r["t"])
        if t:
            age = (t_now - t).total_seconds() / 3600
            if age <= 24:
                d["heat"] += 1
            elif age <= 48:
                d["heat"] += 0.5
    return out


def rank_expr(now_iso: str, rules: dict[str, Any] | None = None) -> str:
    """排序分 = 存储分 + 截止临近加权（只用于精选的入选与排序，不改库里的分）。

    返回 SQL 表达式；参数只有一个占位符 :now（ISO 时间）。加分值来自 config/rules.yaml 的 deadline_rank，均为整数。
    """
    steps, expired = deadline_rank_params(rules if rules is not None else config.load_rules())
    whens = " ".join(f"WHEN julianday(deadline) - julianday(:now) <= {float(d)} THEN {int(b)}" for d, b in steps)
    return (
        "(score + CASE WHEN deadline IS NULL OR julianday(deadline) IS NULL THEN 0 "
        f"WHEN julianday(deadline) < julianday(:now) THEN "
        f"(CASE WHEN category IN ('school','competition') THEN -{int(expired)} ELSE 0 END) "
        f"{whens} ELSE 0 END)"
    )


def featured_threshold_expr(s: Settings) -> str:
    t = s.thresholds
    cases = " ".join(f"WHEN '{k}' THEN {int(v)}" for k, v in t.by_category.items() if k.isidentifier())
    cat = f"(CASE category {cases} ELSE {int(t.featured)} END)" if cases else str(int(t.featured))
    return f"({cat} + CASE WHEN tier = 'T2' THEN {int(t.t2_offset)} ELSE 0 END)"


RULE_ONLY_URGENT_DAYS = 7


def vetted_sql(s: Settings) -> str:
    """精选 / 必看的“已核实”条件（参数 :now）。

    启用模型时，只有规则分（还没经模型打分、或旧文未送模型）的条目不进精选/必看，
    除非规则识别出 7 天内的截止日期；没有配置模型（纯规则模式）时不限制。
    """
    if not (s.llm.enabled and deepseek_key(s)):
        return "1=1"
    return ("(COALESCE(scored_by, '') NOT IN ('rule', '') OR (julianday(deadline) >= julianday(:now) "
            f"AND julianday(deadline) - julianday(:now) <= {RULE_ONLY_URGENT_DAYS}))")


def recent_sql(param: str = ":since") -> str:
    """近期条件：发布（未知则抓取）时间在窗口内，或截止日期还没到。"""
    return f"(COALESCE(published_at, fetched_at) >= {param} OR julianday(deadline) >= julianday(:now))"


FEATURED_DEFAULT_DAYS = 14


def list_items(s: Settings, *, view: str = "all", category: str | None = None, source_id: str | None = None,
               read: str = "all", q: str | None = None, sort: str = "score", days: int | None = None,
               starred: bool = False, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    where = ["status != 'blocked'"]
    args: dict[str, Any] = {"now": now().isoformat(timespec="seconds")}
    featured = view == "featured" and not q
    if category:
        where.append("category = :category")
        args["category"] = category
    if source_id:
        where.append("source_id = :source_id")
        args["source_id"] = source_id
    if starred:
        where.append("starred = 1")
    if featured and not days:
        days = FEATURED_DEFAULT_DAYS  # 精选总要有时间窗：发布很久、又没有未到截止的旧文不进精选
    if days:
        args["since"] = (now() - timedelta(days=days)).isoformat(timespec="seconds")
        if featured:  # 精选：近 N 天发布的，或截止日期还没到的（老通知临近截止时反而更该看）
            where.append(recent_sql())
        else:
            where.append("COALESCE(published_at, fetched_at) >= :since")
    if q:
        q = q.strip()
        if len(q) >= 3:
            where.append("id IN (SELECT rowid FROM items_fts WHERE items_fts MATCH :fts)")
            args["fts"] = '"' + q.replace('"', '""') + '"'
        else:
            where.append("(title LIKE :like OR summary LIKE :like OR content LIKE :like)")
            args["like"] = f"%{q}%"
    elif featured:
        where.append(f"rank_score >= {featured_threshold_expr(s)}")
        where.append(vetted_sql(s))
    elif view == "all":
        where.append("score >= :floor")
        args["floor"] = s.thresholds.browse_floor
    order = {
        "score": "rank_score DESC, score DESC, COALESCE(published_at, fetched_at) DESC",
        "time": "COALESCE(published_at, fetched_at) DESC, id DESC",
    }.get(sort, "rank_score DESC")
    rank = rank_expr(args["now"])
    read_clause = {"unread": "AND is_read = 0", "read": "AND is_read = 1"}.get(read, "")
    sql = f"""
      WITH scored AS (SELECT items.*, {rank} AS rank_score FROM items),
      base AS (SELECT * FROM scored WHERE {' AND '.join(where)}),
      ranked AS (
        SELECT base.*, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY rank_score DESC,
               CASE tier WHEN 'T1' THEN 0 ELSE 1 END, id ASC) AS rn
        FROM base)
      SELECT * FROM ranked WHERE rn = 1 {read_clause} ORDER BY {order} LIMIT :limit OFFSET :offset"""
    count_sql = f"""
      WITH scored AS (SELECT items.*, {rank} AS rank_score FROM items),
      base AS (SELECT * FROM scored WHERE {' AND '.join(where)}),
      ranked AS (SELECT base.*, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY rank_score DESC, id ASC) AS rn FROM base)
      SELECT COUNT(*) AS n, SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) AS unread FROM ranked WHERE rn = 1 {read_clause}"""
    names = {x["id"]: x.get("name", x["id"]) for x in config.load_sources()}
    with db.get_conn() as conn:
        rows = [db.row_to_item(r) for r in conn.execute(sql, {**args, "limit": limit, "offset": offset}).fetchall()]
        cnt = conn.execute(count_sql, args).fetchone()
        heat = _hotness(conn, [r["cluster_id"] for r in rows if r.get("cluster_id")])
    for r in rows:
        h = heat.get(r.get("cluster_id"), {})
        r["heat"] = h.get("heat", 0)
        r["cluster_size"] = h.get("size", 1)
        r["cluster_sources"] = [names.get(x, x) for x in h.get("sources", [])]
        r["source_name"] = names.get(r["source_id"], r["source_id"])
        r.pop("content", None)
        r.pop("rn", None)
        r["rank_bonus"] = (r.get("rank_score") or 0) - (r.get("score") or 0)
    return {"items": rows, "total": cnt["n"] or 0, "unread": cnt["unread"] or 0}


def get_item(item_id: int) -> dict[str, Any] | None:
    names = {x["id"]: x.get("name", x["id"]) for x in config.load_sources()}
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        item = db.row_to_item(row)
        sib = conn.execute(
            "SELECT id, title, url, source_id, published_at, score FROM items WHERE cluster_id=? AND id != ? ORDER BY published_at",
            (item["cluster_id"], item_id)).fetchall()
    item["source_name"] = names.get(item["source_id"], item["source_id"])
    item["related"] = [dict(r) | {"source_name": names.get(r["source_id"], r["source_id"])} for r in sib]
    return item


def deadlines(include_past_days: int = 2, horizon_days: int = 120) -> list[dict[str, Any]]:
    names = {x["id"]: x.get("name", x["id"]) for x in config.load_sources()}
    lo = (now() - timedelta(days=include_past_days)).isoformat(timespec="seconds")
    hi = (now() + timedelta(days=horizon_days)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY score DESC) rn
               FROM items WHERE deadline IS NOT NULL AND deadline BETWEEN ? AND ? AND status != 'blocked') WHERE rn=1
               ORDER BY deadline ASC""", (lo, hi)).fetchall()
    out = []
    t_now = now()
    for r in rows:
        d = db.row_to_item(r)
        d.pop("content", None)
        d.pop("rn", None)
        dl = from_iso(d["deadline"])
        d["days_left"] = round((dl - t_now).total_seconds() / 86400, 1) if dl else None
        d["source_name"] = names.get(d["source_id"], d["source_id"])
        out.append(d)
    return out


def stats(s: Settings) -> dict[str, Any]:
    day_ago = (now() - timedelta(days=1)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        unread = conn.execute("SELECT COUNT(*) FROM items WHERE is_read=0 AND status!='blocked' AND score>=?",
                              (s.thresholds.browse_floor,)).fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM items WHERE fetched_at>=?", (day_ago,)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM items WHERE status='new'").fetchone()[0]
        by_cat = {r[0]: r[1] for r in conn.execute(
            "SELECT category, COUNT(*) FROM items WHERE is_read=0 AND status!='blocked' AND score>=? GROUP BY category",
            (s.thresholds.browse_floor,)).fetchall()}
        upcoming = conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM items WHERE deadline BETWEEN ? AND ?",
                                (now().isoformat(timespec='seconds'), (now() + timedelta(days=7)).isoformat(timespec='seconds'))).fetchone()[0]
        usage = conn.execute("SELECT * FROM llm_usage WHERE day=?", (now().strftime("%Y-%m-%d"),)).fetchone()
    return {"total": total, "unread": unread, "today": today, "pending_llm": pending, "unread_by_category": by_cat,
            "deadlines_7d": upcoming, "llm_usage_today": dict(usage) if usage else {"calls": 0}}
