"""解析 Viewer 的 Google 身分。

規格對應：SPECIFICATION.md 4.2（Phase 2 另需 userinfo.profile）、8.2（/api/v1/me）。

三條路徑，依序嘗試：
  1. **userinfo**（正式路徑）：需要 openid／userinfo.profile／userinfo.email scope，
     回傳的 `sub` 就是 Google Chat 的 `users/{id}` 中的那個 id。
  2. **設定值覆寫**：舊的三 scope token 沒有 userinfo 權限。要把它匯入 credentials 表
     時，可用 CHATPULSE_BOOTSTRAP_USER_ID 補上已知的 id。
  3. **發訊探測**（最後手段，需明確指定目標 Space）：向該 Space 發一則訊息，
     從回應的 `sender.name` 讀回自己的 id。會在該 Space 留下痕跡，所以不自動觸發。

實測補充（2026-09-05）：`https://oauth2.googleapis.com/tokeninfo` **不回傳 `sub`**，
只回 scope 與 aud，因此不能拿來當免 scope 的身分來源。
"""

from typing import Any, Dict, Optional

import requests
from google.oauth2.credentials import Credentials

from . import config as cfg
from .errors import ConfigurationError, NotAuthenticated

USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def has_identity_scope(scopes: Optional[list]) -> bool:
    if not scopes:
        return False
    joined = " ".join(scopes)
    return "userinfo.profile" in joined or "openid" in joined


def fetch_userinfo(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """呼叫 userinfo。缺 scope 時回 None（不拋錯，讓呼叫端走備援路徑）。"""
    if not credentials.token:
        return None
    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code in (401, 403):
        return None
    raise ConfigurationError(
        f"呼叫 userinfo 失敗（HTTP {resp.status_code}）：{resp.text[:200]}"
    )


def resolve(credentials: Credentials) -> Dict[str, Optional[str]]:
    """回傳 {google_user_id, email, display_name}。

    google_user_id 一律是 Google Chat 的資源名格式 `users/{numeric id}`，
    這樣才能直接和訊息 annotation 裡的 `userMention.user.name` 逐字比對（6.1）。
    """
    info = fetch_userinfo(credentials)
    if info and info.get("sub"):
        return {
            "google_user_id": f"users/{info['sub']}",
            "email": info.get("email"),
            "display_name": info.get("name") or info.get("email"),
        }

    if cfg.BOOTSTRAP_USER_ID:
        user_id = cfg.BOOTSTRAP_USER_ID
        if not user_id.startswith("users/"):
            user_id = f"users/{user_id}"
        return {
            "google_user_id": user_id,
            "email": cfg.BOOTSTRAP_EMAIL or None,
            # **不可以拿 user_id 當顯示名稱。** 這條路徑（舊的三 scope token，
            # 沒有 userinfo 權限）本來就查不到名字，回 None 讓上層 COALESCE
            # 保留既有的名字、或改用名錄去查。
            # 原本這裡回 user_id，後果有兩層：viewers.display_name 被覆寫成
            # 「users/1098…」（畫面右上角就顯示那一串），而且 server 的
            # `directory.remember(..., display_name)` 會把同一串寫進人名名錄，
            # 於是連對話裡的「我（users/1098…）」也是這麼來的。
            "display_name": cfg.BOOTSTRAP_EMAIL or None,
        }

    raise NotAuthenticated(
        "無法解析你的 Google 身分。這個憑證沒有 userinfo 權限——"
        "請透過 /api/v1/auth/login 重新登入（會一併取得 openid 與 userinfo scope），"
        "或設定 CHATPULSE_BOOTSTRAP_USER_ID 匯入既有的三 scope token。"
    )
