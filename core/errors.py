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


class DraftNotFound(ChatPulseError):
    """這則 Mention 還沒有存下來的草稿。

    刻意與 MENTION_NOT_FOUND 分開：前者是「這則不是你的／不存在」，
    要擋；這個是**完全正常的狀態**——多數 Mention 本來就還沒產過草稿，
    前端拿到它只要安靜地不顯示還原區塊即可，不該當成錯誤跳出來。
    """

    code = "DRAFT_NOT_FOUND"
    http_status = 404
    default_message = "這則 Mention 還沒有草稿"


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


class ZPlannerAuthError(ChatPulseError):
    """ZPlanner 回 code 401：token 無效、過期或根本沒帶。

    **刻意與 NOT_AUTHENTICATED 分開，雖然兩者都是 401 語意。** 那個是
    「Viewer 的 ChatPulse session 過期了」，前端該做的是把人導去重新登入
    Google；這個是「伺服器手上那把 ZPlanner token 不能用了」，Viewer 再怎麼
    重新登入都沒用，得由管理者去 ZPlanner 的 /api/tokens/ 重產一把、更新
    ZPLANNER_APIKEY 再重啟。合成同一個錯誤碼會讓前端把人導進一個
    「登入了還是壞的」的迴圈。

    http_status 因此是 500 而不是 401——問題出在伺服器的設定，不是呼叫端。
    """

    code = "ZPLANNER_AUTH_ERROR"
    http_status = 500
    default_message = "ZPlanner token 無效或已過期"


class ZPlannerForbidden(ChatPulseError):
    """ZPlanner 回 code 403：這個帳號在該專案沒有這項權限。

    這是**正常的可預期結果**，不是設定錯誤：ZPlanner 的權限是每個專案各自
    一張角色矩陣、由該專案 PM 設定，同一把 token 在 A 專案能填工時、在 B
    專案不能。所以前端要能照實說「你在這個專案沒有填工時的權限」，而不是
    報一句「系統設定有問題」讓人去找管理者——找了也沒用，要找的是該專案 PM。
    """

    code = "ZPLANNER_FORBIDDEN"
    http_status = 403
    default_message = "你在該 ZPlanner 專案沒有這項權限"


class ZPlannerNotFound(ChatPulseError):
    code = "ZPLANNER_NOT_FOUND"
    http_status = 404
    default_message = "找不到指定的 ZPlanner 資源"


class ZPlannerInvalidParameter(ChatPulseError):
    """ZPlanner 回 code 400：hours／date／start_time 之類的格式不合。

    刻意與 INVALID_PARAMETER 分開：那個是 ChatPulse 自己的 API 契約被違反
    （呼叫端的錯），這個是 ZPlanner 那側的欄位規則被違反。兩者要修的地方
    不同——後者要去對照 ZPlanner 的欄位格式（例如 hours 是**字串**且最多
    兩位小數），而不是去翻 ChatPulse 的 api-contract。
    """

    code = "ZPLANNER_INVALID_PARAMETER"
    http_status = 400
    default_message = "ZPlanner 欄位格式錯誤"


class ZPlannerUnavailable(ChatPulseError):
    """連不上 ZPlanner，或連上了但逾時——**請求沒有到達應用層**。

    與 ZPLANNER_API_ERROR 分開的理由和 PERSONA_SOURCE_ERROR 與
    PERSONA_INVALID 同族：這個是「東西拿不到」（稍後再試、確認 VPN／內網
    通不通），那個是「拿到了但讀不出東西」（ZPlanner 改了回應格式，要改程式）。
    對寫入操作來說這個差別更關鍵：逾時**不保證沒寫進去**，重試前要先查。
    """

    code = "ZPLANNER_UNAVAILABLE"
    http_status = 502
    default_message = "無法連線到 ZPlanner"


class ZPlannerApiError(ChatPulseError):
    """連上了，但回應不是預期的形狀（非 JSON、缺 code 欄位、未知的 code）。

    這一類一律往上拋而不是猜——見 `core/zplanner_client` 的模組 docstring：
    這支 API 成敗只看 body 的 code，所以「讀不出 code」等於「不知道成功了
    沒有」，靜默當成成功是這個整合最貴的失敗模式。
    """

    code = "ZPLANNER_API_ERROR"
    http_status = 502
    default_message = "ZPlanner 回應非預期內容"


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


def classify_zplanner_error(code: int, message: str = "") -> ChatPulseError:
    """把 ZPlanner **回應主體裡的** code 轉成對應的 ChatPulseError。

    ⚠️ **簽章刻意與上面兩個 classify_* 不同：第一個參數不是 HTTP 狀態碼。**
    ZPlanner 的 HTTP 狀態碼恆為 200（連 401／403 都是），拿它分流等於把每一種
    失敗都當成功。呼叫端必須傳 body 的 `code` 欄位進來，不是 `resp.status_code`。
    完整說明見 `core/zplanner_client` 的模組 docstring。

    ZPlanner 自己的 message（「權限不足」「請先登入」）放進 detail 保留原文，
    對外訊息用我們的 default_message——後者帶得動「該找誰、下一步做什麼」，
    前者只說了發生什麼事。這與 classify_google_api_error 的處理一致。
    """
    snippet = (message or "").strip()[:400]
    if code == 400:
        return ZPlannerInvalidParameter(detail=snippet)
    if code == 401:
        return ZPlannerAuthError(detail=snippet)
    if code == 403:
        return ZPlannerForbidden(detail=snippet)
    if code == 404:
        return ZPlannerNotFound(detail=snippet)
    # 未知 code 一律往上拋。**不要**在這裡 fallback 成「當作成功」——
    # 這支 API 沒有其他管道可以判斷成敗，猜錯的代價是靜默寫入或靜默漏資料。
    return ZPlannerApiError(
        f"ZPlanner 回傳未預期的 code {code}", detail=snippet
    )
