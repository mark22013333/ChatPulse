"""SQLite 資料層（WAL 模式，單一檔案）。

規格對應：SPECIFICATION.md 九節。

兩個刻意的限制：
  1. **對話全文不落地。** mentions 只存識別資訊（message name、thread name、時間），
     內容於顯示時即時向 Google Chat 取回。
  2. **summaries 每一次查詢都必須帶 owner_viewer_id**（ADR-0002 的唯一執行點）。
     本模組的 repository 函式一律把 viewer_id 列為必填參數，不提供「查全部」的入口。
"""

import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

from . import config as cfg

_local = threading.local()

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 登入 ChatPulse 的人（CONTEXT.md: Viewer）
CREATE TABLE IF NOT EXISTS viewers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    google_user_id  TEXT NOT NULL UNIQUE,   -- 形如 users/1098272650197...
    email           TEXT,
    display_name    TEXT,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT
);

-- OAuth 憑證，token 內容加密（core/crypto.py）
CREATE TABLE IF NOT EXISTS credentials (
    viewer_id       INTEGER PRIMARY KEY REFERENCES viewers(id) ON DELETE CASCADE,
    encrypted_token TEXT NOT NULL,
    expiry          TEXT,
    scopes          TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    viewer_id        INTEGER PRIMARY KEY REFERENCES viewers(id) ON DELETE CASCADE,
    pinned_space_ids TEXT NOT NULL DEFAULT '[]',
    default_limit    INTEGER NOT NULL DEFAULT 50,
    default_style    TEXT NOT NULL DEFAULT 'general',
    -- 空字串／NULL 代表沿用伺服器的 CHATPULSE_AI_PROVIDER
    default_provider TEXT,
    updated_at       TEXT NOT NULL
);

-- Summary 私有於產生者（ADR-0002）
CREATE TABLE IF NOT EXISTS summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_viewer_id  INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    space_id         TEXT NOT NULL,
    space_name       TEXT,
    style            TEXT NOT NULL DEFAULT 'general',
    message_count    INTEGER NOT NULL DEFAULT 0,
    content_md       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_owner ON summaries(owner_viewer_id, created_at DESC);

-- 只存識別資訊，不存訊息內容
CREATE TABLE IF NOT EXISTS mentions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id     INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    space_id      TEXT NOT NULL,
    space_name    TEXT,
    message_name  TEXT NOT NULL,
    thread_name   TEXT,
    sender_name   TEXT,
    sender_display TEXT,
    create_time   TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',   -- pending | resolved
    resolved_at   TEXT,
    detected_at   TEXT NOT NULL,
    UNIQUE(viewer_id, message_name)
);
CREATE INDEX IF NOT EXISTS idx_mentions_viewer_state
    ON mentions(viewer_id, state, create_time DESC);

CREATE TABLE IF NOT EXISTS draft_replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id  INTEGER NOT NULL REFERENCES mentions(id) ON DELETE CASCADE,
    content_md  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_mention ON draft_replies(mention_id, created_at DESC);

-- 採集器的輪詢水位（每位 Viewer 一列）
CREATE TABLE IF NOT EXISTS collector_state (
    viewer_id      INTEGER PRIMARY KEY REFERENCES viewers(id) ON DELETE CASCADE,
    last_polled_at TEXT,
    last_error     TEXT,
    last_run_stats TEXT
);

-- R-2：每日 token 用量記錄，累積後評估成本
CREATE TABLE IF NOT EXISTS token_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL,
    viewer_id     INTEGER,
    model         TEXT NOT NULL,
    operation     TEXT NOT NULL,          -- summarize | draft_reply
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON token_usage(day, model);

-- 使用者名錄：user_id -> 顯示名稱（見 core/directory.py 的緣由說明）
CREATE TABLE IF NOT EXISTS user_directory (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'mention_annotation',
    updated_at   TEXT NOT NULL
);

-- 儀表板 session（Cookie 對應）
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    viewer_id  INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_viewer ON sessions(viewer_id);
"""


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")


def get_connection() -> sqlite3.Connection:
    """取得 thread-local 連線。SQLite 連線不可跨執行緒共用，採集器在別的執行緒跑。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        cfg.ensure_data_dir()
        conn = sqlite3.connect(cfg.DB_PATH, timeout=10.0)
        _configure(conn)
        _local.conn = conn
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


#: 對**既有**資料表補欄位。`CREATE TABLE IF NOT EXISTS` 只在資料表不存在時
#: 生效，對已經建好的資料表完全不做事——所以新增欄位一定要走這裡，
#: 否則舊資料庫升級後會在查詢時才炸「no such column」。
_ADD_COLUMNS = [
    ("preferences", "default_provider", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> List[str]:
    applied = []
    for table, column, decl in _ADD_COLUMNS:
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue  # 資料表還不存在，_SCHEMA 會建（已含該欄位）
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> None:
    """建立 schema（idempotent），補既有資料表的新欄位，並記下 schema 版本。"""
    cfg.ensure_data_dir()
    conn = get_connection()
    with conn:
        conn.executescript(_SCHEMA)
        applied = _migrate(conn)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    if applied:
        import logging

        logging.getLogger("chatpulse.db").info("schema 遷移：新增欄位 %s", applied)
    # 資料庫檔本身也收權限：裡面有摘要與草稿內容
    try:
        os.chmod(cfg.DB_PATH, 0o600)
    except OSError:
        pass


def journal_mode() -> str:
    """回傳目前的 journal 模式，供驗收斷言 WAL 生效。"""
    return get_connection().execute("PRAGMA journal_mode").fetchone()[0]


def query_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in get_connection().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    row = get_connection().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = get_connection()
    with conn:
        return conn.execute(sql, tuple(params))
