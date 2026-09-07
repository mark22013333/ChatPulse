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


class CodeProjectNotFound(ChatPulseError):
    code = "CODE_PROJECT_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的參考專案"


class CodeProjectUnavailable(ChatPulseError):
    """專案登錄還在，但本機路徑已經不是一個可讀的 git repo。

    刻意與 CODE_BRANCH_NOT_FOUND 分開：兩者的下一步完全不同。
    這個要去改路徑（或專案被搬走／磁碟沒掛上），那個要去改分支對應。
    合成同一個錯誤碼會讓前端只能給一句模糊的「設定有問題」。
    """

    code = "CODE_PROJECT_UNAVAILABLE"
    http_status = 409
    default_message = "參考專案的路徑不存在或不是 git repo"


class CodeBranchNotFound(ChatPulseError):
    """環境對應到的分支在 repo 裡不存在。

    這是硬失敗而不是降級：使用者要的就是「正式環境的程式碼」，
    拿不到卻照樣產草稿，等於給他一份沒有依據、但看起來有依據的答案。
    """

    code = "CODE_BRANCH_NOT_FOUND"
    http_status = 409
    default_message = "環境對應的分支不存在"


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


class PersonaNotFound(ChatPulseError):
    code = "PERSONA_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的 Persona"


class PersonaSourceError(ChatPulseError):
    """從外部來源取得 Persona 時失敗（網路、404、超過大小、格式不對）。

    刻意與 PERSONA_INVALID 分開：這個是「東西拿不到」（換網址、稍後再試、
    確認 repo 是公開的），那個是「拿到了但讀不出東西」（換來源或改用手動填寫）。
    502 而不是 400，因為問題出在外部服務或外部內容，不是呼叫端的參數。
    """

    code = "PERSONA_SOURCE_ERROR"
    http_status = 502
    default_message = "無法從來源取得 Persona"


class PersonaInvalid(ChatPulseError):
    """檔案抓到了，但淨化之後沒有任何可用的風格資訊。

    這是**正常的可預期結果**，不是 bug：來源檔案可能整份都是角色扮演指令
    與工作流程（那些一律不採用，見 `core/personas.py`），淨化完就空了。
    409 與 CODE_PROJECT_UNAVAILABLE 同族——東西存在，但不能用。
    """

    code = "PERSONA_INVALID"
    http_status = 409
    default_message = "這份 Persona 沒有可用的風格資訊"


class ReplyPromptNotFound(ChatPulseError):
    code = "REPLY_PROMPT_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的回覆提示詞"


class SepiaUnavailable(ChatPulseError):
    """要求了 Sepia 潤稿，但潤稿規則或供應商不可用。

    **刻意是硬失敗，不是靜默降級。** 使用者勾了「使用 Sepia 潤稿」卻拿到
    一份沒潤過的草稿，他不會知道——他只會覺得這個功能沒效果，然後把
    「AI 味還在」歸因到規則沒用，而不是規則沒跑。

    這與「潤稿跑了但完整性檢查沒過」是兩種不同情況：後者會退回未潤稿的
    版本並在 meta 標示 `fallback_reason`，不拋錯（見
    `core/polishers/base.PolishResult`）。
    """

    code = "SEPIA_UNAVAILABLE"
    http_status = 409
    default_message = "Sepia 潤稿目前無法使用"


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
