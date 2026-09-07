"""Persona 來源註冊表。

比照 `core/providers/__init__.py`：對外只有「名稱 → 實例」與「列出所有來源」。
要加 `LocalPersonaSource`、`OtherRegistrySource` 就在 `_CLASSES` 加一行。

`url` 刻意是 `github` 的別名而不是獨立的類別——它們的安全邊界、
provenance 欄位與檔案格式假設完全一樣，差別只在「使用者給的是
結構化識別還是一個網址」，那是同一個 source 的兩個入口
（見 `GitHubPersonaSource.fetch`）。做成兩個類別會讓兩邊的
allowlist 有機會漂移，而漂移的那一邊就是 SSRF 的入口。
"""

from typing import Any, Dict, List, Optional

from ..errors import InvalidParameter
from .base import (
    ALLOWED_HOSTS,
    MAX_RESPONSE_BYTES,
    FetchedPersona,
    PersonaSource,
    safe_get,
)
from .github import GitHubPersonaSource
from .manual import ManualPersonaSource

_CLASSES = {
    GitHubPersonaSource.name: GitHubPersonaSource,
    ManualPersonaSource.name: ManualPersonaSource,
}

#: `source_type` 的別名。`url` 走 GitHub source 的網址入口。
_ALIASES = {"url": GitHubPersonaSource.name}

#: 合法 `source_type` 的單一事實來源。
VALID_SOURCE_TYPES = tuple(_CLASSES) + tuple(_ALIASES)


def resolve(source_type: Optional[str]) -> PersonaSource:
    """`source_type` → 來源實例。"""
    key = (source_type or "").strip().lower()
    if not key:
        raise InvalidParameter(
            f"source_type 是必填，只接受 {'／'.join(VALID_SOURCE_TYPES)}"
        )
    key = _ALIASES.get(key, key)
    cls = _CLASSES.get(key)
    if cls is None:
        raise InvalidParameter(
            f"source_type 只接受 {'／'.join(VALID_SOURCE_TYPES)}，收到 {source_type!r}"
        )
    return cls()


def describe_all() -> List[Dict[str, Any]]:
    """列出所有來源，給 API 與前端。"""
    return [
        {"name": cls.name, "label": cls.label}
        for cls in _CLASSES.values()
    ]


__all__ = [
    "ALLOWED_HOSTS",
    "MAX_RESPONSE_BYTES",
    "VALID_SOURCE_TYPES",
    "FetchedPersona",
    "GitHubPersonaSource",
    "ManualPersonaSource",
    "PersonaSource",
    "describe_all",
    "resolve",
    "safe_get",
]
