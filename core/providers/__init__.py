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
from .claude_cli import ClaudeCLIProvider
from .gemini import GeminiProvider

_CLASSES = {
    ClaudeCLIProvider.name: ClaudeCLIProvider,
    GeminiProvider.name: GeminiProvider,
}

#: 別名 -> 依序嘗試的實作名稱。第一個「現在可用」的雀屏中選。
_ALIASES: Dict[str, List[str]] = {
    "claude": [ClaudeCLIProvider.name],
    "auto": [ClaudeCLIProvider.name, GeminiProvider.name],
}

VALID_NAMES = tuple(_CLASSES) + tuple(_ALIASES)


def _build(name: str, **kwargs) -> AIProvider:
    return _CLASSES[name](**kwargs)


def resolve(
    name: Optional[str] = None, *, usage_recorder: Optional[UsageRecorder] = None
) -> AIProvider:
    """把名稱或別名解析成一個可用的供應商實例。

    name 為 None 時用 `CHATPULSE_AI_PROVIDER`（預設 `claude`）。
    名稱不合法時拋 INVALID_PARAMETER；別名底下全部不可用時，
    錯誤訊息會逐一列出各自的原因，而不是只說「沒有可用的供應商」——
    使用者需要知道的是「要設哪個環境變數」，不是「失敗了」。
    """
    requested = (name or cfg.AI_PROVIDER or "claude").strip().lower()

    if requested in _CLASSES:
        return _build(requested, usage_recorder=usage_recorder)

    if requested in _ALIASES:
        reasons = []
        for candidate in _ALIASES[requested]:
            provider = _build(candidate, usage_recorder=usage_recorder)
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
    "VALID_NAMES",
]
