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

SCHEMA_VERSION = 8

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
    -- 每個 AI 供應商各自的預設 model：JSON 物件 {"claude_cli": "opus", "gemini": "…"}。
    -- NULL／空字串＝全部沿用 config 預設，與上面幾個欄位同慣例。
    --
    -- **為什麼是 JSON 而不是單一 default_model TEXT**：model 的意義依附在
    -- provider 上，`opus` 對 Gemini 毫無意義。存單值的話，Viewer 一換 provider
    -- 偏好就變成非法值，而且錯誤要到送出摘要時才浮現。一個 provider 一格，
    -- 換來換去都記得住，日後新增供應商也不必再動 schema。
    --
    -- 存的一律是**裸值**（`opus`），不是用量表那種帶前綴的識別字
    -- （`claude-cli:opus`）——混進來會組出 `--model claude-cli:opus`。
    default_models_json      TEXT,
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

-- 我的工作 repo：Draft Worklog 的證據來源（ADR-0008）
--
-- 記的是「要從哪些本機 git repo、用哪些作者信箱去撈 commit」，用來回想
-- 「這天我到底做了什麼」。與 code_projects、Reference Space、Persona 同一個
-- 哲學：由人指定，系統不自動發現。
--
-- **刻意不沿用 code_projects**，雖然兩者都是「Viewer 登錄一個本機 git repo」。
-- 那張表是為了「查程式碼回答問題」而登的（ADR-0006），要釘死是哪個環境的
-- 哪一版；這張表是為了「回想那天做過什麼」。兩者的分支語意正好相反（見下方
-- branch_filter），合成一張表會讓其中一邊的預設值永遠是錯的。
CREATE TABLE IF NOT EXISTS work_repos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id         INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    repo_path         TEXT NOT NULL,
    -- JSON 陣列。**是陣列不是單值**：同一個人在不同 repo 常常設不同的
    -- user.email（公司信箱、個人信箱、GitHub 的 noreply）。只存一個的話，
    -- 另一個信箱的 commit 會被**靜默漏掉**——草稿照樣產得出來，只是少了
    -- 那幾筆，而使用者沒有任何線索知道自己少填了工時。
    author_emails     TEXT NOT NULL DEFAULT '[]',
    -- JSON 陣列。**空陣列＝掃 `--all`**，有值才當白名單。
    --
    -- ⚠️ 分支語意與 code_projects **相反**，這不是疏忽。那邊是
    -- 「環境→釘死一個分支」，因為查程式碼時拿錯環境等於給出錯的答案；
    -- 這邊問的是「這天我在哪都做了什麼」，漏掉一個還沒合併的 feature 分支
    -- 就等於漏掉那天的工作。所以這裡的預設是掃全部分支，白名單是例外。
    branch_filter     TEXT NOT NULL DEFAULT '[]',
    -- ⚠️ **只用來把該專案的 issue 排進候選清單並標示來源，絕不自動填**
    -- （ADR-0008 的唯一硬規則：專案歸屬一律由 Viewer 在確認畫面選）。
    -- 理由是猜錯的那次長得跟猜對的一模一樣，而 ZPlanner 的工時會進月結，
    -- 月結後不能直接改、要走 correction-request——代價由一個月後發現的人付。
    -- 存 key 而不只是 id，是為了候選清單能直接顯示「PEI-133」這種人看得懂的
    -- 字串，不必為了顯示再打一次 ZPlanner。
    default_zplanner_project_id  INTEGER,
    default_zplanner_project_key TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    -- 與 code_projects 同樣的理由：登錄時就驗一次並把結果存下來，讓設定頁
    -- 能顯示「這個路徑已經不見了」。等到產草稿時才發現，代價是那天的草稿
    -- 少了一整個 repo 的證據。
    last_verified_at  TEXT,
    last_verify_error TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(viewer_id, name),
    -- code_projects 沒有這條，這裡刻意加上：同一個 repo 登錄兩次會讓同一批
    -- commit 被算兩遍，直接汙染時數分配的權重。名稱重複只是難看，路徑重複
    -- 會算錯工時。
    UNIQUE(viewer_id, repo_path)
);
CREATE INDEX IF NOT EXISTS idx_work_repos_viewer ON work_repos(viewer_id, enabled);

-- 工時草稿：一位 Viewer 的某一天（ADR-0008）
--
-- **草稿要落地，不重算。** 產一次要跑 N 個 repo 的 git log、查 ZPlanner、
-- 再讓 AI 寫 note，十幾秒起跳；而且 AI 是非決定性的，明天重算出來的草稿
-- 長得不一樣——證據欄會因此失去可信度（「昨天明明不是這樣」）。另外兩個
-- 現實理由：改到一半離開必須留得住，以及送出後必須留著 ZPlanner 的
-- worklog id，否則沒有任何修改與刪除的入口。
CREATE TABLE IF NOT EXISTS worklog_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id     INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    work_date     TEXT NOT NULL,                    -- 'YYYY-MM-DD'
    -- draft｜partial｜submitted。partial 是「有幾筆送成功、有幾筆沒有」，
    -- 它必須是一個獨立狀態：把它算成 draft 會讓人以為整天都還沒送，
    -- 再按一次就重複填了已經成功的那幾筆。
    status        TEXT NOT NULL DEFAULT 'draft',
    -- Viewer 輸入的「這天總共幾小時」。**與各筆之和不保證相等**——湊成 0.5
    -- 倍數必然有餘數，而真正寫進 ZPlanner 的是各筆之和。兩個數字都要留著，
    -- 確認畫面才講得出「你輸入 8.0，實際會送出 7.5」。
    total_hours   TEXT,
    -- 切日界用的時區偏移（例如 '+08:00'）。**一定要存**：git 的 author date
    -- 帶偏移、Chat 的 createTime 是 UTC、ZPlanner 的 date 沒有時區，「半夜
    -- 一點的 commit 算哪天」完全取決於這個值。不存的話，事後沒有人能重現
    -- 這份草稿當初為什麼把某筆算進這一天。
    tz_offset     TEXT NOT NULL,
    -- 產生當下的 SSE meta 快照，**這是證據欄的唯一來源**（設計規格 §5：
    -- 任何無法回溯到 meta 某個欄位的東西，不准進證據欄）。
    --
    -- ⚠️ 只存識別資訊（commit sha、message_name、space_id、時間），**不存
    -- Chat 的訊息內容**——與 mentions 表同一條鐵律，對話文字顯示時才即時取回。
    generation_meta_json TEXT NOT NULL DEFAULT '{}',
    generated_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    -- 一天一份。同一天產第二次是覆蓋，不是並存——兩份草稿同時存在的話，
    -- 「這天到底送了哪一份」就沒有答案了。
    UNIQUE(viewer_id, work_date)
);
CREATE INDEX IF NOT EXISTS idx_worklog_drafts_viewer
    ON worklog_drafts(viewer_id, work_date DESC);

-- 草稿裡的一筆工時（一個 ZPlanner issue ＋ 幾小時 ＋ note）
CREATE TABLE IF NOT EXISTS worklog_draft_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL REFERENCES worklog_drafts(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    -- 專案歸屬。**產生時一律是 NULL**——系統只給候選，選擇權在 Viewer
    -- （ADR-0008 的唯一硬規則）。有值代表人已經選過了。
    issue_id    INTEGER,
    issue_key   TEXT,
    project_id  INTEGER,
    project_key TEXT,
    -- 實際要送出的時數（人調整過的值）
    hours       TEXT,
    note        TEXT,
    -- 系統原始分配，**與 hours 分開存**。合在一起的話，「這個數字是系統算的
    -- 還是我自己改的」就再也分不出來——而那正是 review 自己的工時時最想知道
    -- 的一件事。也讓「還原系統分配」有東西可以還原。
    allocated_hours TEXT,
    weight      REAL,
    -- 這一筆有沒有拿到湊整的那 0.5 小時。UI 靠它顯示「≈ 湊整 +0.5」——
    -- 餘數落在誰身上必須看得見，否則使用者只會覺得數字莫名其妙。
    rounded_up  INTEGER NOT NULL DEFAULT 0,
    -- 支撐這一筆的證據 ref 清單（"commit:<sha>" / "chat:<message_name>"），
    -- 指回 generation_meta_json 裡的陣列。同樣只存 ref，不存內容。
    evidence_json   TEXT NOT NULL DEFAULT '[]',
    -- 候選 issue ＋ 信心度 ＋ 為什麼是它。**候選要留著**：人選了 A 之後，
    -- 下次回來看仍要知道當初還有 B、C 可選，否則無從判斷自己選得對不對。
    candidates_json TEXT NOT NULL DEFAULT '[]',
    -- pending｜skipped｜submitted｜failed｜unknown
    --
    -- ⚠️ **unknown 是獨立狀態，不是 failed**：連線逾時**不保證沒有寫進去**
    -- （見 ZPlannerUnavailable 的 docstring）。當成 failed 去重送，可能在
    -- ZPlanner 那邊變成兩筆；而月結之後多出來的那筆刪不掉，要走
    -- correction-request。這種筆一律不自動重試，改請人去對帳。
    state       TEXT NOT NULL DEFAULT 'pending',
    zplanner_worklog_id INTEGER,
    zplanner_issue_key  TEXT,
    is_locked   INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT,
    last_error_code    TEXT,
    last_error_message TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(draft_id, seq)
);
-- partial unique index：**同一筆 ZPlanner 工時不可以被兩筆草稿認領**。
-- 這是重複送出的最後一道防線——UI 會擋、state 會擋，但兩者都擋不住
-- 「逾時後人工重試」這條路徑。NULL 不受唯一性約束，所以還沒送出的那些
-- 不會互相卡住。
CREATE UNIQUE INDEX IF NOT EXISTS idx_worklog_entry_remote
    ON worklog_draft_entries(zplanner_worklog_id)
    WHERE zplanner_worklog_id IS NOT NULL;
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
    # 「Viewer 自己選 model」的偏好。舊資料庫補上時是 NULL，讀取端要能把
    # 「沒有這個欄位／值是 NULL」一律當成「沒有偏好」，不是錯誤。
    ("preferences", "default_models_json", "TEXT"),
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
