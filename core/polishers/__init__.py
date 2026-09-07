"""潤稿器：Draft Reply 產出後、落地前的最後一道文字處理。

對外只暴露這幾個名字，比照 `core/providers/__init__.py` 的做法——
呼叫端拿到的是「名稱 → 實例」與「列出所有選項」兩個入口，
不需要知道有哪些實作類別。

    polishers.resolve("sepia", provider=ai).polish(request)
    polishers.describe_all(provider=ai)

新增一個 polisher（GrammarPolisher、CompanyStylePolisher、TranslationPolisher）
只要在 `base.resolve()` 與 `base.describe_all()` 的名單裡加一行，
呼叫端不必改。
"""

from .base import (
    IntegrityReport,
    NoopPolisher,
    PolishRequest,
    PolishResult,
    ResponsePolisher,
    describe_all,
    extract_anchors,
    resolve,
    verify_integrity,
)

__all__ = [
    "IntegrityReport",
    "NoopPolisher",
    "PolishRequest",
    "PolishResult",
    "ResponsePolisher",
    "describe_all",
    "extract_anchors",
    "resolve",
    "verify_integrity",
]
