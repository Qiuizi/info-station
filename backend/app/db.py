"""SQLite 存储（WAL + FTS5 trigram 全文检索，中文友好）。"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    url_key TEXT NOT NULL UNIQUE,
    title_key TEXT,
    title TEXT NOT NULL,
    raw_summary TEXT DEFAULT '',
    content TEXT DEFAULT '',
    author TEXT DEFAULT '',
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    category TEXT,
    tier TEXT,
    status TEXT DEFAULT 'new',          -- new / rule / scored / blocked / error
    rule_score INTEGER DEFAULT 0,
    llm_scores TEXT,                    -- JSON 数组：每次独立打分
    score INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    deadline TEXT,                      -- ISO 日期时间
    deadline_note TEXT DEFAULT '',
    action TEXT DEFAULT '',
    relevant INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    should_notify INTEGER DEFAULT 0,
    notified INTEGER DEFAULT 0,
    is_backfill INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0,
    feedback INTEGER DEFAULT 0,
    cluster_id INTEGER,
    prompt_version TEXT,
    scored_by TEXT DEFAULT '',
    audience TEXT DEFAULT '',           -- 模型判断的适用对象
    eligible INTEGER,                   -- 读者本人是否在适用对象内（NULL=未知）
    dims TEXT,                          -- JSON：五维 0–10
    llm_raw TEXT,                       -- JSON 数组：模型原始总分（合成前）
    extra TEXT                          -- JSON：信源特有字段（推文的作者、头像、点赞 / 转发数等）
);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_score ON items(score);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, content, summary, content='items', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, content, summary) VALUES (new.id, new.title, new.content, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, content, summary) VALUES('delete', old.id, old.title, old.content, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF title, content, summary ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, content, summary) VALUES('delete', old.id, old.title, old.content, old.summary);
  INSERT INTO items_fts(rowid, title, content, summary) VALUES (new.id, new.title, new.content, new.summary);
END;

CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    last_run TEXT,
    last_success TEXT,
    last_error TEXT,
    last_count INTEGER DEFAULT 0,
    last_new INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    runs INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    running INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS briefs (
    date TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    title TEXT,
    body TEXT,
    created_at TEXT,
    delivered INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT PRIMARY KEY,
    calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0
);
"""

_init_lock = threading.Lock()
_initialized: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


# 旧库升级：只加列，不动已有数据
_ADDED_COLUMNS = {
    "items": [("audience", "TEXT DEFAULT ''"), ("eligible", "INTEGER"), ("dims", "TEXT"), ("llm_raw", "TEXT"),
              ("extra", "TEXT")],
}


def get_state(key: str, default: Any = None) -> Any:
    with get_conn() as c:
        row = c.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_state(key: str, value: Any) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db(path: Path | None = None) -> None:
    path = path or config.DB_PATH
    with _init_lock:
        conn = _connect(path)
        try:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized.add(str(path))


@contextmanager
def get_conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = path or config.DB_PATH
    if str(path) not in _initialized:
        init_db(path)
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


JSON_FIELDS = ("tags", "llm_scores", "dims", "llm_raw", "extra")


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d:
            try:
                d[f] = json.loads(d[f]) if d[f] else ([] if f == "tags" else {} if f == "extra" else None)
            except Exception:
                d[f] = {} if f == "extra" else []
    for b in ("relevant", "should_notify", "notified", "is_backfill", "is_read", "starred"):
        if b in d:
            d[b] = bool(d[b])
    return d
