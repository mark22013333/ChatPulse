"""AI 供應商介面。

規格對應：SPECIFICATION.md 3.2（AI）、8.3（SSE 事件）、十二（R-4 配額）。

**為什麼要抽象這一層**：R-4 實測 Gemini 免費層 `gemini-3.6-flash` 每天只有 20 次
請求，一輪驗證就用完了。把供應商換成可選之後，配額不再是單點故障，而且兩個
供應商各有適用場景——Claude Code CLI 吃現有訂閱、零設定；Gemini 保留為既有選項。

介面刻意很窄：只有「產生文字」與「串流產生文字」兩件事。摘要與 Draft Reply
都不需要工具呼叫、不需要多輪對話，把介面開大只會讓兩個實作互相遷就。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Sequence


@dataclass(frozen=True)
class ImagePart:
    """要一起送給模型的一張圖。

    `data` 是**原始位元組**，不是路徑也不是 base64——base64 編碼是各實作自己的事
    （Claude CLI 與 Gemini 的包裝格式不同）。刻意不落地成暫存檔：規格 3.2 的
    「對話全文不寫入資料庫」在圖片上同樣適用，位元組全程只待在記憶體。
    """

    media_type: str  # image/png、image/jpeg、image/gif、image/webp
    data: bytes
    label: str = ""  # 檔名，用於降級時的佔位符與除錯

UsageRecorder = Callable[[str, str, int, int, int], None]
"""(operation, model, prompt_tokens, output_tokens, total_tokens) -> None

model 由供應商自己回報，不由呼叫端猜——換供應商時用量表才不會記到錯的模型上。
"""


class AIProvider(ABC):
    """一個可以產生文字的 AI 供應商。"""

    #: 程式用的識別字，同時是 API 與設定值接受的名稱
    name: str = "base"
    #: 給人看的名稱（繁體中文，前端下拉選單用）
    label: str = "未命名供應商"
    #: 這個實作能不能看圖。**呼叫端要在「下載圖片之前」先問這個**——
    #: 若只在實作內部靜默忽略 images，就會付了下載與流量成本卻沒有效果。
    supports_vision: bool = False

    def __init__(self, *, usage_recorder: Optional[UsageRecorder] = None):
        self._usage_recorder = usage_recorder

    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def model(self) -> str:
        """實際使用的模型識別字，會被記進 token_usage。"""

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """這個供應商現在能不能用。

        回傳 (可用, 原因)。**不可用時原因要寫成使用者看得懂、且說得出下一步的話**
        ——例如「找不到 claude 指令，請先安裝 Claude Code」，而不是「binary not found」。
        這個方法不該打網路，只檢查本機條件（金鑰、執行檔是否存在），
        因為它會被列表端點每次呼叫。
        """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        operation: str = "generate",
        images: Optional[Sequence[ImagePart]] = None,
    ) -> str:
        """一次產生完整文字。"""

    @abstractmethod
    def stream_text(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        operation: str = "generate",
        images: Optional[Sequence[ImagePart]] = None,
    ) -> Iterator[str]:
        """逐段 yield 文字片段。

        只吐純文字，不吐 SSE frame——包成規格 8.3 的事件是呼叫端的事，
        這樣 MCP 與 CLI 兩個入口才能重用同一個實作。
        """

    # ------------------------------------------------------------------

    def _record_usage(
        self,
        operation: str,
        prompt_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        if not self._usage_recorder:
            return
        try:
            self._usage_recorder(
                operation, self.model, prompt_tokens, output_tokens, total_tokens
            )
        except Exception:  # 用量記錄失敗不該讓摘要失敗
            pass

    def describe(self) -> dict:
        """給 API 與前端用的描述。"""
        ok, reason = self.available()
        return {
            "name": self.name,
            "label": self.label,
            "model": self.model,
            "available": ok,
            "reason": reason,
            "supports_vision": self.supports_vision,
        }
