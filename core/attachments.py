"""把 Google Chat 訊息裡的圖片挑出來、縮好、交給 AI 供應商。

規格對應：SPECIFICATION.md 3.2（AI 供應商介面）、十（資料處理邊界）。

**設計重點**

1. **不是全抓。** 一輪摘要可能有 1000 則訊息；實測圖片成本與像素數成正比
   （約 750 像素／token），一張未縮圖的 1920×1080 就要 2,694 tokens——
   九張等於一整份 483 則對話的文字量。所以用 **token 預算**控管，不是張數：
   同樣「一張圖」在不同尺寸下差 4.2 倍，用張數控管沒有意義。

2. **優先序比預算更重要。** Draft Reply 最需要看到的是「被 @ 的那則訊息自己帶的圖」
   （實測工作群組最常見的形態就是「@某人 ＋ 一張截圖」）。那張圖**優先取**，
   不能因為同串前面有幾張雜圖就把預算吃光。

3. **位元組不落地。** 全程只在記憶體，不寫暫存檔——規格 3.2 的「對話全文不寫入
   資料庫」在圖片上同樣適用。

4. **失敗一律降級為佔位符，不中斷摘要。** 下載失敗、格式不支援、超出預算，
   都只是「這張圖 AI 看不到」，不該讓整份摘要失敗。略過的原因會回報給呼叫端，
   由它決定要不要顯示。
"""

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import config as cfg
from .providers.base import ImagePart

log = logging.getLogger("chatpulse.attachments")

#: 模型普遍支援的格式。其餘（例如 image/heic、image/svg+xml）一律不送，
#: 因為送了會被拒絕或靜默忽略，白付下載成本。
SUPPORTED_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


class ImageCandidate:
    """一個「可能可以送給 AI」的圖片附件。下載前的描述，不含位元組。"""

    __slots__ = ("resource_name", "content_type", "label", "message_name", "priority")

    def __init__(
        self,
        resource_name: str,
        content_type: str,
        label: str,
        message_name: str,
        priority: int = 1,
    ):
        self.resource_name = resource_name
        self.content_type = content_type
        self.label = label
        self.message_name = message_name
        #: 數字越小越優先。0 保留給「被 @ 的那則自己的圖」
        self.priority = priority


def find_candidates(
    messages: Sequence[Dict[str, Any]],
    *,
    priority_message_names: Sequence[str] = (),
) -> List[ImageCandidate]:
    """從訊息列表挑出可下載的圖片附件，依優先序排好。

    只收「Chat 上傳」的圖片：Drive 來源（只有 driveDataRef）要走 Drive API，
    本專案沒有那個 scope。實測 Drive 來源約佔全部附件的一成，
    這些會由 `chat_client.attachment_note()` 標成「無權讀取」讓 AI 知道漏了什麼。
    """
    priority_set = set(priority_message_names)
    out: List[ImageCandidate] = []

    for idx, m in enumerate(messages):
        msg_name = m.get("name") or ""
        for a in m.get("attachment") or []:
            ctype = (a.get("contentType") or "").lower()
            if ctype not in SUPPORTED_TYPES:
                continue
            resource = (a.get("attachmentDataRef") or {}).get("resourceName")
            if not resource:
                continue  # Drive 來源或結構異常，下載不了
            out.append(
                ImageCandidate(
                    resource_name=resource,
                    content_type=ctype,
                    label=a.get("contentName") or "未命名圖片",
                    message_name=msg_name,
                    # 被指定的訊息排最前；其餘越新越優先（messages 是由舊到新）
                    priority=0 if msg_name in priority_set else len(messages) - idx,
                )
            )

    out.sort(key=lambda c: c.priority)
    return out


def estimate_tokens(width: int, height: int) -> int:
    """依實測比例估算一張圖的 token 成本（約 750 像素／token）。"""
    return max(1, (width * height) // cfg.IMAGE_PIXELS_PER_TOKEN)


def shrink(data: bytes, content_type: str) -> Tuple[bytes, str, int]:
    """把圖縮到長邊不超過 IMAGE_MAX_EDGE，回傳 (位元組, media_type, 估算 token)。

    縮圖是**預設行為不是選項**：API 不會替你縮，1920×1080 會照全部像素計費。
    Pillow 讀不出來（損毀、格式怪）時原樣回傳並照原始大小估算——
    寧可多付一點 token，也不要因為縮圖失敗就把整張圖丟掉。
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            longest = max(width, height)
            if longest <= cfg.IMAGE_MAX_EDGE:
                return data, content_type, estimate_tokens(width, height)

            ratio = cfg.IMAGE_MAX_EDGE / longest
            new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            resized = im.convert("RGB").resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg", estimate_tokens(*new_size)
    except Exception as exc:  # noqa: BLE001 - 縮圖失敗不該讓摘要失敗
        log.warning("縮圖失敗，改用原圖：%s", exc)
        return data, content_type, estimate_tokens(cfg.IMAGE_MAX_EDGE, cfg.IMAGE_MAX_EDGE)


def collect(
    downloader: Callable[[str], bytes],
    messages: Sequence[Dict[str, Any]],
    *,
    space_id: str = "",
    budget_tokens: Optional[int] = None,
    max_count: Optional[int] = None,
    priority_message_names: Sequence[str] = (),
    scan_recent: Optional[int] = None,
) -> Tuple[List[ImagePart], List[str]]:
    """挑圖、下載、縮圖，回傳 (可送給 AI 的圖, 略過原因清單)。

    `downloader` 收 resourceName 回位元組（通常是
    `GoogleChatClient.download_attachment`）。傳函式而不是 client 是為了
    測試時好替換，也避免這個模組反向依賴 chat_client。

    略過原因是給人看的字串，呼叫端可以放進 SSE meta 或日誌。
    **有略過不是錯誤**——那些圖仍然以佔位符出現在對話文本裡。
    """
    if not cfg.IMAGE_ENABLED:
        return [], ["圖片功能已由設定停用（CHATPULSE_IMAGES=0）"]
    if space_id and space_id in cfg.IMAGE_EXCLUDED_SPACE_IDS:
        return [], [f"此聊天室已被列入圖片排除清單，只送文字佔位符"]

    budget = budget_tokens if budget_tokens is not None else cfg.IMAGE_BUDGET_TOKENS_SUMMARY
    limit = max_count if max_count is not None else cfg.IMAGE_MAX_COUNT
    scan = scan_recent if scan_recent is not None else cfg.IMAGE_SCAN_RECENT_MESSAGES

    # 只掃最近 N 則，但**被指定的優先訊息不受這個窗口限制**——
    # Draft Reply 要看的那則可能在更前面。
    priority_set = set(priority_message_names)
    windowed = list(messages[-scan:]) if scan and len(messages) > scan else list(messages)
    if priority_set:
        in_window = {m.get("name") for m in windowed}
        for m in messages:
            if m.get("name") in priority_set and m.get("name") not in in_window:
                windowed.append(m)

    candidates = find_candidates(windowed, priority_message_names=priority_message_names)
    if not candidates:
        return [], []

    skipped: List[str] = []
    if len(candidates) > limit:
        skipped.append(f"另有 {len(candidates) - limit} 張圖片超出單次張數上限（{limit}）未送出")
        candidates = candidates[:limit]

    def _fetch(c: ImageCandidate) -> Optional[Tuple[ImageCandidate, bytes]]:
        try:
            raw = downloader(c.resource_name)
        except Exception as exc:  # noqa: BLE001 - 單張失敗不影響其他張
            log.warning("附件下載失敗（%s）：%s", c.label, exc)
            skipped.append(f"{c.label}：下載失敗")
            return None
        if len(raw) > cfg.IMAGE_MAX_SOURCE_BYTES:
            skipped.append(f"{c.label}：原始檔過大（{len(raw) // 1024 // 1024} MB）")
            return None
        return c, raw

    workers = min(cfg.IMAGE_DOWNLOAD_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(_fetch, candidates))

    # 併發下載會打亂順序，重新按優先序排回來——送給模型的圖片順序要可預期
    ordered = [f for f in fetched if f]
    ordered.sort(key=lambda pair: pair[0].priority)

    parts: List[ImagePart] = []
    used = 0
    for c, raw in ordered:
        data, media_type, cost = shrink(raw, c.content_type)
        if parts and used + cost > budget:
            skipped.append(f"{c.label}：超出本次圖片 token 預算（{budget}）")
            continue
        parts.append(ImagePart(media_type=media_type, data=data, label=c.label))
        used += cost

    if parts:
        log.info(
            "圖片挑選完成：送出 %d 張、估算 %d tokens、略過 %d 項",
            len(parts),
            used,
            len(skipped),
        )
    return parts, skipped
