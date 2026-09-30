"""資料存取層。

規格對應：SPECIFICATION.md 九節。

ADR-0002 的執行點在這裡：所有 summaries 與 mentions 的查詢函式都把 viewer_id
列為**必填位置參數**，沒有任何「不帶 viewer 查全部」的入口。漏一次就等於全開，
所以這件事用函式簽章擋，不靠呼叫端自律。
"""

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import crypto, db, providers
from .errors import InvalidParameter, WorklogAlreadySubmitted

log = logging.getLogger("chatpulse.repository")


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
        # 各 AI 供應商各自的預設 model（裸值）。沒設過是空 dict，不是 None——
        # 呼叫端一律 `default_models.get(provider_name)`，不必先判空。
        "default_models": _parse_default_models(row.get("default_models_json")),
        "updated_at": row["updated_at"],
    }


def _parse_default_models(raw: Any) -> Dict[str, str]:
    """把 `preferences.default_models_json` 解析成 {實作名: 裸值}。

    🔴 **這裡刻意不照專案硬規則「認不出來就拋」**，理由是它的下游不同：
    這是偏好讀取路徑，而偏好是「錦上添花」的資料。壞掉的 JSON 若往上拋，
    Viewer 會遇到整個設定頁與摘要頁都打不開，而且自己修不了（壞值在 DB 裡）；
    退化成「沒有偏好」的代價只是回到 config 預設，看得見也救得回來。

    同理逐一檢查型別而不是整包信任：非字串的值（例如手動改 DB 改成數字）
    會一路流進 `--model`，錯誤要到執行 CLI 才浮現，不如在這裡就當它不存在。

    🔴 **鍵要濾掉已經不存在的供應商**（2026-09-17 驗收實測）。
    不濾的話會出現一條自己解不開的死結：供應商名冊會變動（`claude_api` 已於
    2026-09-05 移除），Viewer 存過的舊鍵留在 JSON 裡 → 讀出來原樣給前端 →
    前端「設為預設」是送**整份**回來 → API 逐鍵驗合法性 → 400。那位 Viewer
    從此存不了任何 model 偏好，而錯誤訊息指著一個他從來沒選過的供應商名，
    UI 上也沒有任何地方刪得掉它。濾掉之後下一次整份覆寫就會把髒鍵洗掉，會自癒。
    這道防護 `default_provider` 早就有（見 dashboard 的 `preferred_provider_name`），
    是 `default_models` 漏了，不是新規定。

    丟東西要留紀錄：Viewer 只會看到「我的偏好自己不見了」，沒有 log 就查不出原因。
    每次讀取都可能印一行（這支在多數請求上都會被呼叫過），與 `default_provider`
    的既有行為一致；髒鍵被洗掉之後就不會再印。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("default_models 偏好不是合法 JSON，這次當作沒設過：%r", raw)
        return {}
    if not isinstance(data, dict):
        log.warning("default_models 偏好不是 JSON 物件，這次當作沒設過：%r", data)
        return {}

    out: Dict[str, str] = {}
    malformed: List[str] = []
    unknown: List[str] = []
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            malformed.append(repr(key))
            continue
        name, model = key.strip(), value.strip()
        if not name or not model:
            continue
        if name not in providers.MODEL_CHOICES:
            unknown.append(name)
            continue
        out[name] = model
    if malformed:
        log.warning("default_models 偏好有型別不對的項目，已略過：%s", "、".join(malformed))
    if unknown:
        log.warning(
            "default_models 偏好裡的 %s 已不是合法的供應商實作名，已略過"
            "（下次存偏好時會一併清掉）",
            "、".join(sorted(unknown)),
        )
    return out


def get_default_models(viewer_id: int) -> Dict[str, str]:
    """這位 Viewer 各供應商的預設 model（裸值）。沒設過回空 dict。

    值是裸值（`opus`），不是用量表的識別字（`claude-cli:opus`）。
    """
    return _parse_default_models(ensure_preferences(viewer_id).get("default_models_json"))


def set_default_models(viewer_id: int, mapping: Dict[str, str]) -> None:
    """整份覆寫這位 Viewer 的 model 偏好。

    值為 None 或空字串的鍵會被剔除——「清除某個 provider 的偏好」與
    「把它存成 null」在讀取端是同一件事，但存 null 會讓 JSON 裡長期累積
    一堆沒有意義的鍵，日後很難分辨「沒設過」與「設過又清掉」。
    全部清空時寫回 NULL 而不是 `{}`，讓「沒有偏好」在 DB 裡只有一種樣子。

    鍵應該是**具體實作名**（`claude_cli`／`gemini`），不是別名——別名會展開成
    不同實作，存進去就對應不回來。這件事由 API 層驗（它才知道要回幾號錯誤），
    這裡不重複擋，避免兩邊的判準日後各走各的。

    ⚠️ 寫入不擋未知的鍵、讀取卻會濾掉，兩邊**刻意不對稱**：讀取那側要處理的是
    「存的時候合法、後來供應商被移除」的舊資料（見 `_parse_default_models`），
    那是時間造成的，不是寫入時擋得掉的。
    """
    ensure_preferences(viewer_id)
    cleaned: Dict[str, str] = {}
    for key, value in (mapping or {}).items():
        if value is None:
            continue
        if not isinstance(key, str) or not isinstance(value, str):
            # 寫入路徑遇到型別不對一律拋，不要靜默跳過：靜默跳過的下場是
            # 前端顯示「已儲存」但什麼也沒存，Viewer 只會覺得功能壞了卻查不出來。
            # （讀取路徑相反——見 `_parse_default_models` 的理由。）
            raise InvalidParameter(
                f"model 偏好的鍵與值都必須是字串，收到 {key!r}: {value!r}"
            )
        if key.strip() and value.strip():
            cleaned[key.strip()] = value.strip()
    db.execute(
        "UPDATE preferences SET default_models_json = ?, updated_at = ? "
        "WHERE viewer_id = ?",
        (
            json.dumps(cleaned, ensure_ascii=False) if cleaned else None,
            _now(),
            viewer_id,
        ),
    )


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


def mention_ids_with_drafts(viewer_id: int) -> set:
    """這個 Viewer 的哪些 Mention 已經有存下來的草稿。

    一次查完再由呼叫端貼回清單，而不是每列各查一次——收件匣一次就是
    200 列（`list_mentions` 的預設上限）。

    只回 id 集合、不回內容：清單不需要草稿全文，而 `content_md` 動輒
    一兩千字，200 列全帶會讓那個端點的回應大上一個量級。
    """
    rows = db.query_all(
        """
        SELECT DISTINCT d.mention_id AS mention_id
          FROM draft_replies d
          JOIN mentions m ON m.id = d.mention_id
         WHERE m.viewer_id = ?
        """,
        (viewer_id,),
    )
    return {row["mention_id"] for row in rows}


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


#: 工時草稿的保留策略（ADR-0008／計畫 §4）。三個數字刻意不同：
#:
#:   * 未送出的草稿 90 天就丟——它只是一份「還沒決定的東西」，與 summaries／
#:     mentions 同一個級距。
#:   * 已送出的留 365 天，但**不是為了證據**，是為了留住 `zplanner_worklog_id`：
#:     少了它就沒有任何修改與刪除的入口（月結後還要走 correction-request）。
#:   * 所以已送出的那些在 90 天後**就地清空** `evidence_json`／`candidates_json`——
#:     ZPlanner 的 id 索引還在，證據不留那麼久。
#:
#: 放在本模組而不是 `core/config.py`：這三個值不是部署時該調的旋鈕，
#: 改動它們等於改動保留承諾本身。（`SUMMARY_RETENTION_DAYS` 當初放 config
#: 是歷史選擇，沒有跟著搬是為了不動既有行為。）
WORKLOG_DRAFT_RETENTION_DAYS = 90
WORKLOG_SUBMITTED_RETENTION_DAYS = 365
WORKLOG_EVIDENCE_RETENTION_DAYS = 90


def purge_expired() -> Dict[str, int]:
    now = datetime.now(timezone.utc)
    cutoff_summary = (
        now - timedelta(days=cfg.SUMMARY_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    cutoff_mention = (
        now - timedelta(days=cfg.MENTION_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    s = db.execute("DELETE FROM summaries WHERE created_at < ?", (cutoff_summary,))
    m = db.execute("DELETE FROM mentions WHERE detected_at < ?", (cutoff_mention,))

    # --- 工時草稿（ADR-0008）---
    #
    # 三段都用 `updated_at` 當基準，不用 `work_date`：使用者上週才回頭補的
    # 半年前那一天，是**還在動的東西**，不該因為那天很久以前就被清掉。
    # entries 靠 `ON DELETE CASCADE` 跟著走（db.py 的外鍵，連線已開 foreign_keys）。
    cutoff_draft = (
        now - timedelta(days=WORKLOG_DRAFT_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    cutoff_submitted = (
        now - timedelta(days=WORKLOG_SUBMITTED_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    cutoff_evidence = (
        now - timedelta(days=WORKLOG_EVIDENCE_RETENTION_DAYS)
    ).isoformat(timespec="seconds")

    unsent = db.execute(
        "DELETE FROM worklog_drafts WHERE status = 'draft' AND updated_at < ?",
        (cutoff_draft,),
    )
    sent = db.execute(
        "DELETE FROM worklog_drafts WHERE status <> 'draft' AND updated_at < ?",
        (cutoff_submitted,),
    )
    # 就地清空證據。**不動 `worklog_drafts.updated_at`**：動了的話這些列會
    # 永遠看起來很新，365 天那條線就永遠不會到。也只挑「還有東西可清」的列，
    # 免得每天重跑一次把整批 entries 的 updated_at 洗掉。
    scrubbed = db.execute(
        """
        UPDATE worklog_draft_entries
           SET evidence_json = '[]', candidates_json = '[]', updated_at = ?
         WHERE (evidence_json NOT IN ('[]', '') OR candidates_json NOT IN ('[]', ''))
           AND draft_id IN (
                 SELECT id FROM worklog_drafts
                  WHERE status <> 'draft' AND updated_at < ?
               )
        """,
        (_now(), cutoff_evidence),
    )
    return {
        "summaries_deleted": s.rowcount,
        "mentions_deleted": m.rowcount,
        "worklog_drafts_deleted": unsent.rowcount + sent.rowcount,
        "worklog_evidence_scrubbed": scrubbed.rowcount,
    }


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


# --------------------------------------------------------------------------
# 我的工作 repo（Draft Worklog 的證據來源，ADR-0008）
#
# 與本模組其他函式同一條紀律：viewer_id 必填且擺第一，沒有「查全部」的入口。
#
# 這一組刻意與上面的 code_projects 那一組**分開**而不是共用：兩者都是
# 「Viewer 登錄的本機 git repo」，但語意不同（查程式碼 vs 回想做了什麼），
# 欄位不同，分支的預設語意甚至相反（見 core/db.py 的 work_repos 註解）。
# --------------------------------------------------------------------------


def _work_repo_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """把資料列轉成 API 形狀。JSON 欄位在這裡解開，呼叫端拿到的是真的 list。

    `author_emails` 與 `branch_filter` 在資料庫裡是 TEXT（JSON 陣列字串），
    與 code_projects 的 include_globs／exclude_globs 同一個做法：SQLite 沒有
    陣列型別，而這兩個欄位不需要被查詢或 join，另開一張子表只會多一次
    N+1 與一組要維護的同步邏輯。
    """
    return {
        "id": row["id"],
        "name": row["name"],
        "repo_path": row["repo_path"],
        "author_emails": json.loads(row["author_emails"] or "[]"),
        "branch_filter": json.loads(row["branch_filter"] or "[]"),
        "default_zplanner_project_id": row["default_zplanner_project_id"],
        "default_zplanner_project_key": row["default_zplanner_project_key"],
        "enabled": bool(row["enabled"]),
        "last_verified_at": row["last_verified_at"],
        "last_verify_error": row["last_verify_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_work_repos(viewer_id: int, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM work_repos WHERE viewer_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY name"
    return [_work_repo_row(r) for r in db.query_all(sql, (viewer_id,))]


def get_work_repo(viewer_id: int, repo_id: int) -> Optional[Dict[str, Any]]:
    row = db.query_one(
        "SELECT * FROM work_repos WHERE viewer_id = ? AND id = ?", (viewer_id, repo_id)
    )
    return _work_repo_row(row) if row else None


def find_work_repo_by_name(viewer_id: int, name: str) -> Optional[Dict[str, Any]]:
    """按名稱找。理由同 `get_reply_prompt_by_name`：`UNIQUE(viewer_id, name)`
    會擋住重複，但那會變成 sqlite3.IntegrityError（500）。先查一次才能回
    一句使用者看得懂的 400。"""
    row = db.query_one(
        "SELECT * FROM work_repos WHERE viewer_id = ? AND name = ?",
        (viewer_id, (name or "").strip()),
    )
    return _work_repo_row(row) if row else None


def find_work_repo_by_path(viewer_id: int, repo_path: str) -> Optional[Dict[str, Any]]:
    """按路徑找。存在的理由同上，對應的是 `UNIQUE(viewer_id, repo_path)`。

    路徑重複比名稱重複嚴重：同一個 repo 登錄兩次，同一批 commit 會被算兩遍，
    直接讓時數分配的權重失真。所以這個檢查不是為了美觀，是為了算對工時。
    """
    row = db.query_one(
        "SELECT * FROM work_repos WHERE viewer_id = ? AND repo_path = ?",
        (viewer_id, (repo_path or "").strip()),
    )
    return _work_repo_row(row) if row else None


def create_work_repo(
    viewer_id: int,
    *,
    name: str,
    repo_path: str,
    author_emails: Optional[List[str]] = None,
    branch_filter: Optional[List[str]] = None,
    default_zplanner_project_id: Optional[int] = None,
    default_zplanner_project_key: Optional[str] = None,
) -> int:
    """建立一筆工作 repo 登錄，回傳 id。

    `branch_filter` 給 `None` 或空 list 都存成 `[]`，而 `[]` 的意思是
    **掃全部分支**（不是「不掃」）——見 core/db.py 的欄位註解。
    """
    now = _now()
    cur = db.execute(
        """
        INSERT INTO work_repos(viewer_id, name, repo_path, author_emails, branch_filter,
                               default_zplanner_project_id, default_zplanner_project_key,
                               created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            viewer_id,
            name.strip(),
            repo_path.strip(),
            json.dumps(author_emails or []),
            json.dumps(branch_filter or []),
            default_zplanner_project_id,
            default_zplanner_project_key,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_work_repo(
    viewer_id: int,
    repo_id: int,
    *,
    # --- NOT NULL 欄位：`None` ＝ 不改（同 update_code_project 的慣例） ---
    name: Optional[str] = None,
    repo_path: Optional[str] = None,
    author_emails: Optional[List[str]] = None,
    branch_filter: Optional[List[str]] = None,
    enabled: Optional[bool] = None,
    # --- 可為 NULL 的欄位：沒傳 ＝ 不改，傳 `None` ＝ 清除（見 `_Unset`） ---
    default_zplanner_project_id: Any = UNSET,
    default_zplanner_project_key: Any = UNSET,
) -> Optional[Dict[str, Any]]:
    """部分更新。找不到（或不是這位 Viewer 的）回 None，由呼叫端決定怎麼報錯。

    兩種哨兵慣例並存的理由與 `update_preferences` 相同：`default_zplanner_*`
    的 `None` 有實際語意（「這個 repo 沒有預設的 ZPlanner 專案」是使用者會
    主動選的狀態，必須存得下去），其餘欄位是 NOT NULL，`None` 不可能是要存的值。

    `author_emails` 與 `branch_filter` 是**整組取代**，不是逐項合併——理由同
    code_projects 的 branches：部分更新會讓「把某個舊信箱拿掉」這個動作
    沒有辦法表達。
    """
    current = get_work_repo(viewer_id, repo_id)
    if current is None:
        return None
    db.execute(
        """
        UPDATE work_repos
           SET name = ?, repo_path = ?, author_emails = ?, branch_filter = ?,
               default_zplanner_project_id = ?, default_zplanner_project_key = ?,
               enabled = ?, updated_at = ?
         WHERE viewer_id = ? AND id = ?
        """,
        (
            current["name"] if name is None else name.strip(),
            current["repo_path"] if repo_path is None else repo_path.strip(),
            json.dumps(
                current["author_emails"] if author_emails is None else author_emails
            ),
            json.dumps(
                current["branch_filter"] if branch_filter is None else branch_filter
            ),
            _pick(
                default_zplanner_project_id, current["default_zplanner_project_id"]
            ),
            _pick(
                default_zplanner_project_key, current["default_zplanner_project_key"]
            ),
            int(current["enabled"] if enabled is None else enabled),
            _now(),
            viewer_id,
            repo_id,
        ),
    )
    return get_work_repo(viewer_id, repo_id)


def delete_work_repo(viewer_id: int, repo_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM work_repos WHERE viewer_id = ? AND id = ?", (viewer_id, repo_id)
    )
    return cur.rowcount > 0


def record_work_repo_verification(
    viewer_id: int, repo_id: int, error: Optional[str]
) -> None:
    """記錄驗證結果，供設定頁顯示「這個路徑已經不見了」。

    與 `record_project_verification` 同一個做法，但刻意分成兩個函式而不是
    共用一個帶 table 名稱的泛用版：那會是第一個能用字串決定要寫哪張表的
    函式，而本模組的 SQL 一律是寫死的字面值。
    """
    db.execute(
        """
        UPDATE work_repos SET last_verified_at = ?, last_verify_error = ?
         WHERE viewer_id = ? AND id = ?
        """,
        (_now(), error, viewer_id, repo_id),
    )


# --------------------------------------------------------------------------
# 工時草稿（ADR-0008）
#
# 同一條紀律：`viewer_id` 必填且擺第一，沒有「查全部」的入口。
# `worklog_draft_entries` 自己沒有 viewer 欄位（與 `draft_replies` 同型），
# 所以每一個碰 entries 的函式都**透過 draft 反查 viewer**——少一次就等於
# 讓任何人帶著猜到的 draft_id 改別人的工時。
#
# 兩個 JSON 欄位的慣例與 work_repos 的 `author_emails` 相同：DB 裡是 TEXT，
# 在這一層解開成真的 list／dict，呼叫端不必知道它曾經是字串。
# --------------------------------------------------------------------------


def _json_list(raw: Optional[str]) -> List[Any]:
    """把 JSON 欄位解成 list。**壞掉的內容不讓整份草稿讀不回來**。

    這些欄位是證據與候選，它們壞掉時使用者該看到的是「證據少了」，
    不是一個開不了的草稿——與 `get_stored_draft` 對 `generation_config_json`
    的處置同一個判準。
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return list(value) if isinstance(value, list) else []


def _json_obj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _hours_sum(values: Any) -> str:
    """把一串時數字串加總成 `"8.00"`。

    時數在 DB 裡是 TEXT（ZPlanner 要字串），加總在 Python 做而不是交給
    SQL 的 `SUM()`：後者會對 TEXT 做隱式數值轉換，遇到 NULL 或空字串時
    靜默當 0，而我們要的是「算不動的值要看得出來」——所以壞值一律跳過
    但**不**讓整個加總失敗。
    """
    total = 0.0
    for raw in values:
        if raw is None or str(raw).strip() == "":
            continue
        try:
            total += float(raw)
        except (TypeError, ValueError):
            continue
    return f"{total:.2f}"


def _worklog_entry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "draft_id": row["draft_id"],
        "seq": row["seq"],
        # 產生時一律是 None——系統只給候選，選擇權在 Viewer（ADR-0008 的
        # 唯一硬規則）。有值代表人已經選過了。
        "issue_id": row["issue_id"],
        "issue_key": row["issue_key"],
        "project_id": row["project_id"],
        "project_key": row["project_key"],
        "hours": row["hours"],
        "note": row["note"],
        "allocated_hours": row["allocated_hours"],
        "weight": row["weight"],
        "rounded_up": bool(row["rounded_up"]),
        "evidence_refs": _json_list(row["evidence_json"]),
        "candidates": _json_list(row["candidates_json"]),
        "state": row["state"],
        "zplanner_worklog_id": row["zplanner_worklog_id"],
        "zplanner_issue_key": row["zplanner_issue_key"],
        "is_locked": bool(row["is_locked"]),
        "submitted_at": row["submitted_at"],
        "last_error_code": row["last_error_code"],
        "last_error_message": row["last_error_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _worklog_draft_row(
    row: Dict[str, Any], entries: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    out = {
        "id": row["id"],
        "work_date": row["work_date"],
        "status": row["status"],
        # Viewer 輸入的總時數。**與各筆之和不保證相等**（湊 0.5 倍數必然有
        # 餘數），兩個數字都要留著，確認畫面才講得出「你輸入 8.0，實際會
        # 送出 7.5」。
        "total_hours": row["total_hours"],
        "tz_offset": row["tz_offset"],
        "generation_meta": _json_obj(row["generation_meta_json"]),
        "generated_at": row["generated_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if entries is not None:
        out["entries"] = entries
    return out


def _entries_for(draft_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """一次撈完所有 entries，避免 N+1（做法同 `_branches_for`）。"""
    if not draft_ids:
        return {}
    marks = ",".join("?" * len(draft_ids))
    rows = db.query_all(
        f"SELECT * FROM worklog_draft_entries WHERE draft_id IN ({marks}) "
        f"ORDER BY draft_id, seq",
        tuple(draft_ids),
    )
    out: Dict[int, List[Dict[str, Any]]] = {did: [] for did in draft_ids}
    for row in rows:
        out[row["draft_id"]].append(_worklog_entry_row(row))
    return out


def get_worklog_draft(
    viewer_id: int, work_date: str, *, with_entries: bool = True
) -> Optional[Dict[str, Any]]:
    """取某一天的草稿。沒有就回 None——**那是正常狀態，不是錯誤**
    （比照 `DRAFT_NOT_FOUND` 的判準，由呼叫端決定要不要報 404）。"""
    row = db.query_one(
        "SELECT * FROM worklog_drafts WHERE viewer_id = ? AND work_date = ?",
        (viewer_id, (work_date or "").strip()),
    )
    if row is None:
        return None
    entries = _entries_for([row["id"]])[row["id"]] if with_entries else None
    return _worklog_draft_row(row, entries)


def get_worklog_draft_by_id(
    viewer_id: int, draft_id: int, *, with_entries: bool = True
) -> Optional[Dict[str, Any]]:
    """用 id 取，但**仍然要帶 viewer_id**：id 是前端傳回來的。"""
    row = db.query_one(
        "SELECT * FROM worklog_drafts WHERE viewer_id = ? AND id = ?",
        (viewer_id, draft_id),
    )
    if row is None:
        return None
    entries = _entries_for([row["id"]])[row["id"]] if with_entries else None
    return _worklog_draft_row(row, entries)


def upsert_worklog_draft(
    viewer_id: int,
    work_date: str,
    *,
    tz_offset: str,
    # --- 沒傳 ＝ 不改（新建時用預設值），傳 `None` ＝ 清除（見 `_Unset`）---
    status: Any = UNSET,
    total_hours: Any = UNSET,
    generation_meta: Any = UNSET,
    generated_at: Any = UNSET,
) -> Dict[str, Any]:
    """建立或更新某一天的草稿，回傳完整的草稿（含 entries）。

    **一天一份**（`UNIQUE(viewer_id, work_date)`）：同一天產第二次是覆蓋，
    不是並存——兩份同時存在的話，「這天到底送了哪一份」就沒有答案了。

    用 `INSERT … ON CONFLICT DO UPDATE` 而不是「先查再寫」：後者在同一位
    Viewer 同時開兩個分頁按產生時會撞 `UNIQUE` 變成 500。要沿用的舊值在
    進 SQL 之前就算好，所以 UPSERT 的 SET 子句可以一律用 `excluded.*`。

    `total_hours` 的 `None` 有實際語意（**還沒決定總時數**，此時各筆的
    `hours` 應該是 null），所以這裡用 UNSET 哨兵而不是「None ＝ 不改」。
    """
    date = (work_date or "").strip()
    current = get_worklog_draft(viewer_id, date, with_entries=False)
    now = _now()

    new_status = _pick(status, current["status"] if current else "draft")
    new_total = _pick(total_hours, current["total_hours"] if current else None)
    new_meta = _pick(
        generation_meta, current["generation_meta"] if current else {}
    )
    new_generated = _pick(generated_at, current["generated_at"] if current else None)

    db.execute(
        """
        INSERT INTO worklog_drafts(viewer_id, work_date, status, total_hours, tz_offset,
                                   generation_meta_json, generated_at, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(viewer_id, work_date) DO UPDATE SET
            status               = excluded.status,
            total_hours          = excluded.total_hours,
            tz_offset            = excluded.tz_offset,
            generation_meta_json = excluded.generation_meta_json,
            generated_at         = excluded.generated_at,
            updated_at           = excluded.updated_at
        """,
        (
            viewer_id,
            date,
            new_status,
            None if new_total is None else str(new_total),
            tz_offset,
            json.dumps(new_meta or {}, ensure_ascii=False),
            new_generated,
            current["created_at"] if current else now,
            now,
        ),
    )
    result = get_worklog_draft(viewer_id, date)
    assert result is not None
    return result


def replace_worklog_entries(
    viewer_id: int,
    draft_id: int,
    entries: List[Dict[str, Any]],
    *,
    refuse_submitted: bool = False,
) -> Optional[Dict[str, Any]]:
    """**整組取代**這份草稿的 entries，回傳更新後的草稿；不是這位 Viewer 的回 None。

    整組取代而不是逐筆 merge，比照 `code_projects` 的 branches：部分更新會讓
    「把某一筆刪掉」這個動作沒有辦法表達。`seq` 由這裡按清單順序重新編號，
    呼叫端不必自己維護——它唯一的用途是「畫面上的第幾張卡」。

    刪除與新增在**同一個 transaction**，理由同 `create_code_project`：
    中途失敗會留下一份沒有任何 entries 的草稿，而那看起來就像「這天沒事做」。

    ⚠️ `refuse_submitted=True` 時，**已經有任何一筆帶著 `zplanner_worklog_id`
    就整筆拒絕**（拋 `WorklogAlreadySubmitted`，交易回捲，一行都不動）。

    本模組的函式一律不拋業務錯誤——**例外只有兩個，而且都是刻意的**：這支，與
    `update_worklog_entries`（逐筆已送出守門與「id 不屬於此草稿」，理由相同：
    守門要跟寫入在同一個交易裡才擋得住）。呼叫端
    自己在外面先查一次是不夠的：草稿產生要掃 git、要打 ZPlanner，守門與真正
    寫入之間隔得掉數十秒，那段時間另一條路徑把工時送出去，這裡的 DELETE 就會
    把剛拿到的 `zplanner_worklog_id` **安靜地抹掉**——正好是守門要防的事。
    檢查必須跟 DELETE 在同一個交易裡才擋得住。

    順序也是刻意的：**先做 `UPDATE worklog_drafts`** 把交易升級成寫入交易
    （SQLite 的 deferred transaction 要等到第一個寫入語句才真的開始；純 SELECT
    不會拿到 writer lock），拿到鎖之後再查 `zplanner_worklog_id`。倒過來寫的話
    查與刪之間仍然有一條縫，等於把幾十秒的競態縮小成幾微秒的競態——**還是
    競態**。
    """
    draft = get_worklog_draft_by_id(viewer_id, draft_id, with_entries=False)
    if draft is None:
        return None

    now = _now()
    conn = db.get_connection()
    with conn:
        conn.execute(
            "UPDATE worklog_drafts SET updated_at = ? WHERE id = ? AND viewer_id = ?",
            (now, draft_id, viewer_id),
        )
        if refuse_submitted:
            row = conn.execute(
                "SELECT COUNT(*) FROM worklog_draft_entries "
                "WHERE draft_id = ? AND zplanner_worklog_id IS NOT NULL",
                (draft_id,),
            ).fetchone()
            sent = int(row[0])
            if sent:
                raise WorklogAlreadySubmitted(
                    f"這份草稿已經有 {sent} 筆工時送進 ZPlanner 了，不能整組取代。"
                    "取代會弄丟那幾筆的 worklog id，之後只能走 correction-request。",
                    detail={"submitted_count": sent, "force_allowed": False},
                )
        conn.execute("DELETE FROM worklog_draft_entries WHERE draft_id = ?", (draft_id,))
        for seq, entry in enumerate(entries or [], start=1):
            conn.execute(
                """
                INSERT INTO worklog_draft_entries(
                    draft_id, seq, issue_id, issue_key, project_id, project_key,
                    hours, note, allocated_hours, weight, rounded_up,
                    evidence_json, candidates_json, state,
                    zplanner_worklog_id, zplanner_issue_key, is_locked,
                    submitted_at, last_error_code, last_error_message,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    seq,
                    entry.get("issue_id"),
                    entry.get("issue_key"),
                    entry.get("project_id"),
                    entry.get("project_key"),
                    None if entry.get("hours") is None else str(entry["hours"]),
                    entry.get("note"),
                    None
                    if entry.get("allocated_hours") is None
                    else str(entry["allocated_hours"]),
                    entry.get("weight"),
                    int(bool(entry.get("rounded_up"))),
                    json.dumps(entry.get("evidence_refs") or [], ensure_ascii=False),
                    json.dumps(entry.get("candidates") or [], ensure_ascii=False),
                    entry.get("state") or "pending",
                    entry.get("zplanner_worklog_id"),
                    entry.get("zplanner_issue_key"),
                    int(bool(entry.get("is_locked"))),
                    entry.get("submitted_at"),
                    entry.get("last_error_code"),
                    entry.get("last_error_message"),
                    now,
                    now,
                ),
            )
        # `updated_at` 已經在交易最前面寫過了（見 docstring：那一步同時是
        # 「拿 writer lock」）。這裡不再寫第二次。
    return get_worklog_draft_by_id(viewer_id, draft_id)


#: `update_worklog_entries` 准許改寫的欄位——**只有 Viewer 自己決定的那幾個**
#: （D-2）。state／zplanner_worklog_id／is_locked／last_error_*／submitted_at
#: 是送出流程寫的，allocated_hours／weight／evidence／candidates 是產生流程寫的；
#: 讓 PUT 碰得到它們，一次前端 bug 就能把「這筆已經送過」的紀錄抹掉。
_WORKLOG_EDITABLE_FIELDS = (
    "hours",
    "note",
    "issue_id",
    "issue_key",
    "project_id",
    "project_key",
)


def update_worklog_entries(
    viewer_id: int, work_date: str, updates: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """逐筆改寫某一天草稿裡的可編輯欄位，回傳更新後的整份草稿；沒有草稿回 None。

    `updates` 每一項是 `{"id": <entry id>, <欄位>: <新值>, ...}`。**鍵沒出現 ＝
    不改，出現且值為 `None` ＝ 清除**（沿用本檔 `UNSET` 哨兵的語意：清掉專案
    歸屬是 Viewer 會主動做的事，必須存得下去）。可改的欄位只有
    `_WORKLOG_EDITABLE_FIELDS`，出現別的鍵直接拋 `ValueError`——那是呼叫端的
    程式錯誤，靜默略過的話「我以為改了 state」會變成查不出來的 bug。

    🔴 **整批一個交易，全有或全無（D-11／AC-7）**，與旁邊
    `update_worklog_entry_state` 的「一筆一交易」刻意相反：那支寫的是 ZPlanner
    已經收下的結果，回捲會抹掉遠端 id；這支寫的是 Viewer 一次按下的「儲存」，
    存一半的話畫面上改了三筆、實際只落地兩筆，而使用者沒有任何方法知道是哪兩筆。

    交易內的順序是刻意的：

    1. **先 `UPDATE worklog_drafts` 拿 writer lock**（理由同
       `replace_worklog_entries`：SQLite 的 deferred transaction 在第一個寫入
       語句前不持鎖，先查後鎖會留一條縫讓送出流程在中間寫入遠端 id）。
    2. **要改的筆中有已送出者 → 拋 `WorklogAlreadySubmitted`**（D-21 逐筆）。
       判準是「這次要改的筆」，**不是**「整份草稿有沒有任何一筆送過」：後者
       （D-3，已被 D-21 取代）會讓部分送出的草稿無路可修——選錯專案而 failed
       的那筆改不了、重產也被擋，只能拿錯的值重送。`failed`／`unknown` 沒有
       遠端 id，所以可改；有遠端 id 的筆改本地值不會同步到 ZPlanner，本地與
       遠端從此對不起來，那只能走 correction-request。
    3. **有不屬於此草稿的 id → 拋 `InvalidParameter`**，`detail` 鍵名與送出
       端點的 `_worklog_submit_targets` 相同（`unknown_entry_ids`／
       `draft_entry_ids`），前端用同一段程式碼處理兩種 400。
    4. **逐筆 `UPDATE`，`WHERE` 仍帶 `zplanner_worklog_id IS NULL`，`rowcount≠1`
       就拋 `RuntimeError` 回捲整批。** 在 writer lock 底下、第 2、3 步也過了，
       照理不會發生；真的發生代表有什麼繞過了這把鎖（例如另一條沒有先搶鎖的
       寫入路徑），那是 bug，要以 500 炸出來，不能回成功。

    PUT 不能新增或刪除 entry（D-4）：entry 只能由產生流程依證據造出來。
    時數格式**不在這裡驗**（D-13）：驗證規則在 `zplanner_client.require_hours`，
    存取層依賴會發 HTTP 的模組違反 core 內部界線，所以由 server 先驗好再傳進來。
    """
    draft = get_worklog_draft(viewer_id, work_date, with_entries=False)
    if draft is None:
        return None
    draft_id = draft["id"]

    for item in updates or []:
        stray = set(item) - {"id", *_WORKLOG_EDITABLE_FIELDS}
        if stray:
            raise ValueError(
                f"update_worklog_entries 不接受欄位 {sorted(stray)}；只能改 "
                f"{list(_WORKLOG_EDITABLE_FIELDS)}（D-2）。"
            )

    now = _now()
    conn = db.get_connection()
    with conn:
        # 1. writer lock（見 docstring 第 1 點；這一句同時是草稿的 updated_at）
        conn.execute(
            "UPDATE worklog_drafts SET updated_at = ? WHERE id = ? AND viewer_id = ?",
            (now, draft_id, viewer_id),
        )
        remote_by_id = {
            int(row[0]): row[1]
            for row in conn.execute(
                "SELECT id, zplanner_worklog_id FROM worklog_draft_entries "
                "WHERE draft_id = ?",
                (draft_id,),
            ).fetchall()
        }
        wanted = [item["id"] for item in updates or []]

        # 2. D-21：只看這次要改的筆
        sent = sum(
            1
            for entry_id in wanted
            if entry_id in remote_by_id and remote_by_id[entry_id] is not None
        )
        if sent:
            raise WorklogAlreadySubmitted(
                f"要改的筆中有 {sent} 筆已經送進 ZPlanner 了，這次一筆都沒有改。"
                "改本地的值不會同步到 ZPlanner，兩邊從此對不起來；已送出的工時"
                "要改只能走 correction-request。",
                detail={"submitted_count": sent, "force_allowed": False},
            )

        # 3. 不屬於這份草稿的 id
        missing = [entry_id for entry_id in wanted if entry_id not in remote_by_id]
        if missing:
            raise InvalidParameter(
                f"entries 裡的 {', '.join(str(i) for i in missing)} 不屬於 "
                f"{draft['work_date']} 的草稿，這次一筆都沒有改。",
                detail={
                    "unknown_entry_ids": missing,
                    "draft_entry_ids": sorted(remote_by_id),
                },
            )

        # 4. 逐筆寫入，只 SET 有帶的欄位
        for item in updates or []:
            sets: List[str] = []
            params: List[Any] = []
            for field in _WORKLOG_EDITABLE_FIELDS:
                value = item.get(field, UNSET)
                if isinstance(value, _Unset):
                    continue
                if field == "hours" and value is not None:
                    value = str(value)  # DB 是 TEXT，理由見 `_hours_sum`
                sets.append(f"{field} = ?")
                params.append(value)
            sets.append("updated_at = ?")
            params.append(now)
            cur = conn.execute(
                f"UPDATE worklog_draft_entries SET {', '.join(sets)} "
                "WHERE id = ? AND draft_id = ? AND zplanner_worklog_id IS NULL",
                (*params, item["id"], draft_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"entry {item['id']} 在 writer lock 底下仍然寫不進去"
                    f"（rowcount={cur.rowcount}），整批回捲。前面的守門都過了卻"
                    "寫不進去，代表有別的寫入路徑沒有先搶鎖——這是 bug，不是使用者錯誤。"
                )
    return get_worklog_draft(viewer_id, work_date)


def update_worklog_entry_state(
    viewer_id: int,
    draft_id: int,
    entry_id: int,
    *,
    state: str,
    zplanner_worklog_id: Optional[int] = None,
    zplanner_issue_key: Optional[str] = None,
    is_locked: Optional[bool] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """把**一筆** entry 的送出結果寫回去。回 `True` ＝ 影響 1 列，`False` ＝ 0 列。

    🔴 **一筆一個交易，刻意不沿用 `replace_worklog_entries` 的整批
    all-or-nothing。** 那支的回捲是為了「不留下一份沒有 entries 的草稿」；這裡
    回捲會把**已經寫進 ZPlanner 的 worklog id 一起抹掉**——ZPlanner 那邊的工時
    還在，本地卻沒有任何人認領它，而認領不到就沒有修改與刪除的入口（月結後
    只能走 correction-request）。所以每成功一筆就立刻落地，不等整批跑完。

    **三個守門條件併進同一句 `UPDATE` 的 `WHERE`**，不先 SELECT 再 UPDATE：

    1. `id`／`draft_id` 相符——id 是前端傳回來的；
    2. 這份草稿屬於這位 Viewer（用 `EXISTS` 繞回 `worklog_drafts`，
       entries 表本身沒有 viewer_id 欄位）；
    3. `zplanner_worklog_id IS NULL`——已經認領過遠端工時的筆不准被改寫。

    查了再寫的話，查與寫之間那條縫剛好是「另一條路徑把同一份草稿重產掉」的
    窗口，而那正是守門要防的事。單句 `UPDATE` 本身即原子，不必像
    `replace_worklog_entries` 那樣先搶 writer lock。

    ⚠️ **`False` 是呼叫端必須處理的結果，不是「沒事發生」。** 走到這裡代表
    ZPlanner 那筆很可能已經建好了，卻沒能記回任何一筆草稿 ＝ 孤兒工時，
    要當成需要人工對帳回報出去。四種成因都回 `False`，因為對使用者來說要做的
    事情一樣（去 ZPlanner 對帳）：這筆在送出期間被重產清掉、id 指到別份草稿、
    不是這位 Viewer 的、或這個遠端 id 已經被別筆認領（撞 `idx_worklog_entry_remote`
    這道 partial unique index——它是重複送出的最後一道防線，見 `core/db.py`）。
    最後那種會拋 `sqlite3.IntegrityError`，這裡接住轉成 `False`：讓它往上炸會
    中斷整個逐筆迴圈，於是**後面每一筆都不會被送出，前面剛建好的那筆也沒人
    記錄**——一個約束衝突換來一整天的工時失聯。

    各欄位「沒給」的語意刻意不一致，因為它們本來就不是同一種東西
    （所以也沒有套用本檔上面 `upsert_worklog_draft` 的 `UNSET` 哨兵）：

    * `error_code`／`error_message` 直接寫入，`None` 就是**清掉**。重打成功之後
      舊的錯誤訊息必須消失，否則畫面上會同時看到 `submitted` 與上一輪的錯誤，
      沒有人分得出那是歷史還是現況。
    * `is_locked` 用 `COALESCE`，`None` ＝ **不動**。它只有 `create_worklog` 的
      回應講得出來（`is_locked` 是 ZPlanner 的月結鎖），失敗那幾筆我們沒有這個
      資訊，寫成 0 等於謊稱「查過了，沒鎖」。
    * `submitted_at` 只在 `state='submitted'` 時寫入。被去重跳過的筆是
      `skipped`，**那筆工時不是我們送的**，給它一個送出時間會讓對帳的人以為是。
    """
    now = _now()
    params = (
        state,
        zplanner_worklog_id,
        zplanner_issue_key,
        None if is_locked is None else int(bool(is_locked)),
        now if state == "submitted" else None,
        error_code,
        error_message,
        now,
        entry_id,
        draft_id,
        viewer_id,
    )
    try:
        cur = db.execute(
            """
            UPDATE worklog_draft_entries
               SET state               = ?,
                   zplanner_worklog_id = ?,
                   zplanner_issue_key  = ?,
                   is_locked           = COALESCE(?, is_locked),
                   submitted_at        = COALESCE(?, submitted_at),
                   last_error_code     = ?,
                   last_error_message  = ?,
                   updated_at          = ?
             WHERE id = ?
               AND draft_id = ?
               AND zplanner_worklog_id IS NULL
               AND EXISTS (
                     SELECT 1 FROM worklog_drafts
                      WHERE worklog_drafts.id = worklog_draft_entries.draft_id
                        AND worklog_drafts.viewer_id = ?
                   )
            """,
            params,
        )
    except sqlite3.IntegrityError:
        return False
    return cur.rowcount == 1


def list_worklog_drafts_in_range(
    viewer_id: int, start_date: str, end_date: str
) -> List[Dict[str, Any]]:
    """週檢視用的摘要：這段日期內每一天的草稿狀態與時數合計。

    **刻意不回 entries 本體。** 一週七天、每天可能好幾筆，每筆都帶著證據 ref
    與候選清單——週檢視只要畫「有沒有草稿、幾小時、選完專案了沒」，整包帶
    過去只是讓那個端點的回應大上一個量級。要細節就去打單日的端點。

    日期是 `YYYY-MM-DD` 字串，字典序比較等同日期比較（固定寬度、零補齊），
    所以這裡直接用 `BETWEEN` 而不先轉成 date 物件。
    """
    rows = db.query_all(
        """
        SELECT * FROM worklog_drafts
         WHERE viewer_id = ? AND work_date >= ? AND work_date <= ?
         ORDER BY work_date
        """,
        (viewer_id, (start_date or "").strip(), (end_date or "").strip()),
    )
    entries_by_draft = _entries_for([r["id"] for r in rows])

    out: List[Dict[str, Any]] = []
    for row in rows:
        entries = entries_by_draft.get(row["id"], [])
        summary = _worklog_draft_row(row)
        summary.update(
            {
                "entry_count": len(entries),
                # 真正會送出的是各筆之和，不是 total_hours（見欄位註解）
                "hours": _hours_sum(e["hours"] for e in entries),
                "allocated_hours": _hours_sum(e["allocated_hours"] for e in entries),
                "submitted_count": sum(1 for e in entries if e["state"] == "submitted"),
                "failed_count": sum(1 for e in entries if e["state"] == "failed"),
                "unknown_count": sum(1 for e in entries if e["state"] == "unknown"),
                # 還沒選專案的筆數。**未選擋住送出**（計畫 §7 第 1 條），
                # 週檢視要看得出「這天還差幾筆沒決定」。
                "unassigned_count": sum(1 for e in entries if not e["issue_id"]),
            }
        )
        out.append(summary)
    return out


def delete_worklog_draft(viewer_id: int, work_date: str) -> bool:
    """刪掉某一天的草稿（entries 靠外鍵 CASCADE 跟著走）。"""
    cur = db.execute(
        "DELETE FROM worklog_drafts WHERE viewer_id = ? AND work_date = ?",
        (viewer_id, (work_date or "").strip()),
    )
    return cur.rowcount > 0
