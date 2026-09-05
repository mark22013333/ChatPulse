"""Anthropic API 供應商（官方 anthropic Python SDK）。

規格對應：SPECIFICATION.md 3.2（AI）、十二（R-4）。

適用場景：發給團隊、或需要並發與穩定延遲時。與 CLI 路徑的差別是需要
`ANTHROPIC_API_KEY`（或 `ant auth login` 的 OAuth profile），按 token 計費，
但沒有子行程開銷、也不依賴本機裝了 Claude Code。

> ⚠️ **這個實作尚未對真實 API 跑過。** 撰寫當下這台機器沒有 `ANTHROPIC_API_KEY`
> 也沒有 `ant` CLI，所以只有匯入與參數組裝驗過，沒有實際呼叫紀錄。
> 第一次設定金鑰後請跑 `tests/e2e/test_providers.py` 確認。
> 相對地，`claude_cli` 與 `gemini` 兩條路徑都有實跑證據。
"""

from typing import Iterator, Optional

from .. import config as cfg
from ..errors import ClaudeApiError, ClaudeQuotaExceeded, ConfigurationError
from .base import AIProvider

DEFAULT_SYSTEM = (
    "你是一個文字分析工具。嚴格依照使用者訊息中的指示產生內容，"
    "不要加開場白、不要解釋你在做什麼、不要詢問後續問題。"
)


class ClaudeAPIProvider(AIProvider):
    name = "claude_api"
    label = "Claude（Anthropic API）"

    def __init__(self, model: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._model = model or cfg.CLAUDE_API_MODEL
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "未安裝 anthropic 套件，請執行 pip install anthropic"
        # 未設 ANTHROPIC_API_KEY 不代表沒有憑證：SDK 也會讀 ANTHROPIC_AUTH_TOKEN
        # 與 `ant auth login` 留下的 OAuth profile。這裡不打網路驗證，
        # 只在完全找不到任何來源時才擋。
        import os

        if os.environ.get("ANTHROPIC_API_KEY"):
            return True, "使用 ANTHROPIC_API_KEY"
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, "使用 ANTHROPIC_AUTH_TOKEN"

        # **目錄存在不等於有 profile。** 實測 ~/.config/anthropic 可能是空目錄
        # （其他工具建的），只檢查 isdir 會讓這個供應商誤報為可用，
        # 然後在真的要摘要時才失敗——比一開始就說不可用更難查。
        profile_dir = os.path.expanduser(
            os.environ.get("ANTHROPIC_CONFIG_DIR", "~/.config/anthropic")
        )
        if os.path.isdir(profile_dir):
            try:
                if any(
                    entry.is_file() and entry.name.endswith(".json")
                    for entry in os.scandir(profile_dir)
                ):
                    return True, f"使用 {profile_dir} 的 OAuth profile"
            except OSError:
                pass

        return (
            False,
            "找不到 Anthropic 憑證。設定 ANTHROPIC_API_KEY，"
            "或安裝 ant CLI 後執行 ant auth login 建立 OAuth profile。"
            "（若只想用現有的 Claude Code 訂閱，改選 claude_cli 供應商即可）",
        )

    def _get_client(self):
        if self._client is None:
            ok, reason = self.available()
            if not ok:
                raise ConfigurationError(reason)
            import anthropic

            # 零參數建構：SDK 依序解析 ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN
            # → OAuth profile，不要在這裡寫死金鑰來源
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------

    def _request_kwargs(self, prompt: str, system: Optional[str]) -> dict:
        kwargs = {
            "model": self._model,
            # 與 Gemini 端一致：這個上限要同時容納思考與正文（D-3 的教訓，
            # 在 Anthropic API 上同樣成立——thinking token 計入 max_tokens）
            "max_tokens": cfg.GEMINI_MAX_OUTPUT_TOKENS,
            "system": system or DEFAULT_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if cfg.CLAUDE_API_EFFORT:
            kwargs["output_config"] = {"effort": cfg.CLAUDE_API_EFFORT}
        return kwargs

    def _translate(self, exc: Exception) -> Exception:
        import anthropic

        if isinstance(exc, anthropic.RateLimitError):
            retry_after = None
            try:
                retry_after = int(exc.response.headers.get("retry-after", "60"))
            except Exception:
                pass
            return ClaudeQuotaExceeded(
                "Anthropic API 已限流，請稍後再試。", retry_after=retry_after
            )
        if isinstance(exc, anthropic.AuthenticationError):
            return ConfigurationError("Anthropic 憑證無效，請確認 ANTHROPIC_API_KEY")
        if isinstance(exc, anthropic.PermissionDeniedError):
            return ConfigurationError("Anthropic 憑證權限不足")
        if isinstance(exc, anthropic.NotFoundError):
            return ConfigurationError(f"找不到模型 {self._model}")
        if isinstance(exc, anthropic.APIConnectionError):
            return ClaudeApiError("連線 Anthropic API 失敗")
        if isinstance(exc, anthropic.APIStatusError):
            return ClaudeApiError(f"Anthropic API 回應 {exc.status_code}")
        return exc

    def _record(self, operation: str, usage) -> None:
        if usage is None:
            return
        prompt_tokens = (
            (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        self._record_usage(
            operation, prompt_tokens, output_tokens, prompt_tokens + output_tokens
        )

    @staticmethod
    def _check_refusal(message) -> None:
        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ClaudeApiError(
                "Claude 基於安全考量拒絕了這次請求"
                + (f"（類別：{category}）" if category else "")
                + "。這通常表示對話內容觸發了分類器，可改用其他供應商重試。"
            )

    # ------------------------------------------------------------------

    def generate(
        self, prompt: str, *, system: Optional[str] = None, operation: str = "generate"
    ) -> str:
        client = self._get_client()
        try:
            # max_tokens 較大時官方建議用串流避免 HTTP timeout，
            # 所以非串流路徑也走 stream 再取最終訊息
            with client.messages.stream(**self._request_kwargs(prompt, system)) as stream:
                message = stream.get_final_message()
        except Exception as exc:
            raise self._translate(exc) from exc

        self._record(operation, getattr(message, "usage", None))
        self._check_refusal(message)

        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        if not text.strip():
            raise ClaudeApiError(
                f"Claude 回傳空內容（stop_reason: {getattr(message, 'stop_reason', '?')}）"
            )
        return text

    def stream_text(
        self, prompt: str, *, system: Optional[str] = None, operation: str = "generate"
    ) -> Iterator[str]:
        client = self._get_client()
        try:
            with client.messages.stream(**self._request_kwargs(prompt, system)) as stream:
                for chunk in stream.text_stream:
                    if chunk:
                        yield chunk
                message = stream.get_final_message()
        except Exception as exc:
            raise self._translate(exc) from exc

        self._record(operation, getattr(message, "usage", None))
        self._check_refusal(message)
