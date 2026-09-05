"""Google Chat REST API v1 封裝。

規格對應：SPECIFICATION.md 5.1、5.5、6.2、8.4、十一（D-1）。

改用 REST + AuthorizedSession（而非 googleapiclient 的 discovery build）的理由：
  1. `spaces.messages.search`（實作 A）與 `messages.list` 的 `filter` 參數需要直接控制
  2. 429 的指數退避重試要能包住每一次呼叫（8.4）
  3. 憑證要能由外部注入（Phase 2 多 Viewer，憑證來自 credentials 表而非固定檔案）

既有的四個公開方法（list_spaces／find_space_by_name／fetch_recent_messages／
send_message）簽章不變，mcp_app 與 summarizer 不需改動。
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config as cfg
from .errors import (
    ChatApiError,
    ChatRateLimited,
    InvalidParameter,
    classify_google_api_error,
)

API_BASE = "https://chat.googleapis.com/v1"

# search 端點只接受這兩種 orderBy（2026-09-05 實測，見 docs/R1-findings.md）
SEARCH_ORDER_BY = "createTime DESC"


def rfc3339(dt: datetime) -> str:
    """Google Chat filter 用的時間字串。實測 messages.list 接受帶或不帶小數秒。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_rfc3339(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_limit(limit: Optional[int]) -> int:
    """五節 5.5：四個入口共用同一組上下限，超出一律 INVALID_PARAMETER。"""
    if limit is None:
        return cfg.LIMIT_DEFAULT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise InvalidParameter(f"limit 必須是整數，收到 {limit!r}")
    if value < cfg.LIMIT_MIN or value > cfg.LIMIT_MAX:
        raise InvalidParameter(
            f"limit 必須在 {cfg.LIMIT_MIN}~{cfg.LIMIT_MAX} 之間，收到 {value}"
        )
    return value


def load_legacy_credentials(interactive: bool = True) -> Credentials:
    """讀取（必要時以互動流程建立）Phase 1 之前的單人 token。"""
    creds = None
    if os.path.exists(cfg.LEGACY_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(
            cfg.LEGACY_TOKEN_FILE, cfg.CHAT_SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive:
            flow = InstalledAppFlow.from_client_secrets_file(
                cfg.CLIENT_SECRET_FILE, cfg.CHAT_SCOPES
            )
            creds = flow.run_local_server(port=0)
        else:
            raise ChatApiError("找不到可用的 Google Chat 憑證，且未允許互動授權")
        with open(cfg.LEGACY_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        # google-auth 用預設 umask 寫檔（實測 0644），而這個檔含明文
        # refresh_token；寫完立刻收成 0600
        from . import crypto

        crypto.harden_file(cfg.LEGACY_TOKEN_FILE)
    return creds


class GoogleChatClient:
    """單一 Viewer 的 Google Chat 存取器。

    credentials 為 None 時沿用舊行為（讀 config/google_chat_token.json，
    必要時開瀏覽器授權），讓 mcp_app 與 CLI 不受影響。
    """

    def __init__(
        self,
        credentials: Optional[Credentials] = None,
        *,
        on_token_refresh: Optional[Callable[[Credentials], None]] = None,
        interactive: bool = True,
    ):
        self.credentials = credentials or load_legacy_credentials(interactive=interactive)
        self._on_token_refresh = on_token_refresh
        self._session = AuthorizedSession(self.credentials)
        self._token_at_start = self.credentials.token

    # ------------------------------------------------------------------
    # 底層請求（含 429 指數退避）
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{API_BASE}/{path.lstrip('/')}"
        last_error: Optional[Exception] = None

        for attempt in range(cfg.CHAT_RETRY_MAX_ATTEMPTS):
            try:
                resp = self._session.request(
                    method, url, params=params, json=json_body, timeout=60
                )
            except requests.RequestException as exc:
                last_error = ChatApiError(f"連線 Google Chat 失敗：{exc}")
                if attempt < cfg.CHAT_RETRY_MAX_ATTEMPTS - 1:
                    time.sleep(cfg.CHAT_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise last_error from exc

            self._persist_refreshed_token()

            if resp.status_code == 200:
                return resp.json() if resp.content else {}

            # 8.4：Google Chat 回 429 時採指數退避重試，最多 3 次
            if resp.status_code == 429 and attempt < cfg.CHAT_RETRY_MAX_ATTEMPTS - 1:
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else cfg.CHAT_RETRY_BASE_DELAY * (2**attempt)
                )
                time.sleep(delay)
                continue

            err = classify_google_api_error(resp.status_code, resp.text)
            if isinstance(err, ChatRateLimited):
                ra = resp.headers.get("Retry-After")
                err.retry_after = int(ra) if ra and ra.isdigit() else 30
            raise err

        raise last_error or ChatApiError("Google Chat 請求重試次數已用盡")

    def _persist_refreshed_token(self) -> None:
        """AuthorizedSession 會就地刷新 token，刷新後要讓呼叫端有機會存回去。"""
        if self.credentials.token != self._token_at_start:
            self._token_at_start = self.credentials.token
            if self._on_token_refresh:
                self._on_token_refresh(self.credentials)

    # ------------------------------------------------------------------
    # Space
    # ------------------------------------------------------------------

    def list_spaces(self) -> List[Dict[str, Any]]:
        """列出所有 Space（自動翻頁；缺陷 D-1）。

        必須處理 nextPageToken。先前只取第一頁，導致 436 個 Space 中有 336 個
        永遠讀不到，且表現為「查無此群組」而非錯誤。
        回傳的每個 Space 都帶 lastActiveTime（實測 436/436 皆有），
        採集器靠它預篩活躍子集。
        """
        all_spaces: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        for _ in range(cfg.MAX_PAGES):
            params: Dict[str, Any] = {"pageSize": cfg.SPACES_PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", "spaces", params=params)
            all_spaces.extend(data.get("spaces", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return all_spaces

    def find_space_by_name(self, name_keyword: str) -> Optional[Dict[str, Any]]:
        """依名稱關鍵字尋找 Space（不分大小寫，取第一個命中）。"""
        keyword = (name_keyword or "").lower()
        for s in self.list_spaces():
            if keyword in (s.get("displayName") or "").lower():
                return s
        return None

    def get_space(self, space_id: str) -> Dict[str, Any]:
        return self._request("GET", space_id)

    # ------------------------------------------------------------------
    # 訊息
    # ------------------------------------------------------------------

    def fetch_recent_messages(
        self, space_id: str, limit: int = cfg.LIMIT_DEFAULT
    ) -> List[Dict[str, Any]]:
        """抓取指定 Space 最近 limit 則訊息，回傳由舊到新。"""
        limit = validate_limit(limit)
        collected: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        for _ in range(cfg.MAX_PAGES):
            remaining = limit - len(collected)
            if remaining <= 0:
                break
            params: Dict[str, Any] = {
                "pageSize": min(cfg.MESSAGES_PAGE_SIZE, remaining),
                # 由新到舊取，才能用 limit 截到「最近的 N 則」
                "orderBy": "createTime desc",
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{space_id}/messages", params=params)
            msgs = data.get("messages", [])
            if not msgs:
                break
            collected.extend(msgs)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        collected = collected[:limit]
        # Google 回傳由新到舊，轉成由舊到新方便閱讀脈絡
        collected.reverse()
        return collected

    def list_messages_since(
        self, space_id: str, since: datetime, page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """取回某時間點之後的新訊息（採集器實作 B 用）。

        實測 messages.list 的 filter 只接受 `createTime >`（`>=` 回 400），
        且不支援任何 mention 相關欄位——所以判定必須在本地做（6.2）。
        """
        collected: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        flt = f'createTime > "{rfc3339(since)}"'

        for _ in range(cfg.MAX_PAGES):
            params: Dict[str, Any] = {"pageSize": page_size, "filter": flt}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{space_id}/messages", params=params)
            collected.extend(data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return collected

    def get_message(self, message_name: str) -> Dict[str, Any]:
        """取回單一則訊息。mentions 只存識別資訊，顯示時即時取回內容（九節）。"""
        return self._request("GET", message_name)

    def list_thread_messages(
        self, space_id: str, thread_name: str, limit: int = cfg.LIMIT_MAX
    ) -> List[Dict[str, Any]]:
        """取回同一討論串的完整對話（7.2 步驟 2）。

        messages.list 的 filter 支援 thread.name，所以這是一次呼叫就能拿到的。
        """
        collected: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        flt = f'thread.name = "{thread_name}"'

        for _ in range(cfg.MAX_PAGES):
            params: Dict[str, Any] = {
                "pageSize": min(cfg.MESSAGES_PAGE_SIZE, limit),
                "filter": flt,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{space_id}/messages", params=params)
            collected.extend(data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token or len(collected) >= limit:
                break

        collected.sort(key=lambda m: m.get("createTime") or "")
        return collected[:limit]

    def search_mentions(
        self, google_user_id: str, page_size: int = 100, max_pages: int = 5
    ) -> List[Dict[str, Any]]:
        """實作 A：跨群搜尋 Mention（spaces/-/messages:search）。

        **此實作於 2026-09-05 實測不可用**：端點回 HTTP 200 但恆 0 筆，
        連剛發出、annotation 確實存在的 Mention 也搜不到；單用
        `space.name = ` 過濾同一個確定有數百則訊息的 Space 也是 0 筆。
        因此預設走實作 B，本方法保留供帳號條件改變後重測（見 docs/R1-findings.md）。

        另外兩個實測到的語法限制已寫死在這裡：
          - mention 條件只能用 `:`，用 `=` 回 400
          - filter 不可含 createTime（任何格式都回 400），所以時間範圍要在本地過濾
        """
        collected: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        flt = f'annotations.user_mentions.user.name:"{google_user_id}"'

        for _ in range(max_pages):
            body: Dict[str, Any] = {
                "filter": flt,
                "orderBy": SEARCH_ORDER_BY,
                "pageSize": page_size,
            }
            if page_token:
                body["pageToken"] = page_token
            data = self._request("POST", "spaces/-/messages:search", json_body=body)
            msgs = data.get("messages", [])
            collected.extend(msgs)
            page_token = data.get("nextPageToken")
            # 這個端點會在沒有資料時仍持續回傳 nextPageToken，
            # 所以「有 token」不能當成「還有資料」——連續空頁就停。
            if not page_token or not msgs:
                break

        return collected

    def send_message(
        self, space_id: str, text: str, thread_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """以 Viewer 本人身分送出訊息（ADR-0001）。

        thread_name 有值時回到原討論串（7.2 步驟 6）。
        """
        body: Dict[str, Any] = {"text": text}
        params: Dict[str, Any] = {}
        if thread_name:
            body["thread"] = {"name": thread_name}
            # 沒有這個參數的話，Chat 會另開新串而不是回到原串
            params["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
        return self._request(
            "POST", f"{space_id}/messages", params=params or None, json_body=body
        )

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------

    def whoami_via_probe(self, space_id: str) -> str:
        """向指定 Space 發一則訊息並讀回 sender.name，取得自身 user id。

        僅在沒有 userinfo scope 時作為最後手段（會在該 Space 留下一則訊息）。
        正式路徑是 core/identity.py 的 userinfo 解析。
        """
        res = self.send_message(space_id, "[ChatPulse] 身分確認訊息，可忽略")
        sender = res.get("sender", {}).get("name")
        if not sender:
            raise ChatApiError("送出訊息成功但回應沒有 sender.name，無法取得身分")
        return sender


def format_conversation(
    messages: List[Dict[str, Any]],
    name_resolver: Optional[Callable[[Optional[str]], str]] = None,
) -> str:
    """把訊息列表組成給模型讀的純文字對話。

    name_resolver 用來把 `users/{id}` 換成看得懂的名字。**這個參數不是可有可無的**：
    使用者驗證下 Google 從不回傳 `sender.displayName`（見 core/directory.py），
    沒有 resolver 的話每一位發言者都會變成「未知成員」，模型無法區分誰說了什麼。
    """
    lines = []
    for m in messages:
        sender_obj = m.get("sender") or {}
        sender = sender_obj.get("displayName")
        if not sender:
            sender = (
                name_resolver(sender_obj.get("name"))
                if name_resolver
                else "未知成員"
            )
        created = (m.get("createTime") or "")[:16].replace("T", " ")
        text = (m.get("text") or "").strip()
        if text:
            lines.append(f"[{created}] {sender}: {text}")
    return "\n".join(lines)


def initial_lookback() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        hours=cfg.MENTION_INITIAL_LOOKBACK_HOURS
    )
