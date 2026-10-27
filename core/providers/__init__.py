"""AI 供應商註冊表與解析。

用法：

    from core import providers
    p = providers.resolve("claude", usage_recorder=rec)   # 別名
    p = providers.resolve(None)                            # 用設定的預設值
    providers.describe_all()                               # 給前端的清單

`"claude"` 是**別名**而不是一個實作，目前底下只有本機的 Claude Code CLI
（Anthropic API 供應商已於 2026-09-05 移除，見 SPECIFICATION.md 3.2）。
保留這個別名的理由是它同時是 `CHATPULSE_AI_PROVIDER` 的預設值與 API 契約的
一部分；別名底下不可用時，錯誤訊息會說出「該裝什麼」，比直接寫死實作名有用。
"""

from typing import Dict, List, Optional

from .. import config as cfg
from ..errors import InvalidParameter
from .base import AIProvider, UsageRecorder
from .acp import ClaudeAcpProvider, CodexAcpProvider
from .claude_cli import ClaudeCLIProvider
from .gemini import GeminiProvider

_CLASSES = {
    ClaudeCLIProvider.name: ClaudeCLIProvider,
    GeminiProvider.name: GeminiProvider,
    CodexAcpProvider.name: CodexAcpProvider,
    ClaudeAcpProvider.name: ClaudeAcpProvider,
}

#: 別名 -> 依序嘗試的實作名稱。第一個「現在可用」的雀屏中選。
_ALIASES: Dict[str, List[str]] = {
    "claude": [ClaudeCLIProvider.name],
    "auto": [ClaudeCLIProvider.name, GeminiProvider.name],
}

VALID_NAMES = tuple(_CLASSES) + tuple(_ALIASES)

#: 實作名 -> 該供應商的常用模型**裸值**，供 MCP 與 API 層顯示選單。
#: ⚠️ **model 不是白名單**：Viewer 可以自訂輸入沒列在這裡的**值**，各層一律不得
#: 拿它來擋（理由見 `base.AIProvider.model_choices`）。
#: 但它的**鍵**是完整的：合法的實作名只有這些，所以「這個鍵還存在嗎」可以拿它判。
MODEL_CHOICES: Dict[str, tuple[str, ...]] = {
    impl_name: impl.model_choices for impl_name, impl in _CLASSES.items()
}


def _build(name: str, **kwargs) -> AIProvider:
    return _CLASSES[name](**kwargs)


def _canonical_name(name: Optional[str]) -> str:
    """把使用者給的名稱正規化成註冊表的鍵。**不展開別名。**

    `resolve()` 與 `is_multi_candidate()` 共用這一支，兩邊才不會對「沒給名稱時
    算誰」有不同答案——那種分歧會讓守門條件與實際建構的供應商對不上。
    """
    return (name or cfg.AI_PROVIDER or "claude").strip().lower()


def is_multi_candidate(name: Optional[str]) -> bool:
    """這個名稱（**展開前**）是不是會挑選多個候選實作的別名。

    目前只有 `auto`，但**呼叫端不可以自己寫死這個字**：別名表 `_ALIASES` 在
    這個模組裡，抄一份到別層之後，日後新增第二個多候選別名時抄本不會跟著變，
    而那個漏洞的長相是——指定的 model 被送進一個它不適用的供應商，
    要到真的呼叫 API 才失敗（2026-09-17 驗收實測：dashboard 手抄的
    `MULTI_CANDIDATE_ALIASES` 就漏掉了新加的別名，`opus` 真的送進了 Gemini）。
    所以判準只有這裡一份，其他層一律呼叫它。

    name 為 None 時與 `resolve()` 一樣退回 `CHATPULSE_AI_PROVIDER`——
    伺服器預設本身就可能是 `auto`，「沒指定」不等於「不是別名」。
    不認得的名稱回 False：那是 `resolve()` 要負責拒絕的事，不在這裡搶著判。
    """
    return len(_ALIASES.get(_canonical_name(name), ())) > 1


def resolve(
    name: Optional[str] = None,
    *,
    model: Optional[str] = None,
    usage_recorder: Optional[UsageRecorder] = None,
) -> AIProvider:
    """把名稱或別名解析成一個可用的供應商實例。

    name 為 None 時用 `CHATPULSE_AI_PROVIDER`（預設 `claude`）。
    名稱不合法時拋 INVALID_PARAMETER；別名底下全部不可用時，
    錯誤訊息會逐一列出各自的原因，而不是只說「沒有可用的供應商」——
    使用者需要知道的是「要設哪個環境變數」，不是「失敗了」。

    `model` 是**裸值**（`opus`、`gemini-3.6-flash`），原樣交給被選中的實作，
    None 表示沿用該實作的 config 預設。這裡刻意不驗證它在不在
    `MODEL_CHOICES` 裡：需求就是「白名單之外也能自訂」。
    """
    requested = _canonical_name(name)
    # 空字串與純空白視同「沒指定」。
    # 🔴 **不這樣做會怎麼壞**：API 與 CLI 兩邊的「沒填」都是空字串而不是 None，
    # 原樣往下傳的話，「沒填」會變成 `--model ''`——要到子行程真的跑起來才炸，
    # 而那個錯誤訊息完全看不出是空字串造成的（看起來像模型名無效）。
    # 一併 strip()：自訂輸入是讓 Viewer 打字的欄位，最常見的髒值就是尾端空白，
    # `opus ` 與 `opus` 在這裡必須是同一件事。
    model = (model or "").strip() or None

    if model and is_multi_candidate(requested):
        # 🔴 多候選別名會自己挑供應商，而 model 只對其中一家有意義。
        # 放行的下場是把 `opus` 餵給 Gemini，**要到真的呼叫 API 才會失敗**——
        # 符合專案硬規則「認不出來就拋，絕不猜」，在這裡就擋掉。
        raise InvalidParameter(
            f"指定 model 時請一併指定具體的供應商（{'、'.join(_CLASSES)}）。"
            f"{requested!r} 是別名，會自行挑選供應商，你指定的 model 未必適用。"
        )

    if requested in _CLASSES:
        return _build(requested, model=model, usage_recorder=usage_recorder)

    if requested in _ALIASES:
        reasons = []
        for candidate in _ALIASES[requested]:
            provider = _build(candidate, model=model, usage_recorder=usage_recorder)
            ok, reason = provider.available()
            if ok:
                return provider
            reasons.append(f"{candidate}：{reason}")
        raise InvalidParameter(
            f"別名 {requested!r} 底下沒有可用的供應商。" + "；".join(reasons)
        )

    raise InvalidParameter(
        f"未知的 AI 供應商 {name!r}。可用值：{'、'.join(VALID_NAMES)}"
    )


def describe_all() -> List[dict]:
    """列出每個供應商的狀態，供 API 與前端下拉選單使用。"""
    out = []
    for provider_name in _CLASSES:
        try:
            out.append(_build(provider_name).describe())
        except Exception as exc:  # 單一供應商壞掉不該讓整份清單掛掉
            out.append(
                {
                    "name": provider_name,
                    "label": provider_name,
                    "model": "?",
                    "available": False,
                    "reason": f"初始化失敗：{exc}",
                    # 這個分支要與 `describe()` 給**同一組鍵**：前端是逐項讀的，
                    # 少一個鍵不會顯示成「這家壞了」，而是讀到 undefined。
                    "model_id": "?",
                    "models": [],
                    # 初始化都失敗了就不該宣稱看得懂圖——這裡取保守側。
                    # 呼叫端會拿它決定「要不要先去下載附件」（見 base.py 的
                    # supports_vision 註解），報 True 會付了下載成本卻沒有效果。
                    "supports_vision": False,
                }
            )
    return out


def default_name() -> str:
    """目前設定的預設值（可能是別名）。"""
    return (cfg.AI_PROVIDER or "claude").strip().lower()


def resolve_name(name: Optional[str] = None) -> str:
    """解析出實際會被使用的供應商名稱（別名展開後）。"""
    return resolve(name).name


__all__ = [
    "AIProvider",
    "UsageRecorder",
    "resolve",
    "resolve_name",
    "describe_all",
    "default_name",
    "is_multi_candidate",
    "VALID_NAMES",
    "MODEL_CHOICES",
]
