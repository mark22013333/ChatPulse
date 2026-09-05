"""Claude Code CLI 供應商：把本機的 `claude` 指令當成文字產生器。

**這條路徑的價值**：吃使用者現有的 Claude Code 訂閱，不需要任何 API key，
裝好 Claude Code 就能用。R-4 的 Gemini 每日 20 次限制對它不適用。

**代價**（實測，不是推測）：
  1. 每次請求開一個子行程，啟動成本比 HTTP 呼叫高
  2. 預設會載入 Claude Code 自己的 system prompt、CLAUDE.md 與全部工具定義——
     一個 2-token 的 prompt 也會寫入 19,085 token 的快取（約 $0.077）。
     停掉工具與 MCP、並用自己的 system prompt 取代之後降到 0 token、約 $0.0006，
     所以下面那串參數不是可有可無的調校，是必要的
  3. 發給團隊時每個人都要裝好並登入 Claude Code

**刻意不用 `--bare`**：它雖然能跳過 CLAUDE.md 自動載入，但同時規定
「Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper；OAuth and keychain
are never read」——那會讓訂閱認證失效，正好毀掉這條路徑唯一的優點。
改用 `--system-prompt` 取代預設 prompt，效果相同而不動認證。
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
from typing import Iterator, List, Optional, Sequence

from .. import config as cfg
from ..errors import ClaudeApiError, ClaudeQuotaExceeded, ConfigurationError
from .base import AIProvider, ImagePart

DEFAULT_SYSTEM = (
    "你是一個文字分析工具。嚴格依照使用者訊息中的指示產生內容，"
    "不要加開場白、不要解釋你在做什麼、不要詢問後續問題。"
)


class ClaudeCLIProvider(AIProvider):
    name = "claude_cli"
    label = "Claude Code（本機 CLI，用你現有的訂閱）"
    # 實測：帶上本檔現行的全部參數（--restricted、工具全停用、--strict-mcp-config、
    # --system-prompt）再加 --input-format stream-json 送 image block，模型答得出
    # 圖片內容，且 init 事件顯示 "tools":[]。**視覺是模型的原生能力，不是 Read 工具**，
    # 所以停用工具不影響它。多張圖的順序也正確。
    supports_vision = True

    def __init__(self, model: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._model = model or cfg.CLAUDE_CLI_MODEL

    @property
    def model(self) -> str:
        return f"claude-cli:{self._model}"

    def available(self) -> tuple[bool, str]:
        binary = shutil.which(cfg.CLAUDE_CLI_BIN)
        if not binary:
            return (
                False,
                f"找不到 {cfg.CLAUDE_CLI_BIN} 指令。請先安裝 Claude Code，"
                "或用 CHATPULSE_CLAUDE_BIN 指到它的實際路徑。",
            )
        return True, f"使用本機 {binary}"

    # ------------------------------------------------------------------

    def _argv(
        self, system: Optional[str], stream: bool, has_images: bool = False
    ) -> List[str]:
        argv = [
            cfg.CLAUDE_CLI_BIN,
            "-p",
            "--model",
            self._model,
            # 這是一次性的文字生成，不需要留下 session
            "--no-session-persistence",
            # 拿掉會執行指令或碰檔案的內建工具
            "--restricted",
            # 連同其餘工具與 MCP 一起關掉——摘要用不到，但它們的 schema
            # 會佔掉一萬多個 token（見模組開頭的實測）
            "--disallowedTools",
            cfg.CLAUDE_CLI_DISABLED_TOOLS,
            "--strict-mcp-config",
            # 取代 Claude Code 自己的 system prompt，避免它的行為規範
            # 與使用者的 CLAUDE.md 影響摘要內容
            "--system-prompt",
            system or DEFAULT_SYSTEM,
        ]
        if has_images:
            # 圖片只能經由 stream-json 輸入傳進去。**CLI 強制**：
            # --input-format stream-json 一定要搭配 --output-format stream-json
            # （用 json 會被直接拒絕：「requires output-format=stream-json」），
            # 所以有圖時一律走串流路徑，generate() 也改成把串流接起來。
            argv += ["--input-format", "stream-json"]
        if stream or has_images:
            # stream-json 需要 --verbose，否則 CLI 直接拒絕執行
            argv += [
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--verbose",
            ]
        else:
            argv += ["--output-format", "json"]
        return argv

    @staticmethod
    def _stdin_payload(prompt: str, images: Optional[Sequence[ImagePart]]) -> str:
        """組 stdin 要寫的內容。

        無圖時就是原本的純文字 prompt（**argv 與 stdin 都一行不改，零迴歸**）；
        有圖時包成一行 stream-json 的 user 訊息。
        """
        if not images:
            return prompt

        content: List[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    },
                }
            )
        return json.dumps(
            {"type": "user", "message": {"role": "user", "content": content}},
            ensure_ascii=False,
        ) + "\n"

    def _run_dir(self) -> str:
        """在暫存目錄執行，避免把專案的 CLAUDE.md 與檔案帶進上下文。"""
        return tempfile.gettempdir()

    def _spawn(
        self,
        prompt: str,
        system: Optional[str],
        stream: bool,
        has_images: bool = False,
    ) -> subprocess.Popen:
        ok, reason = self.available()
        if not ok:
            raise ConfigurationError(reason)
        # prompt 走 stdin 而不是 argv：摘要的 prompt 可能上萬字，
        # 而且內容來自聊天室，不該經過 shell 或 argv 長度限制。
        # 圖片同理——base64 後可能好幾百 KB，argv 塞不下。
        return subprocess.Popen(
            self._argv(system, stream, has_images),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._run_dir(),
            text=True,
            encoding="utf-8",
            env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
        )

    @staticmethod
    def _classify(stderr: str, exit_code: int) -> ClaudeApiError:
        low = (stderr or "").lower()
        if any(k in low for k in ("rate limit", "usage limit", "quota", "429")):
            return ClaudeQuotaExceeded(
                "Claude Code 的用量已達上限。等用量視窗重置，"
                "或改用 gemini 供應商。"
            )
        if any(k in low for k in ("not logged in", "unauthenticated", "login")):
            return ConfigurationError(
                "Claude Code 尚未登入。請在終端執行 claude 完成登入後再試。"
            )
        snippet = (stderr or "").strip()[:300] or f"exit code {exit_code}"
        return ClaudeApiError(f"Claude Code CLI 執行失敗：{snippet}")

    def _record_from_usage(self, operation: str, usage: dict) -> None:
        if not usage:
            return
        # CLI 的 usage 欄位名與 Anthropic API 一致
        prompt_tokens = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
        )
        output_tokens = usage.get("output_tokens") or 0
        self._record_usage(
            operation, prompt_tokens, output_tokens, prompt_tokens + output_tokens
        )

    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        operation: str = "generate",
        images: Optional[Sequence[ImagePart]] = None,
    ) -> str:
        if images:
            # 有圖時 CLI 強制走 stream-json 輸出，沒有非串流的 JSON 可以解析。
            # 重用既有的串流解析而不是為它寫第二套。
            text = "".join(
                self.stream_text(prompt, system=system, operation=operation, images=images)
            )
            if not text.strip():
                raise ClaudeApiError("Claude Code CLI 回傳空內容")
            return text

        proc = self._spawn(prompt, system, stream=False)
        try:
            stdout, stderr = proc.communicate(prompt, timeout=cfg.CLAUDE_CLI_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ClaudeApiError(
                f"Claude Code CLI 超過 {cfg.CLAUDE_CLI_TIMEOUT} 秒未回應"
            )

        if proc.returncode != 0:
            raise self._classify(stderr, proc.returncode)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            raise ClaudeApiError(f"無法解析 Claude Code CLI 的輸出：{stdout[:300]}")

        if data.get("is_error"):
            raise self._classify(str(data.get("result") or ""), 1)

        self._record_from_usage(operation, data.get("usage") or {})
        text = data.get("result") or ""
        if not text.strip():
            raise ClaudeApiError("Claude Code CLI 回傳空內容")
        return text

    def stream_text(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        operation: str = "generate",
        images: Optional[Sequence[ImagePart]] = None,
    ) -> Iterator[str]:
        proc = self._spawn(prompt, system, stream=True, has_images=bool(images))
        assert proc.stdin and proc.stdout
        usage: dict = {}
        try:
            proc.stdin.write(self._stdin_payload(prompt, images))
            proc.stdin.close()

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # CLI 偶爾會夾雜非 JSON 的行；跳過而不是中斷整個串流
                    continue

                etype = event.get("type")
                if etype == "stream_event":
                    inner = event.get("event") or {}
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif inner.get("type") == "message_delta":
                        usage.update(inner.get("usage") or {})
                elif etype == "result":
                    usage.update(event.get("usage") or {})
                    if event.get("is_error"):
                        raise self._classify(str(event.get("result") or ""), 1)
                elif etype == "rate_limit_event":
                    info = event.get("rate_limit_info") or {}
                    # 只有真的被擋才中斷；warning 讓它跑完
                    if info.get("status") in ("rejected", "blocked"):
                        raise ClaudeQuotaExceeded(
                            "Claude Code 的用量已達上限，本次請求被拒絕。"
                        )
        finally:
            if proc.poll() is None:
                proc.kill()
            stderr = proc.stderr.read() if proc.stderr else ""
            proc.wait()
            if proc.returncode not in (0, -9) and stderr:
                # 已經 yield 過內容時不要再拋——呼叫端會把它包成 error 事件，
                # 但那時前端已經顯示了部分結果，中途變成錯誤更令人困惑
                if not usage:
                    raise self._classify(stderr, proc.returncode)
            self._record_from_usage(operation, usage)
