"""資料存取層。

規格對應：SPECIFICATION.md 九節。

ADR-0002 的執行點在這裡：所有 summaries 與 mentions 的查詢函式都把 viewer_id
列為**必填位置參數**，沒有任何「不帶 viewer 查全部」的入口。漏一次就等於全開，
所以這件事用函式簽章擋，不靠呼叫端自律。
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import crypto, db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Viewer
# --------------------------------------------------------------------------


def upsert_viewer(
    google_user_id: str, email: Optional[str], display_name: Optional[str]
) -> Dict[str, Any]:
    """依 google_user_id 建立或更新 Viewer，回傳完整資料列。"""
    db.execute(
        """
        INSERT INTO viewers(google_user_id, email, display_name, created_at, last_seen_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(google_user_id) DO UPDATE SET
            email        = COALESCE(excluded.email, viewers.email),
            display_name = COALESCE(excluded.display_name, viewers.display_name),
            last_seen_at = excluded.last_seen_at
        """,
        (google_user_id, email, display_name, _now(), _now()),
    )
    viewer = db.query_one(
        "SELECT * FROM viewers WHERE google_user_id = ?", (google_user_id,)
    )
    assert viewer is not None
    ensure_preferences(viewer["id"])
    return viewer


def get_viewer(viewer_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM viewers WHERE id = ?", (viewer_id,))


def list_viewers() -> List[Dict[str, Any]]:
    return db.query_all("SELECT * FROM viewers ORDER BY id")


# --------------------------------------------------------------------------
# 憑證（加密存放）
# --------------------------------------------------------------------------


def save_credentials(viewer_id: int, token_json: str, expiry: Optional[str], scopes: List[str]) -> None:
    db.execute(
        """
        INSERT INTO credentials(viewer_id, encrypted_token, expiry, scopes, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(viewer_id) DO UPDATE SET
            encrypted_token = excluded.encrypted_token,
            expiry          = excluded.expiry,
            scopes          = excluded.scopes,
            updated_at      = excluded.updated_at
        """,
        (viewer_id, crypto.encrypt(token_json), expiry, json.dumps(scopes), _now()),
    )


def load_credentials_json(viewer_id: int) -> Optional[str]:
    row = db.query_one(
        "SELECT encrypted_token FROM credentials WHERE viewer_id = ?", (viewer_id,)
    )
    if not row:
        return None
    return crypto.decrypt(row["encrypted_token"])


def credentials_scopes(viewer_id: int) -> List[str]:
    row = db.query_one("SELECT scopes FROM credentials WHERE viewer_id = ?", (viewer_id,))
    if not row or not row["scopes"]:
        return []
    try:
        return list(json.loads(row["scopes"]))
    except (TypeError, ValueError):
        return []


# --------------------------------------------------------------------------
# 偏好
# --------------------------------------------------------------------------


def ensure_preferences(viewer_id: int) -> Dict[str, Any]:
    db.execute(
        """
        INSERT INTO preferences(viewer_id, pinned_space_ids, default_limit, default_style, updated_at)
        VALUES(?, '[]', ?, ?, ?)
        ON CONFLICT(viewer_id) DO NOTHING
        """,
        (viewer_id, cfg.LIMIT_DEFAULT, cfg.SUMMARY_STYLE_DEFAULT, _now()),
    )
    row = db.query_one("SELECT * FROM preferences WHERE viewer_id = ?", (viewer_id,))
    assert row is not None
    return row


def get_preferences(viewer_id: int) -> Dict[str, Any]:
    row = ensure_preferences(viewer_id)
    return {
        "pinned_space_ids": json.loads(row["pinned_space_ids"] or "[]"),
        "default_limit": row["default_limit"],
        "default_style": row["default_style"],
        # 空字串視為「沒有偏好」，讓它退回伺服器預設而不是變成非法值
        "default_provider": row["default_provider"] or None,
        # ADR-0006：草稿頁預選用。只是預選，不會自動送出——
        # code_refs 在 API 契約上仍維持預設空，與 reference_space_ids 一致。
        "default_code_project_id": row["default_code_project_id"],
        "default_code_environment": row["default_code_environment"] or None,
        # ADR-0007：Draft Reply 的回覆設定預設值。
        # default_style（摘要章節結構）與 default_reply_tone（回話語氣）
        # 是兩件不同的事，不共用值域也不互相影響。
        #
        # 這四個欄位用 `row.get()` 而不是 `row[...]`：schema 還沒遷移的
        # 資料庫沒有這些欄位，而**讀取路徑遇到舊 schema 應該降級成
        # 「沒有偏好」，不該是 500**。遷移由 `db.init_db()` 在啟動時保證，
        # 但讀取端不必假設它一定已經跑過（測試與工具腳本會繞過啟動流程）。
        "default_reply_tone": row.get("default_reply_tone") or None,
        # 指向的 persona／preset 可能已被刪除（欄位刻意無外鍵，見 db.py）。
        # 這裡照實回傳，由 API 層在使用前驗擁有權與存在性——
        # 在這裡靜默改成 None 會讓「我的預設 persona 不見了」變成無法察覺的事。
        "default_persona_id": row.get("default_persona_id"),
        "default_reply_prompt_id": row.get("default_reply_prompt_id"),
        # NULL 代表沒有偏好；轉成 bool 前先保留 None 語意（0 是明確關閉）
        "default_sepia_enabled": (
            None
            if row.get("default_sepia_enabled") is None
            else bool(row["default_sepia_enabled"])
        ),
        "updated_at": row["updated_at"],
    }


class _Unset:
    """「這個參數沒有被指定」的哨兵型別。

    存在的理由是 `None` 在新欄位上有實際語意：`default_persona_id = None`
    就是「不使用 Persona」，那是使用者會主動選的選項，必須存得下去。

    既有欄位（`default_provider` 等）沿用 `None` ＝「不改」的舊語意，
    **刻意不一起改**：那會改變既有呼叫端的行為，而需求明確要求
    不藉這次功能順便改既有決策。代價是同一個函式有兩種慣例，
    所以兩邊都在簽章與註解裡標清楚。

    （附帶記錄一個既有限制：因為舊欄位沿用 `None` ＝不改，
    `default_provider` 目前**無法**被清成 NULL。前端「設為預設」選
    「自動」時送 `null`，後端會當成「不改」。這是本功能之前就存在的
    行為，不在這次的範圍內。）
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


#: 給 API 層引用的哨兵單例。
UNSET: Any = _Unset()


def _pick(new: Any, current: Any) -> Any:
    """哨兵語意的取值：沒指定就沿用現值，指定了就用新值（含 None）。"""
    return current if isinstance(new, _Unset) else new


def update_preferences(
    viewer_id: int,
    *,
    # --- 舊欄位：`None` ＝ 不改（既有語意，不要改） ---
    pinned_space_ids: Optional[List[str]] = None,
    default_limit: Optional[int] = None,
    default_style: Optional[str] = None,
    default_provider: Optional[str] = None,
    default_code_project_id: Optional[int] = None,
    default_code_environment: Optional[str] = None,
    # --- 新欄位：沒傳 ＝ 不改，傳 `None` ＝ 清除（見 `_Unset`） ---
    default_reply_tone: Any = UNSET,
    default_persona_id: Any = UNSET,
    default_reply_prompt_id: Any = UNSET,
    default_sepia_enabled: Any = UNSET,
) -> Dict[str, Any]:
    current = get_preferences(viewer_id)
    pinned = current["pinned_space_ids"] if pinned_space_ids is None else pinned_space_ids
    limit = current["default_limit"] if default_limit is None else default_limit
    style = current["default_style"] if default_style is None else default_style
    provider = (
        current.get("default_provider") if default_provider is None else default_provider
    )
    code_project = (
        current.get("default_code_project_id")
        if default_code_project_id is None
        else default_code_project_id
    )
    code_env = (
        current.get("default_code_environment")
        if default_code_environment is None
        else default_code_environment
    )
    tone = _pick(default_reply_tone, current.get("default_reply_tone"))
    persona_id = _pick(default_persona_id, current.get("default_persona_id"))
    prompt_id = _pick(default_reply_prompt_id, current.get("default_reply_prompt_id"))
    sepia = _pick(default_sepia_enabled, current.get("default_sepia_enabled"))
    db.execute(
        """
        UPDATE preferences
           SET pinned_space_ids = ?, default_limit = ?, default_style = ?,
               default_provider = ?, default_code_project_id = ?,
               default_code_environment = ?, default_reply_tone = ?,
               default_persona_id = ?, default_reply_prompt_id = ?,
               default_sepia_enabled = ?, updated_at = ?
         WHERE viewer_id = ?
        """,
        (
            json.dumps(pinned),
            limit,
            style,
            provider,
            code_project,
            code_env,
            tone,
            persona_id,
            prompt_id,
            None if sepia is None else int(bool(sepia)),
            _now(),
            viewer_id,
        ),
    )
    return get_preferences(viewer_id)


# --------------------------------------------------------------------------
# Summary（私有於產生者）
# --------------------------------------------------------------------------


def create_summary(
    owner_viewer_id: int,
    space_id: str,
    space_name: Optional[str],
    style: str,
    message_count: int,
    content_md: str,
) -> int:
    cur = db.execute(
        """
        INSERT INTO summaries(owner_viewer_id, space_id, space_name, style,
                              message_count, content_md, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (owner_viewer_id, space_id, space_name, style, message_count, content_md, _now()),
    )
    return int(cur.lastrowid)


def list_summaries(owner_viewer_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """只回傳指定 Viewer 自己的 Summary。這個函式沒有「查全部」的變體。"""
    return db.query_all(
        """
        SELECT id, space_id, space_name, style, message_count, content_md, created_at
          FROM summaries
         WHERE owner_viewer_id = ?
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (owner_viewer_id, limit),
    )


def get_summary(owner_viewer_id: int, summary_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one(
        "SELECT * FROM summaries WHERE id = ? AND owner_viewer_id = ?",
        (summary_id, owner_viewer_id),
    )


# --------------------------------------------------------------------------
# Mention
# --------------------------------------------------------------------------


def upsert_mention(viewer_id: int, mention: Dict[str, Any]) -> bool:
    """寫入一則 Mention。回傳 True 表示是新的（先前沒見過）。

    以 UNIQUE(viewer_id, message_name) 做冪等：同一則訊息重複採集不會產生第二筆，
    也不會把已處理的狀態改回待處理。
    """
    existing = db.query_one(
        "SELECT id FROM mentions WHERE viewer_id = ? AND message_name = ?",
        (viewer_id, mention["message_name"]),
    )
    if existing:
        # 只補齊可能後來才知道的顯示資訊，不動 state
        db.execute(
            """
            UPDATE mentions
               SET space_name = COALESCE(?, space_name),
                   thread_name = COALESCE(?, thread_name),
                   sender_display = COALESCE(?, sender_display)
             WHERE id = ?
            """,
            (
                mention.get("space_name"),
                mention.get("thread_name"),
                mention.get("sender_display"),
                existing["id"],
            ),
        )
        return False

    db.execute(
        """
        INSERT INTO mentions(viewer_id, space_id, space_name, message_name, thread_name,
                             sender_name, sender_display, create_time, state, detected_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            viewer_id,
            mention["space_id"],
            mention.get("space_name"),
            mention["message_name"],
            mention.get("thread_name"),
            mention.get("sender_name"),
            mention.get("sender_display"),
            mention["create_time"],
            _now(),
        ),
    )
    return True


#: 手動指定的草稿目標。不是真的被 @，只是為了讓草稿有東西可以掛（見 upsert_draft_target）
MANUAL_STATE = "manual"


def upsert_draft_target(viewer_id: int, mention: Dict[str, Any]) -> int:
    """為「手動挑的一則訊息」建立草稿目標，回傳 mention id。

    草稿的資料模型綁在 mentions 上（`draft_replies.mention_id` 是外鍵），
    但私訊不會產生 mention——沒人會在私訊裡 @ 你。這裡合成一筆
    state='manual' 的記錄，讓「對任何對話產生回覆」重用整條既有的草稿流程，
    不必改 schema。這種記錄會被收件匣過濾掉（見 list_mentions）。

    冪等：同一則訊息重複建立會拿回同一筆。若那則訊息**本來就是**真的 mention
    （你確實被 @ 了），直接沿用原記錄，不把它降級成 manual。
    """
    existing = db.query_one(
        "SELECT id FROM mentions WHERE viewer_id = ? AND message_name = ?",
        (viewer_id, mention["message_name"]),
    )
    if existing:
        return int(existing["id"])
    cur = db.execute(
        """
        INSERT INTO mentions(viewer_id, space_id, space_name, message_name, thread_name,
                             sender_name, sender_display, create_time, state, detected_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            viewer_id,
            mention["space_id"],
            mention.get("space_name"),
            mention["message_name"],
            mention.get("thread_name"),
            mention.get("sender_name"),
            mention.get("sender_display"),
            mention["create_time"],
            MANUAL_STATE,
            _now(),
        ),
    )
    return int(cur.lastrowid)


def list_mentions(
    viewer_id: int, state: Optional[str] = None, limit: int = 200
) -> List[Dict[str, Any]]:
    sql = """
        SELECT * FROM mentions
         WHERE viewer_id = ?
    """
    params: List[Any] = [viewer_id]
    if state == "pending":
        # manual（從摘要工作台按「產生回覆草稿」挑的）**也算待處理**。
        #
        # 一開始的判斷是把它排除，理由是「那不是有人 @ 你，混進來會讓待辦
        # 清單失真」。但那是從資料來源看事情——從使用者的角度，他按下那個
        # 按鈕的意思就是「我要回這則」，跟被 @ 一樣是一件待辦。排除的結果是
        # 他產完草稿切到收件匣，兩個分頁都找不到自己剛做的事。
        # 至於「私訊會不會淹沒清單」：不會，只有他主動按按鈕的才會建立。
        sql += " AND state IN (?, ?)"
        params += ["pending", MANUAL_STATE]
    elif state:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY create_time DESC LIMIT ?"
    params.append(limit)
    return db.query_all(sql, params)


def get_mention(viewer_id: int, mention_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one(
        "SELECT * FROM mentions WHERE id = ? AND viewer_id = ?", (mention_id, viewer_id)
    )


def set_mention_state(viewer_id: int, mention_id: int, state: str) -> Optional[Dict[str, Any]]:
    resolved_at = _now() if state == "resolved" else None
    db.execute(
        "UPDATE mentions SET state = ?, resolved_at = ? WHERE id = ? AND viewer_id = ?",
        (state, resolved_at, mention_id, viewer_id),
    )
    return get_mention(viewer_id, mention_id)


def count_mentions(viewer_id: int) -> Dict[str, int]:
    rows = db.query_all(
        "SELECT state, COUNT(*) AS n FROM mentions WHERE viewer_id = ? GROUP BY state",
        (viewer_id,),
    )
    out = {"pending": 0, "resolved": 0}
    for r in rows:
        # manual 併進 pending（理由見 list_mentions）。用 += 而不是 =，
        # 因為 pending 與 manual 是兩列，直接賦值會讓後來的那列蓋掉前面的。
        key = "pending" if r["state"] in ("pending", MANUAL_STATE) else r["state"]
        if key in out:
            out[key] += r["n"]
    return out


# --------------------------------------------------------------------------
# Draft Reply
# --------------------------------------------------------------------------


def create_draft(
    mention_id: int,
    content_md: str,
    generation_config: Optional[Dict[str, Any]] = None,
) -> int:
    """存一份草稿。

    `generation_config` 是「這份草稿用什麼設定產生的」（provider／model／
    tone／persona／sepia 與潤稿結果）。**選填**，理由有兩個：舊資料庫的
    既有草稿沒有這份資訊，而斷線補存路徑（`server.save_partial`）拿到的
    是一段不完整的文字，那時候記下設定仍然有意義但不該是必要條件。

    只存一份 `content_md`（潤稿後的最終版），不另存未潤稿版——
    見 `core/db.py` 的欄位註解。
    """
    cur = db.execute(
        "INSERT INTO draft_replies(mention_id, content_md, created_at, "
        "generation_config_json) VALUES(?, ?, ?, ?)",
        (
            mention_id,
            content_md,
            _now(),
            json.dumps(generation_config, ensure_ascii=False) if generation_config else None,
        ),
    )
    return int(cur.lastrowid)


def mark_draft_sent(viewer_id: int, draft_id: int) -> bool:
    """把草稿標記為已送出。回傳 True 表示真的有更新到。

    **viewer_id 是必填的**：`draft_id` 由前端傳入，若不驗擁有權，Viewer A 就能
    帶著 Viewer B 的 draft_id 把對方的草稿標成已送出。draft_replies 自己沒有
    viewer 欄位，所以透過 mention 反查（mentions 才是綁 viewer 的那張表）。
    """
    cur = db.execute(
        """
        UPDATE draft_replies
           SET sent_at = ?
         WHERE id = ?
           AND mention_id IN (SELECT id FROM mentions WHERE viewer_id = ?)
        """,
        (_now(), draft_id, viewer_id),
    )
    return cur.rowcount > 0


def latest_draft(mention_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one(
        "SELECT * FROM draft_replies WHERE mention_id = ? ORDER BY created_at DESC LIMIT 1",
        (mention_id,),
    )


# --------------------------------------------------------------------------
# 採集器水位
# --------------------------------------------------------------------------


def get_collector_state(viewer_id: int) -> Dict[str, Any]:
    row = db.query_one("SELECT * FROM collector_state WHERE viewer_id = ?", (viewer_id,))
    if row:
        return row
    db.execute("INSERT INTO collector_state(viewer_id) VALUES(?)", (viewer_id,))
    row = db.query_one("SELECT * FROM collector_state WHERE viewer_id = ?", (viewer_id,))
    assert row is not None
    return row


def set_collector_state(
    viewer_id: int,
    *,
    last_polled_at: Optional[str] = None,
    last_error: Optional[str] = None,
    last_run_stats: Optional[Dict[str, Any]] = None,
) -> None:
    get_collector_state(viewer_id)
    db.execute(
        """
        UPDATE collector_state
           SET last_polled_at = COALESCE(?, last_polled_at),
               last_error     = ?,
               last_run_stats = COALESCE(?, last_run_stats)
         WHERE viewer_id = ?
        """,
        (
            last_polled_at,
            last_error,
            json.dumps(last_run_stats, ensure_ascii=False) if last_run_stats else None,
            viewer_id,
        ),
    )


# --------------------------------------------------------------------------
# Token 用量（R-2）
# --------------------------------------------------------------------------


def record_token_usage(
    viewer_id: Optional[int],
    model: str,
    operation: str,
    prompt_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.execute(
        """
        INSERT INTO token_usage(day, viewer_id, model, operation,
                                prompt_tokens, output_tokens, total_tokens, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (day, viewer_id, model, operation, prompt_tokens, output_tokens, total_tokens, _now()),
    )


def daily_token_usage(viewer_id: int, days: int = 14) -> List[Dict[str, Any]]:
    """某位 Viewer 近 N 天的每日 token 用量（R-2）。

    兩個曾經寫錯的地方，都在這裡修掉了：
      1. **必須帶 viewer_id。** token_usage 有記 viewer_id，但先前的查詢只
         `GROUP BY day, model`，任何登入者都看得到全部 Viewer 的合計用量——
         ADR-0002 連同群的 Summary 都不給看，活動量卻全開，標準不一致。
      2. **用日期下界限制天數，不要用 `LIMIT days`。** `LIMIT` 限制的是分組列數，
         不是天數；一旦用了第二個模型（R-4 提到可用 CHATPULSE_GEMINI_MODEL 換），
         `days=14` 會靜默只回 7 天。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return db.query_all(
        """
        SELECT day, model,
               SUM(prompt_tokens) AS prompt_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(total_tokens)  AS total_tokens,
               COUNT(*)           AS calls
          FROM token_usage
         WHERE viewer_id = ? AND day >= ?
         GROUP BY day, model
         ORDER BY day DESC, model
        """,
        (viewer_id, cutoff),
    )


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


def create_session(viewer_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=cfg.SESSION_TTL_HOURS)
    db.execute(
        "INSERT INTO sessions(token, viewer_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
        (token, viewer_id, _now(), expires.isoformat(timespec="seconds")),
    )
    return token


def resolve_session(token: str) -> Optional[Dict[str, Any]]:
    row = db.query_one(
        """
        SELECT s.token, s.expires_at, v.*
          FROM sessions s JOIN viewers v ON v.id = s.viewer_id
         WHERE s.token = ?
        """,
        (token,),
    )
    if not row:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return row


def delete_session(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


# --------------------------------------------------------------------------
# 保留策略（九節：summaries／mentions 90 天後清除）
# --------------------------------------------------------------------------


def purge_expired() -> Dict[str, int]:
    cutoff_summary = (
        datetime.now(timezone.utc) - timedelta(days=cfg.SUMMARY_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    cutoff_mention = (
        datetime.now(timezone.utc) - timedelta(days=cfg.MENTION_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    s = db.execute("DELETE FROM summaries WHERE created_at < ?", (cutoff_summary,))
    m = db.execute("DELETE FROM mentions WHERE detected_at < ?", (cutoff_mention,))
    return {"summaries_deleted": s.rowcount, "mentions_deleted": m.rowcount}


# --------------------------------------------------------------------------
# 參考專案（ADR-0006）
#
# 與本模組其他函式一樣，viewer_id 是必填且擺第一：沒有「查全部專案」的入口。
# 分支對照永遠跟著父專案一起讀，因此也不存在繞過 viewer_id 讀到別人分支對應的路徑。
# --------------------------------------------------------------------------


def _code_project_row(row: Dict[str, Any], branches: Dict[str, str]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "repo_path": row["repo_path"],
        "default_env": row["default_env"],
        "include_globs": json.loads(row["include_globs"] or "[]"),
        "exclude_globs": json.loads(row["exclude_globs"] or "[]"),
        "enabled": bool(row["enabled"]),
        "branches": branches,
        "last_verified_at": row["last_verified_at"],
        "last_verify_error": row["last_verify_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _branches_for(project_ids: List[int]) -> Dict[int, Dict[str, str]]:
    """一次撈完所有分支對照，避免 N+1。"""
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    rows = db.query_all(
        f"SELECT project_id, environment, branch FROM code_project_branches "
        f"WHERE project_id IN ({marks})",
        tuple(project_ids),
    )
    out: Dict[int, Dict[str, str]] = {pid: {} for pid in project_ids}
    for r in rows:
        out[r["project_id"]][r["environment"]] = r["branch"]
    return out


def list_code_projects(viewer_id: int, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM code_projects WHERE viewer_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY name"
    rows = db.query_all(sql, (viewer_id,))
    branches = _branches_for([r["id"] for r in rows])
    return [_code_project_row(r, branches.get(r["id"], {})) for r in rows]


def get_code_project(viewer_id: int, project_id: int) -> Optional[Dict[str, Any]]:
    row = db.query_one(
        "SELECT * FROM code_projects WHERE viewer_id = ? AND id = ?",
        (viewer_id, project_id),
    )
    if row is None:
        return None
    return _code_project_row(row, _branches_for([project_id]).get(project_id, {}))


def create_code_project(
    viewer_id: int,
    *,
    name: str,
    repo_path: str,
    branches: Dict[str, str],
    default_env: str = cfg.CODE_ENV_DEFAULT,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
) -> int:
    """建立專案與分支對照。

    兩張表要在**同一個 transaction** 內寫完——否則中途失敗會留下
    「專案存在但沒有分支對應」的殘骸，而那正是這個功能要避免的狀態
    （沒有分支對應 = 不知道該查哪個環境）。這是 db.execute（每次自帶
    `with conn`）不夠用、必須直接取連線的唯一地方。
    """
    now = _now()
    conn = db.get_connection()
    with conn:
        cur = conn.execute(
            """
            INSERT INTO code_projects(viewer_id, name, repo_path, default_env,
                                      include_globs, exclude_globs, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                viewer_id,
                name,
                repo_path,
                default_env,
                json.dumps(include_globs or []),
                json.dumps(exclude_globs or []),
                now,
                now,
            ),
        )
        project_id = int(cur.lastrowid)
        for env, branch in branches.items():
            conn.execute(
                """
                INSERT INTO code_project_branches(project_id, environment, branch, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (project_id, env, branch, now),
            )
    return project_id


def update_code_project(
    viewer_id: int,
    project_id: int,
    *,
    name: Optional[str] = None,
    repo_path: Optional[str] = None,
    branches: Optional[Dict[str, str]] = None,
    default_env: Optional[str] = None,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    current = get_code_project(viewer_id, project_id)
    if current is None:
        return None
    now = _now()
    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            UPDATE code_projects
               SET name = ?, repo_path = ?, default_env = ?,
                   include_globs = ?, exclude_globs = ?, enabled = ?, updated_at = ?
             WHERE viewer_id = ? AND id = ?
            """,
            (
                current["name"] if name is None else name,
                current["repo_path"] if repo_path is None else repo_path,
                current["default_env"] if default_env is None else default_env,
                json.dumps(
                    current["include_globs"] if include_globs is None else include_globs
                ),
                json.dumps(
                    current["exclude_globs"] if exclude_globs is None else exclude_globs
                ),
                int(current["enabled"] if enabled is None else enabled),
                now,
                viewer_id,
                project_id,
            ),
        )
        if branches is not None:
            # 整組取代而不是逐項 merge：分支對照是一份小而完整的對應表，
            # 部分更新會讓「刪掉 uat 對應」這個動作沒有辦法表達。
            conn.execute(
                "DELETE FROM code_project_branches WHERE project_id = ?", (project_id,)
            )
            for env, branch in branches.items():
                conn.execute(
                    """
                    INSERT INTO code_project_branches(project_id, environment, branch, created_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (project_id, env, branch, now),
                )
    return get_code_project(viewer_id, project_id)


def delete_code_project(viewer_id: int, project_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM code_projects WHERE viewer_id = ? AND id = ?", (viewer_id, project_id)
    )
    return cur.rowcount > 0


def resolve_branch(viewer_id: int, project_id: int, environment: str) -> Optional[str]:
    """查某專案在某環境對應的分支。查不到回 None，由呼叫端決定怎麼報錯。"""
    project = get_code_project(viewer_id, project_id)
    if project is None:
        return None
    return project["branches"].get(environment)


def record_project_verification(
    viewer_id: int, project_id: int, error: Optional[str]
) -> None:
    """記錄驗證結果，供設定頁顯示「這個分支已經不在了」。"""
    db.execute(
        """
        UPDATE code_projects SET last_verified_at = ?, last_verify_error = ?
         WHERE viewer_id = ? AND id = ?
        """,
        (_now(), error, viewer_id, project_id),
    )


# --------------------------------------------------------------------------
# Persona（ADR-0007）
#
# 每一個函式都把 viewer_id 列為必填的第一個位置參數，與 summaries／mentions／
# code_projects 同一條紀律（見模組 docstring）：Viewer A 不得讀到 Viewer B 的
# Persona。沒有「查全部」的入口。
# --------------------------------------------------------------------------


def _persona_row(row: Dict[str, Any], *, include_raw: bool = False) -> Dict[str, Any]:
    """DB 列 → API 形狀。

    **預設不含 `raw_source`。** 那是遠端原文，只在使用者明確要看「淨化掉了
    什麼」時才需要，而它有 20 KB 上下——放進列表回應會讓 `GET /personas`
    的體積跟著 persona 數量線性膨脹。更重要的是：讓原文預設不出現在
    API 回應裡，可以少一條「有人把它接回 prompt」的路徑。
    """
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "source_type": row["source_type"],
        "source_repository": row["source_repository"],
        "source_url": row["source_url"],
        "source_ref": row["source_ref"],
        "source_commit_sha": row["source_commit_sha"],
        "source_hash": row["source_hash"],
        "enabled": bool(row["enabled"]),
        "imported_at": row["imported_at"],
        "refreshed_at": row["refreshed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "profile": json.loads(row["profile_json"] or "{}"),
    }
    if include_raw:
        out["raw_source"] = row["raw_source"]
    return out


def list_personas(viewer_id: int, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM personas WHERE viewer_id = ?"
    params: List[Any] = [viewer_id]
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY name COLLATE NOCASE"
    return [_persona_row(row) for row in db.query_all(sql, params)]


def get_persona(
    viewer_id: int, persona_id: int, *, include_raw: bool = False
) -> Optional[Dict[str, Any]]:
    row = db.query_one(
        "SELECT * FROM personas WHERE id = ? AND viewer_id = ?",
        (persona_id, viewer_id),
    )
    return _persona_row(row, include_raw=include_raw) if row else None


def find_persona_by_name(viewer_id: int, name: str) -> Optional[Dict[str, Any]]:
    """按名稱找，用於匯入時判斷是「新增」還是「更新」。"""
    row = db.query_one(
        "SELECT * FROM personas WHERE viewer_id = ? AND name = ?",
        (viewer_id, (name or "").strip()),
    )
    return _persona_row(row) if row else None


def create_persona(
    viewer_id: int,
    *,
    name: str,
    description: str,
    profile_json: str,
    source_type: str,
    source_repository: Optional[str] = None,
    source_url: Optional[str] = None,
    source_ref: Optional[str] = None,
    source_commit_sha: Optional[str] = None,
    source_hash: Optional[str] = None,
    raw_source: Optional[str] = None,
) -> int:
    now = _now()
    cur = db.execute(
        """
        INSERT INTO personas(
            viewer_id, name, description, source_type, source_repository,
            source_url, source_ref, source_commit_sha, source_hash,
            profile_json, raw_source, enabled, imported_at, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            viewer_id,
            name.strip(),
            (description or "").strip(),
            source_type,
            source_repository,
            source_url,
            source_ref,
            source_commit_sha,
            source_hash,
            profile_json,
            raw_source,
            now,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_persona(
    viewer_id: int,
    persona_id: int,
    *,
    name: Any = UNSET,
    description: Any = UNSET,
    profile_json: Any = UNSET,
    enabled: Any = UNSET,
    source_ref: Any = UNSET,
    source_commit_sha: Any = UNSET,
    source_hash: Any = UNSET,
    raw_source: Any = UNSET,
    touch_refreshed: bool = False,
) -> Optional[Dict[str, Any]]:
    """更新 Persona。用 UNSET 哨兵，因為 `description` 可以被清成空字串。

    `touch_refreshed=True` 用在「更新 Persona」（重新從來源取得）——
    它與 `updated_at` 分開記錄：後者任何編輯都會動，前者專指
    「重新從遠端拉了一次」。使用者要判斷的是「這份人格多久沒同步了」，
    改個名字不該讓那個時間跟著跳。
    """
    current = get_persona(viewer_id, persona_id)
    if current is None:
        return None

    new_name = _pick(name, current["name"])
    new_desc = _pick(description, current["description"])
    new_profile = _pick(profile_json, json.dumps(current["profile"], ensure_ascii=False))
    new_enabled = _pick(enabled, current["enabled"])
    new_ref = _pick(source_ref, current["source_ref"])
    new_sha = _pick(source_commit_sha, current["source_commit_sha"])
    new_hash = _pick(source_hash, current["source_hash"])

    sets = [
        "name = ?",
        "description = ?",
        "profile_json = ?",
        "enabled = ?",
        "source_ref = ?",
        "source_commit_sha = ?",
        "source_hash = ?",
        "updated_at = ?",
    ]
    params: List[Any] = [
        (new_name or "").strip(),
        (new_desc or "").strip(),
        new_profile,
        int(bool(new_enabled)),
        new_ref,
        new_sha,
        new_hash,
        _now(),
    ]
    # raw_source 只在明確傳入時才寫，避免把既有原文覆蓋成 None
    if not isinstance(raw_source, _Unset):
        sets.append("raw_source = ?")
        params.append(raw_source)
    if touch_refreshed:
        sets.append("refreshed_at = ?")
        params.append(_now())

    params.extend([persona_id, viewer_id])
    db.execute(
        f"UPDATE personas SET {', '.join(sets)} WHERE id = ? AND viewer_id = ?",
        params,
    )
    return get_persona(viewer_id, persona_id)


def delete_persona(viewer_id: int, persona_id: int) -> bool:
    """刪除 Persona，並清掉指向它的偏好。

    偏好欄位刻意沒有外鍵（見 `core/db.py`），所以這裡要自己清。
    不清的話 `default_persona_id` 會指向一個不存在的 id，
    而使用者下次產草稿時會拿到「沒有套用 persona」但 UI 顯示有選——
    那種不一致查起來很費時。
    """
    cur = db.execute(
        "DELETE FROM personas WHERE id = ? AND viewer_id = ?",
        (persona_id, viewer_id),
    )
    deleted = cur.rowcount > 0
    if deleted:
        db.execute(
            "UPDATE preferences SET default_persona_id = NULL, updated_at = ? "
            "WHERE viewer_id = ? AND default_persona_id = ?",
            (_now(), viewer_id, persona_id),
        )
    return deleted


# --------------------------------------------------------------------------
# Reply Prompt Preset（ADR-0007）
# --------------------------------------------------------------------------


def _reply_prompt_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "prompt": row["prompt"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_reply_prompts(viewer_id: int) -> List[Dict[str, Any]]:
    return [
        _reply_prompt_row(row)
        for row in db.query_all(
            "SELECT * FROM reply_prompts WHERE viewer_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (viewer_id,),
        )
    ]


def get_reply_prompt(viewer_id: int, prompt_id: int) -> Optional[Dict[str, Any]]:
    row = db.query_one(
        "SELECT * FROM reply_prompts WHERE id = ? AND viewer_id = ?",
        (prompt_id, viewer_id),
    )
    return _reply_prompt_row(row) if row else None


def get_reply_prompt_by_name(viewer_id: int, name: str) -> Optional[Dict[str, Any]]:
    """按名稱找。

    存在的理由是給 API 層做「同名檢查」：`UNIQUE(viewer_id, name)` 會擋住
    重複，但那會變成 sqlite3.IntegrityError（500）。先查一次才能回
    一句使用者看得懂的 400。
    """
    row = db.query_one(
        "SELECT * FROM reply_prompts WHERE viewer_id = ? AND name = ?",
        (viewer_id, (name or "").strip()),
    )
    return _reply_prompt_row(row) if row else None


def create_reply_prompt(
    viewer_id: int, *, name: str, description: str, prompt: str
) -> int:
    now = _now()
    cur = db.execute(
        "INSERT INTO reply_prompts(viewer_id, name, description, prompt, "
        "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
        (viewer_id, name.strip(), (description or "").strip(), prompt, now, now),
    )
    return int(cur.lastrowid)


def update_reply_prompt(
    viewer_id: int,
    prompt_id: int,
    *,
    name: Any = UNSET,
    description: Any = UNSET,
    prompt: Any = UNSET,
) -> Optional[Dict[str, Any]]:
    current = get_reply_prompt(viewer_id, prompt_id)
    if current is None:
        return None
    db.execute(
        "UPDATE reply_prompts SET name = ?, description = ?, prompt = ?, "
        "updated_at = ? WHERE id = ? AND viewer_id = ?",
        (
            (_pick(name, current["name"]) or "").strip(),
            (_pick(description, current["description"]) or "").strip(),
            _pick(prompt, current["prompt"]),
            _now(),
            prompt_id,
            viewer_id,
        ),
    )
    return get_reply_prompt(viewer_id, prompt_id)


def delete_reply_prompt(viewer_id: int, prompt_id: int) -> bool:
    """刪除 preset，並清掉指向它的偏好（理由同 `delete_persona`）。"""
    cur = db.execute(
        "DELETE FROM reply_prompts WHERE id = ? AND viewer_id = ?",
        (prompt_id, viewer_id),
    )
    deleted = cur.rowcount > 0
    if deleted:
        db.execute(
            "UPDATE preferences SET default_reply_prompt_id = NULL, updated_at = ? "
            "WHERE viewer_id = ? AND default_reply_prompt_id = ?",
            (_now(), viewer_id, prompt_id),
        )
    return deleted
