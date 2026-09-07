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

SCHEMA_VERSION = 5

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
    -- Draft Reply 的回覆設定預設值。
    -- default_style 是**摘要**的章節結構（general/technical/action_only），
    -- default_reply_tone 是**回話**的語氣（見 core/reply_profiles.py）——
    -- 兩者不同層次也不同值域，刻意分成兩個欄位，不共用。
    -- NULL／空字串一律代表「沒有偏好，沿用系統預設」，與 default_provider 同慣例。
    default_reply_tone       TEXT,
    -- 指向 personas.id / reply_prompts.id，但**刻意不設外鍵**：
    -- SQLite 的 ALTER TABLE ADD COLUMN 加不了 FK constraint，
    -- 若新裝的資料庫有 FK、遷移過的沒有，兩邊行為會不一致，那比沒有 FK 更難查。
    -- 改由 repository 讀取時驗證擁有權與存在性（指向已刪除的項目視為沒有偏好），
    -- 與既有的 default_code_project_id 同一個做法。
    default_persona_id       INTEGER,
    default_reply_prompt_id  INTEGER,
    -- 0/1；NULL 代表沒有偏好（目前的系統預設是關閉）
    default_sepia_enabled    INTEGER,
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
    sent_at     TEXT,
    -- 這份草稿是用什麼設定產生的（provider/model/tone/persona/sepia 與潤稿結果）。
    -- 只存**一份** content_md（潤稿後的最終版），不存未潤稿版——
    -- 兩份聊天內容落地的隱私成本不划算，而「當時用什麼設定」才是
    -- 事後真正需要回答的問題（例：這則回話的語氣是誰選的、Sepia 有沒有生效）。
    generation_config_json TEXT
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

-- 參考專案：Viewer 手動登錄的本機 git repo（ADR-0006）
-- 與 Reference Space 同一個哲學：由人指定，系統不自動發現。
-- 只存「去哪裡找」，不存程式碼內容——原始碼落地的風險見規格第十節。
CREATE TABLE IF NOT EXISTS code_projects (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id         INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    repo_path         TEXT NOT NULL,
    default_env       TEXT NOT NULL DEFAULT 'production',
    include_globs     TEXT NOT NULL DEFAULT '[]',
    exclude_globs     TEXT NOT NULL DEFAULT '[]',
    enabled           INTEGER NOT NULL DEFAULT 1,
    -- 登錄時就驗證並把結果存下來，讓設定頁能顯示「這個分支已經不在了」。
    -- 等到產草稿時才發現，代價高得多。
    last_verified_at  TEXT,
    last_verify_error TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(viewer_id, name)
);
CREATE INDEX IF NOT EXISTS idx_code_projects_viewer ON code_projects(viewer_id, enabled);

-- 分支→環境對照。這是整個功能的重點：查問題時不能查錯環境。
-- UNIQUE 是 (project_id, environment) 而不是反過來：實務上問的是
-- 「正式環境跑的是哪一版？」，不會是「main 用在哪些環境？」。
CREATE TABLE IF NOT EXISTS code_project_branches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    environment TEXT NOT NULL,
    branch      TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, environment)
);
CREATE INDEX IF NOT EXISTS idx_code_branches_project ON code_project_branches(project_id);

-- Persona：Viewer 匯入的表達／思考風格參考（ADR-0007）
--
-- 與 code_projects、Reference Space 同一個哲學：由人指定，系統不自動發現。
--
-- 兩個欄位需要特別說明：
--
--   profile_json  已經過淨化的結構化 profile（core/personas.PersonaProfile）。
--                 **這是唯一可以進 prompt 的東西。**
--   raw_source    遠端原文，只為兩件事保存：debug（使用者問「為什麼這個
--                 persona 沒效果」時要看得出淨化掉了什麼）與更新時比較差異。
--                 **永遠不得作為 generation system instruction**——那正是
--                 core/personas.py 整個模組存在的原因。
--
-- provenance（source_* 六個欄位）不是稽核裝飾，是功能的一部分：沒有
-- commit SHA 就無法保證「今天產生的草稿明天還是同樣行為」，因為遠端
-- 隨時可以改 SKILL.md 而 Viewer 不會知道。匯入時固定版本，只有按
-- 「更新 Persona」才重新取得。
CREATE TABLE IF NOT EXISTS personas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id         INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    source_type       TEXT NOT NULL,          -- github | url | manual
    source_repository TEXT,
    source_url        TEXT,
    source_ref        TEXT,
    source_commit_sha TEXT,
    source_hash       TEXT,                   -- sha256:… 實際讀到的內容雜湊
    profile_json      TEXT NOT NULL,
    raw_source        TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    imported_at       TEXT NOT NULL,          -- 第一次匯入的時間
    refreshed_at      TEXT,                   -- 最後一次按「更新」的時間
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(viewer_id, name)
);
CREATE INDEX IF NOT EXISTS idx_personas_viewer ON personas(viewer_id, enabled);

-- Reply Prompt Preset：Viewer 存起來重複使用的自訂提示詞（ADR-0007）
--
-- 刻意與「System Prompt」分開命名。這裡存的是**這一次回話要怎麼寫**的
-- 使用者要求（「不要太正式，需要對方補資料就明確列出來」），
-- 不是系統層的 prompt 模板——後者在 core/prompts.py，由程式碼管理、
-- 不給使用者改。混在一起會讓「使用者的偏好」有機會覆寫防幻覺規則。
CREATE TABLE IF NOT EXISTS reply_prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id   INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    prompt      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(viewer_id, name)
);
CREATE INDEX IF NOT EXISTS idx_reply_prompts_viewer ON reply_prompts(viewer_id);
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
    # ADR-0006：草稿頁預選用的純量預設。這兩個是 per-viewer 單值（不是實體），
    # 所以放 preferences 而不是開新表；專案本體在 code_projects。
    ("preferences", "default_code_project_id", "INTEGER"),
    ("preferences", "default_code_environment", "TEXT"),
    # ADR-0007：Draft Reply 的回覆設定預設值。照上面那條同樣的判準——
    # 都是 per-viewer 的單值純量，實體（persona、prompt preset）各自有表。
    ("preferences", "default_reply_tone", "TEXT"),
    ("preferences", "default_persona_id", "INTEGER"),
    ("preferences", "default_reply_prompt_id", "INTEGER"),
    ("preferences", "default_sepia_enabled", "INTEGER"),
    # 這份草稿是用什麼設定產生的。舊資料庫的既有草稿會是 NULL，
    # 讀取端一律要能處理「沒有這份資訊」（那些草稿產生時還沒有這個功能）。
    ("draft_replies", "generation_config_json", "TEXT"),
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
