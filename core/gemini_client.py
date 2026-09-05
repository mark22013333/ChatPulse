"""Gemini API 封裝：非串流與 SSE 串流兩種呼叫。

規格對應：SPECIFICATION.md 3.2、8.3、十一（D-3）、十二（R-2）。

三個與 v1 實作的差異：
  1. maxOutputTokens 由 2048 提為 16384（D-3：500 則對話的結構化摘要會被截斷）
  2. 串流輸出改為規格 8.3 的事件格式（meta／chunk／done／error），不再是自訂的
     `{"chunk": ...}` 與 `[DONE]`
  3. 每次呼叫記錄 usageMetadata 的 token 數（R-2：累積兩週後評估成本）
"""

import base64
import json
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

import requests

from . import config as cfg
from .errors import ConfigurationError, GeminiApiError, classify_gemini_error

UsageRecorder = Callable[[str, int, int, int], None]
"""(operation, prompt_tokens, output_tokens, total_tokens) -> None"""


class GeminiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        *,
        usage_recorder: Optional[UsageRecorder] = None,
    ):
        self.api_key = api_key or cfg.GEMINI_API_KEY
        if not self.api_key:
            raise ConfigurationError(
                "缺少 Gemini API key。請先設定環境變數再啟動：\n"
                "  export GOOGLE_API_KEY='你的 Gemini API key'"
            )
        self.model = model or cfg.GEMINI_MODEL
        self._usage_recorder = usage_recorder

    # ------------------------------------------------------------------

    def _url(self, method: str, sse: bool = False) -> str:
        """組 endpoint URL。**API key 不放 query string。**

        把 key 放在 `?key=` 會讓它跟著 URL 出現在每一個含 URL 的地方：
        requests 的 `HTTPError` 訊息、traceback、access log、以及任何
        把 exception 印出來的地方。實測 D-3 測試撞到 429 時，
        `raise_for_status()` 就把整把金鑰印在終端上。
        改用官方支援的 `x-goog-api-key` 標頭（見 _headers）。
        """
        url = f"{cfg.GEMINI_API_BASE}/models/{self.model}:{method}"
        if sse:
            url += "?alt=sse"
        return url

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _payload(
        self,
        prompt: str,
        max_output_tokens: Optional[int] = None,
        images: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for img in images or []:
            # Gemini 的圖片走 inlineData（mimeType ＋ base64），與 text 併列在
            # 同一個 parts 陣列裡。
            # ⚠️ 這條路徑**尚未對真實 API 跑過**：Gemini 免費層每天只有 20 次請求
            # （R-4），把配額燒在測試上會擋掉使用者自己的使用。欄位名依官方文件，
            # 但單次請求的總大小上限與超限行為未驗證。
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img.media_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    }
                }
            )
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": cfg.GEMINI_TEMPERATURE,
                # D-3：2048 不夠，500 則對話的結構化摘要會被截斷
                "maxOutputTokens": max_output_tokens or cfg.GEMINI_MAX_OUTPUT_TOKENS,
            },
        }

    def _record_usage(self, operation: str, usage: Optional[Dict[str, Any]]) -> None:
        if not usage or not self._usage_recorder:
            return
        self._usage_recorder(
            operation,
            int(usage.get("promptTokenCount") or 0),
            int(usage.get("candidatesTokenCount") or 0),
            int(usage.get("totalTokenCount") or 0),
        )

    # ------------------------------------------------------------------
    # 非串流
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        operation: str = "generate",
        images: Optional[Sequence[Any]] = None,
    ) -> str:
        resp = requests.post(
            self._url("generateContent"),
            json=self._payload(prompt, images=images),
            headers=self._headers(),
            timeout=300,
        )
        if resp.status_code != 200:
            raise classify_gemini_error(resp.status_code, resp.text)

        data = resp.json()
        self._record_usage(operation, data.get("usageMetadata"))

        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise GeminiApiError(f"Gemini 沒有回傳任何候選內容（promptFeedback: {feedback}）")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            reason = candidates[0].get("finishReason", "UNKNOWN")
            raise GeminiApiError(f"Gemini 回傳空內容（finishReason: {reason}）")
        return text

    def summarize_discussion(
        self,
        space_name: str,
        conversation_text: str,
        message_count: int,
        style: str = cfg.SUMMARY_STYLE_DEFAULT,
    ) -> str:
        """單群摘要（非串流）。MCP 與 CLI 走這條。"""
        from . import prompts

        style = prompts.validate_style(style)
        prompt = prompts.summary_prompt(
            space_name, conversation_text, message_count, style
        )
        return self.generate(prompt, operation="summarize")

    # ------------------------------------------------------------------
    # 串流（SSE）
    # ------------------------------------------------------------------

    def stream_text(
        self,
        prompt: str,
        *,
        operation: str = "generate",
        images: Optional[Sequence[Any]] = None,
    ) -> Iterator[str]:
        """逐段 yield 文字。上游是 Gemini 的 :streamGenerateContent?alt=sse。

        呼叫端負責把這些片段包成規格 8.3 的 chunk 事件——本方法只吐純文字，
        不吐 SSE frame，這樣 MCP／CLI 也能重用。
        """
        usage: Optional[Dict[str, Any]] = None
        try:
            with requests.post(
                self._url("streamGenerateContent", sse=True),
                json=self._payload(prompt, images=images),
                headers=self._headers(),
                stream=True,
                timeout=300,
            ) as resp:
                if resp.status_code != 200:
                    raise classify_gemini_error(resp.status_code, resp.text[:2000])

                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace")
                    if not line.startswith("data: "):
                        continue
                    body = line[6:].strip()
                    if not body or body == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        # 上游偶爾切在 JSON 中間；跳過該行，不要中斷整個串流
                        continue
                    if chunk.get("usageMetadata"):
                        usage = chunk["usageMetadata"]
                    for cand in chunk.get("candidates") or []:
                        for part in cand.get("content", {}).get("parts") or []:
                            text = part.get("text")
                            if text:
                                yield text
        except requests.RequestException as exc:
            raise GeminiApiError(f"連線 Gemini 失敗：{exc}") from exc
        finally:
            self._record_usage(operation, usage)
