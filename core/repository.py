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
        "updated_at": row["updated_at"],
    }


def update_preferences(
    viewer_id: int,
    *,
    pinned_space_ids: Optional[List[str]] = None,
    default_limit: Optional[int] = None,
    default_style: Optional[str] = None,
    default_provider: Optional[str] = None,
) -> Dict[str, Any]:
    current = get_preferences(viewer_id)
    pinned = current["pinned_space_ids"] if pinned_space_ids is None else pinned_space_ids
    limit = current["default_limit"] if default_limit is None else default_limit
    style = current["default_style"] if default_style is None else default_style
    provider = (
        current.get("default_provider") if default_provider is None else default_provider
    )
    db.execute(
        """
        UPDATE preferences
           SET pinned_space_ids = ?, default_limit = ?, default_style = ?,
               default_provider = ?, updated_at = ?
         WHERE viewer_id = ?
        """,
        (json.dumps(pinned), limit, style, provider, _now(), viewer_id),
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
    if state:
        sql += " AND state = ?"
        params.append(state)
    else:
        # 沒指定狀態時排除手動草稿目標——那些不是「有人 @ 你」，
        # 混進收件匣會讓待辦清單失真
        sql += " AND state != ?"
        params.append(MANUAL_STATE)
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
        out[r["state"]] = r["n"]
    return out


# --------------------------------------------------------------------------
# Draft Reply
# --------------------------------------------------------------------------


def create_draft(mention_id: int, content_md: str) -> int:
    cur = db.execute(
        "INSERT INTO draft_replies(mention_id, content_md, created_at) VALUES(?, ?, ?)",
        (mention_id, content_md, _now()),
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
