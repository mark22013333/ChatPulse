"""ChatPulse 錯誤型別。

規格對應：SPECIFICATION.md 8.4。非串流端點統一回
    {"error": {"code": "SPACE_NOT_FOUND", "message": "找不到指定的聊天室"}}
串流端點則以事件傳遞，不中斷連線：
    data: {"type":"error","code":"GEMINI_QUOTA_EXCEEDED","message":"..."}

每個型別自帶 HTTP 狀態碼與規格書表格中的 code，避免呼叫端各自拼字串。
"""

from typing import Any, Dict, Optional


class ChatPulseError(Exception):
    """所有可預期錯誤的基底。code 與 http_status 由子類別指定。"""

    code = "INTERNAL_ERROR"
    http_status = 500
    default_message = "系統內部錯誤"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        retry_after: Optional[int] = None,
        detail: Optional[str] = None,
    ):
        self.message = message or self.default_message
        self.retry_after = retry_after
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """非串流端點的回應主體（8.4）。"""
        return {"error": {"code": self.code, "message": self.message}}

    def to_sse_event(self) -> Dict[str, Any]:
        """串流端點的錯誤事件（8.3）。"""
        return {"type": "error", "code": self.code, "message": self.message}


class InvalidParameter(ChatPulseError):
    code = "INVALID_PARAMETER"
    http_status = 400
    default_message = "參數格式錯誤"


class NotAuthenticated(ChatPulseError):
    code = "NOT_AUTHENTICATED"
    http_status = 401
    default_message = "未登入或 session 已過期"


class SpaceForbidden(ChatPulseError):
    code = "SPACE_FORBIDDEN"
    http_status = 403
    default_message = "你不是該聊天室的成員"


class SpaceNotFound(ChatPulseError):
    code = "SPACE_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的聊天室"


class MentionNotFound(ChatPulseError):
    code = "MENTION_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的 Mention"


class RouteNotFound(ChatPulseError):
    """API 路徑不存在。

    刻意與 SPACE_NOT_FOUND 分開：借用後者會讓「打錯端點」在前端看起來像
    「這個聊天室不見了」，訊息（「找不到指定的聊天室」）與情境完全不符。
    """

    code = "ROUTE_NOT_FOUND"
    http_status = 404
    default_message = "找不到這個 API 端點"


class ChatRateLimited(ChatPulseError):
    code = "CHAT_RATE_LIMITED"
    http_status = 429
    default_message = "Google Chat 已限流，請稍後再試"


class GeminiQuotaExceeded(ChatPulseError):
    code = "GEMINI_QUOTA_EXCEEDED"
    http_status = 429
    default_message = "Gemini 配額已用盡"


class ClaudeQuotaExceeded(ChatPulseError):
    """Claude 用量上限。

    與 GEMINI_QUOTA_EXCEEDED 分開，因為兩者的處置不同：Gemini 免費層是
    每日請求數（等隔天或換模型），Claude Code 訂閱是滾動時間窗的用量
    （等窗口重置或改用 API key）。前端要能對使用者說清楚該怎麼辦。
    """

    code = "CLAUDE_QUOTA_EXCEEDED"
    http_status = 429
    default_message = "Claude 用量已達上限"


class ClaudeApiError(ChatPulseError):
    code = "CLAUDE_API_ERROR"
    http_status = 502
    default_message = "Claude 回應非預期內容"


class ChatApiError(ChatPulseError):
    code = "CHAT_API_ERROR"
    http_status = 502
    default_message = "Google Chat 回應非預期內容"


class GeminiApiError(ChatPulseError):
    code = "GEMINI_API_ERROR"
    http_status = 502
    default_message = "Gemini 回應非預期內容"


class ConfigurationError(ChatPulseError):
    code = "CONFIGURATION_ERROR"
    http_status = 500
    default_message = "伺服器設定不完整"


def classify_google_api_error(status_code: int, body: str) -> ChatPulseError:
    """把 Google Chat 的 HTTP 錯誤轉成對應的 ChatPulseError。

    只依狀態碼分流，不解析錯誤字串——Google 的 message 文字會變，狀態碼不會。
    """
    snippet = (body or "")[:400]
    if status_code == 401:
        return NotAuthenticated("Google 憑證已失效，請重新登入")
    if status_code == 403:
        return SpaceForbidden(detail=snippet)
    if status_code == 404:
        return SpaceNotFound(detail=snippet)
    if status_code == 429:
        return ChatRateLimited(detail=snippet)
    return ChatApiError(detail=snippet)


def classify_gemini_error(status_code: int, body: str) -> ChatPulseError:
    """把 Gemini 的 HTTP 錯誤轉成對應的 ChatPulseError。"""
    snippet = (body or "")[:400]
    if status_code == 429:
        return GeminiQuotaExceeded(detail=snippet)
    if status_code in (401, 403):
        return ConfigurationError("Gemini API key 無效或權限不足", detail=snippet)
    return GeminiApiError(detail=snippet)
