"""使用者名錄：把 `users/{id}` 反查成看得懂的名字。

**為什麼需要這個模組**

Google Chat 的 `User` 資源在**使用者驗證**下只回傳 `name` 與 `type`，
`displayName` 一律是空的（官方文件明載，2026-09-05 實測確認：
`spaces.messages.list` 回的每個 `sender` 都只有 `{"name": "users/…", "type": "HUMAN"}`）。
因此舊實作的 `sender.get("displayName", "未知成員")` **永遠**取到「未知成員」，
送進 Gemini 的對話長得像：

    [09-04 10:56] 未知成員: 如果SDP是指程式的框架，那不用
    [09-04 09:43] 未知成員: 請問SDP升版我們是需要到中心現場處理嗎

發言者全部同名，模型無法區分誰說了什麼，摘要與 Draft Reply 的品質都被拖垮。
這是規格書十一節之外新發現的缺陷（D-7）。

**解法（不需要任何新 scope）**

`USER_MENTION` annotation 同時給出 `userMention.user.name` 與該提及在文字中的
`startIndex`／`length`，而那段文字正是 `@王小明`。所以每一則「有人被 @」的訊息
都是一筆 id → 名字的對照。把它累積進 SQLite，名錄會隨使用逐漸長齊。

覆蓋不到的 id（從未被 @ 過的人）退回 `成員…8641` 這種短代號——它至少是**穩定且
可區分**的，模型能靠它分辨「這兩句是同一個人說的」，比全部叫「未知成員」好得多。
"""

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import db

_MENTION_PREFIX = re.compile(r"^[@＠]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def learn_from_messages(messages: Iterable[Dict[str, Any]]) -> int:
    """從訊息的 USER_MENTION annotation 反建名錄，回傳新學到的筆數。"""
    learned: Dict[str, str] = {}

    for msg in messages:
        text = msg.get("text") or ""
        for ann in msg.get("annotations") or []:
            if ann.get("type") != "USER_MENTION":
                continue
            um = ann.get("userMention") or {}
            user = um.get("user") or {}
            uid = user.get("name")
            if not uid:
                continue
            # annotation 自己帶 displayName 的話直接用（未來 API 若補上就會生效）
            if user.get("displayName"):
                learned[uid] = user["displayName"]
                continue
            try:
                start = int(ann.get("startIndex", -1))
                length = int(ann.get("length", 0))
            except (TypeError, ValueError):
                continue
            if start < 0 or length <= 0 or start + length > len(text):
                continue
            fragment = text[start : start + length]
            name = _MENTION_PREFIX.sub("", fragment).strip()
            if name:
                learned[uid] = name

    if not learned:
        return 0

    conn = db.get_connection()
    with conn:
        for uid, name in learned.items():
            conn.execute(
                """
                INSERT INTO user_directory(user_id, display_name, source, updated_at)
                VALUES(?, ?, 'mention_annotation', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    updated_at   = excluded.updated_at
                """,
                (uid, name, _now()),
            )
    return len(learned)


def remember(user_id: str, display_name: str, source: str = "identity") -> None:
    """明確登記一筆（例如登入時解析出的自己）。"""
    if not user_id or not display_name:
        return
    db.execute(
        """
        INSERT INTO user_directory(user_id, display_name, source, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            display_name = excluded.display_name,
            source       = excluded.source,
            updated_at   = excluded.updated_at
        """,
        (user_id, display_name, source, _now()),
    )


def load_all() -> Dict[str, str]:
    return {
        r["user_id"]: r["display_name"]
        for r in db.query_all("SELECT user_id, display_name FROM user_directory")
    }


def short_code(user_id: str) -> str:
    """名錄查不到時的穩定代號。用 id 末四碼，足以區分不同的人。"""
    if not user_id:
        return "未知成員"
    tail = user_id.rsplit("/", 1)[-1][-4:]
    return f"成員…{tail}"


def make_resolver(
    self_user_id: Optional[str] = None, self_label: str = "我"
) -> Callable[[Optional[str]], str]:
    """回傳一個 user_id -> 名字 的解析函式（一次載入名錄，避免逐筆查 DB）。

    self_user_id 有值時，該 id 會標成 `self_label`（預設「我」）——
    讓模型清楚知道哪些話是使用者本人說的，這對 Draft Reply 特別重要。
    """
    table = load_all()

    def resolve(user_id: Optional[str]) -> str:
        if not user_id:
            return "未知成員"
        if self_user_id and user_id == self_user_id:
            known = table.get(user_id)
            return f"{self_label}（{known}）" if known else self_label
        return table.get(user_id) or short_code(user_id)

    return resolve


def safe_resolver(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    self_user_id: Optional[str] = None,
) -> Callable[[Optional[str]], str]:
    """給 MCP／CLI 用的容錯入口：先從這批訊息學名字，再回傳解析器。

    這兩個入口不像儀表板那樣保證資料庫已初始化（可能是 `uv run` 起來的臨時環境），
    所以資料庫層任何失敗都吞掉、退回只用 id 末四碼的代號——名字查不到是可以接受的
    降級，讓摘要整個掛掉不是。
    """
    try:
        db.init_db()
        if messages:
            learn_from_messages(messages)
        return make_resolver(self_user_id)
    except Exception:
        return lambda uid: short_code(uid) if uid else "未知成員"


def stats() -> Dict[str, int]:
    row = db.query_one("SELECT COUNT(*) AS n FROM user_directory")
    return {"known_users": row["n"] if row else 0}


def describe(user_ids: List[str]) -> Dict[str, str]:
    resolver = make_resolver()
    return {uid: resolver(uid) for uid in user_ids}
