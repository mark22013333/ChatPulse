"""Gemini 供應商：包裝既有的 `core.gemini_client.GeminiClient`。

刻意用包裝而不是把邏輯搬過來——`GeminiClient` 仍被 mcp_app 與既有測試直接引用，
搬走會製造一批沒有必要的破壞性改動。這一層只做兩件事：把介面對齊
`AIProvider`，以及把用量回報補上模型名（舊的 recorder 簽章不帶 model，
換供應商後用量表會記到錯的模型上）。
"""

from typing import Iterator, Optional

from .. import config as cfg
from ..errors import ConfigurationError
from ..gemini_client import GeminiClient
from .base import AIProvider


class GeminiProvider(AIProvider):
    name = "gemini"
    label = "Gemini（Google AI Studio）"

    def __init__(self, model: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._model = model or cfg.GEMINI_MODEL
        self._client: Optional[GeminiClient] = None

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> tuple[bool, str]:
        if not cfg.GEMINI_API_KEY:
            return (
                False,
                "未設定 GOOGLE_API_KEY。取得 Gemini API key 後 "
                "export GOOGLE_API_KEY='…' 再重啟服務。",
            )
        return True, "使用 GOOGLE_API_KEY"

    def _get_client(self) -> GeminiClient:
        if self._client is None:
            ok, reason = self.available()
            if not ok:
                raise ConfigurationError(reason)
            self._client = GeminiClient(
                model=self._model,
                # 舊 recorder 的簽章是 (operation, prompt, output, total)，
                # 這裡補上模型名再往上轉
                usage_recorder=lambda op, p, o, t: self._record_usage(op, p, o, t),
            )
        return self._client

    # ------------------------------------------------------------------
    # Gemini 端沒有獨立的 system 參數，指示直接併進 prompt——
    # 這與既有的 core/prompts.py 組法一致（instruction 寫在 prompt 開頭）
    # ------------------------------------------------------------------

    @staticmethod
    def _merge(prompt: str, system: Optional[str]) -> str:
        return f"{system}\n\n{prompt}" if system else prompt

    def generate(
        self, prompt: str, *, system: Optional[str] = None, operation: str = "generate"
    ) -> str:
        return self._get_client().generate(self._merge(prompt, system), operation=operation)

    def stream_text(
        self, prompt: str, *, system: Optional[str] = None, operation: str = "generate"
    ) -> Iterator[str]:
        yield from self._get_client().stream_text(
            self._merge(prompt, system), operation=operation
        )
