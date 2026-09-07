"""Draft Reply 的脈絡取得政策（docs/draft-context-design.md）。

放在 `core/` 與 `attachments.py`、`code_search.py` 同層：它跟它們一樣是
「取脈絡的政策」，不是 API 封裝（那是 `chat_client.py` 的職責）。

**這個模組存在的理由**

摘要工作台讀「最近 N 則」（扁平、跨 thread），草稿只撈「同一個 thread」。
私訊裡幾乎每則訊息各自成一個 thread，所以草稿只拿得到 1 則脈絡——
實測同一個私訊，摘要看到 10 則 4 個附件，草稿只有 1 則 1 個附件。

修法的三個地雷（前一輪都踩過）：

1. **根因不是「沒有 thread_name」。** Google Chat 的 Message 必然帶 thread，
   私訊走的是有 thread 的那一支，只是那個 thread 只有它自己。
   `server.py` 原本的 `else: thread_msgs = [mention_msg]` 是死碼，改它零效果。
2. **「thread 只有 1 則」是症狀不是成因。** 這個訊號同時涵蓋兩種語意相反的情況：
   私訊（周圍的扁平訊息**就是**同一段對話）與群組薄串（周圍是**別人的**討論串）。
   兩者不能共用一條規則，所以判準用 Space 的結構語意，不用 thread 長度。
3. **文字脈絡與圖片脈絡必須解耦。** `attachments.collect()` 原本吃的就是
   `thread_msgs`，放大文字會靜默放大圖片母體，而那一行 diff 上完全看不出改動。

**判準為什麼是「spaceType 或 spaceThreadingState」的聯集**

2026-09-06 對本帳號 436 個 Space 實測 `spaceThreadingState` 的分布：

    GROUP_CHAT     / THREADED_MESSAGES     180
    DIRECT_MESSAGE / THREADED_MESSAGES     131   ← 注意
    SPACE          / THREADED_MESSAGES      98
    SPACE          / UNTHREADED_MESSAGES    27

官方文件說 `UNTHREADED_MESSAGES` 涵蓋私訊，**但實際回傳不是**：私訊一律回報
`THREADED_MESSAGES`。所以只用 `spaceThreadingState` 當判準的話，私訊一則都修不到。
反過來只用 `spaceType == "DIRECT_MESSAGE"` 則會漏掉 27 個不分串的群組
（抽樣實測：30 則分屬 30 個 thread，確實是扁平的）。兩者取聯集才對。

GROUP_CHAT 走討論串路徑而不是扁平路徑：它沒有可靠的結構訊號（抽樣兩個，
一個 30 則/30 thread、一個 30 則/22 thread 且最長一串 8 則），判錯的代價不對稱——
把別串內容當成同一段對話，比少給脈絡貴。薄串時它仍會拿到帶警語的跨串小窗。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import config as cfg
from .chat_client import format_conversation, parse_rfc3339

#: 錨點那幾則在對話文本裡的前綴。模型要知道「要回的是哪一則」。
ANCHOR_MARK = "▶ "

#: 扁平空間（thread 不是對話單位）的 spaceThreadingState 值
FLAT_THREADING_STATES = ("UNTHREADED_MESSAGES",)


@dataclass
class ContextBlock:
    """送進 prompt 的一個脈絡區塊。

    `kind` 決定 prompt 怎麼介紹它，這是整個設計的重點：把「該 space 最近 20 則、
    分屬 6 個 thread」塞進標著「該討論串的完整對話」的區塊，模型會相信這些訊息
    是同一串的連續發言，於是把別串的結論當成本串的既定事實。那種錯誤比
    「明顯的資訊不足」更貴——它看起來很有脈絡。
    """

    kind: str  # "thread" | "flat_window" | "cross_thread"
    label: str  # 給 prompt 的中文標題
    messages: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    note: str = ""  # 區塊「之前」的說明句／警語

    @property
    def count(self) -> int:
        return len(self.messages)

    @property
    def time_range(self) -> Tuple[str, str]:
        return _time_range(self.messages)

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "note": self.note,
            "count": self.count,
            "time_range": self.time_range,
            "text": self.text,
        }


@dataclass
class DraftContext:
    """一次草稿所需的全部脈絡，含它自己的元資料。

    元資料（來源、範圍、錨點）必須一路帶到 prompt。退化成一個沒有標籤的字串，
    就是現狀那個「【該討論串的完整對話】底下只有一行」的假承諾。
    """

    mode: str  # "thread" | "flat_window" | "thread_thin"
    blocks: List[ContextBlock]
    anchor_run: List[Dict[str, Any]]
    #: 這次要回覆的「訊息」有幾則（收件匣多選合併時 > 1）。
    #: 注意這不是 len(anchor_run)——一個錨點可能是同一人的一串連發。
    anchor_count: int
    anchor_text: str  # 帶發話者與時間，給 prompt 的【被 @ 的訊息】
    anchor_plain_text: str  # 只有內文，給 code_search 抽關鍵字
    image_messages: List[Dict[str, Any]]
    coverage: str  # "full"（錨點在取得範圍內）| "partial"
    space_type_label: str

    @property
    def message_count(self) -> int:
        return sum(b.count for b in self.blocks)

    @property
    def time_range(self) -> Tuple[str, str]:
        return _time_range([m for b in self.blocks for m in b.messages])

    @property
    def search_text(self) -> str:
        """給 `code_search.extract_search_terms()` 的次要語料。

        **刻意排除 cross_thread**：那是別的討論串的內容，拿它抽出來的關鍵字
        會讓程式碼搜尋往錯的方向查，而使用者只會看到一排看不懂的搜尋詞。
        """
        return "\n".join(b.text for b in self.blocks if b.kind != "cross_thread")

    def to_meta(self) -> Dict[str, Any]:
        """給 SSE meta 事件。這是使用者判斷這份草稿可不可信的唯一依據。"""
        start, end = self.time_range
        return {
            "mode": self.mode,
            "message_count": self.message_count,
            "anchor_count": self.anchor_count,
            "coverage": self.coverage,
            "time_range": {"start": start, "end": end},
            "blocks": [
                {"kind": b.kind, "label": b.label, "count": b.count} for b in self.blocks
            ],
        }


# ---------------------------------------------------------------------------
# 判準
# ---------------------------------------------------------------------------


def is_flat_space(space_type: Optional[str], threading_state: Optional[str]) -> bool:
    """這個 Space 的 thread 是不是對話單位？

    回 True 代表「不是」——扁平的時間序列本身才是對話，該用錨點前後窗。
    兩個條件取聯集的理由見模組 docstring 的實測分布表。
    """
    if (threading_state or "").strip().upper() in FLAT_THREADING_STATES:
        return True
    return (space_type or "").strip().upper() == "DIRECT_MESSAGE"


def space_type_label(space_type: Optional[str], threading_state: Optional[str]) -> str:
    """給 prompt 用的中文說明。模型要知道自己在讀哪種形態的聊天室。"""
    stype = (space_type or "").strip().upper()
    if stype == "DIRECT_MESSAGE":
        return "一對一私訊"
    if (threading_state or "").strip().upper() in FLAT_THREADING_STATES:
        return "不分討論串的聊天室"
    if stype == "GROUP_CHAT":
        return "多人群組"
    return "聊天室"


# ---------------------------------------------------------------------------
# 內部工具
# ---------------------------------------------------------------------------


def _created(msg: Dict[str, Any]) -> str:
    return msg.get("createTime") or ""


def _created_dt(msg: Dict[str, Any]) -> Optional[datetime]:
    return parse_rfc3339(_created(msg))


def _time_range(messages: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    stamps = sorted(_created(m) for m in messages if _created(m))
    if not stamps:
        return "", ""
    return stamps[0], stamps[-1]


def _sender(msg: Dict[str, Any]) -> str:
    return (msg.get("sender") or {}).get("name") or ""


def _dedup_sorted(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """依 message name 去重並按 createTime 由舊到新排序。

    順序不是美觀問題：`attachments.find_candidates()` 用清單索引算「越新越優先」
    （`len(messages) - idx`），順序亂掉會讓圖片優先序整個錯亂。
    """
    seen = set()
    out: List[Dict[str, Any]] = []
    for m in messages:
        key = m.get("name")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(m)
    out.sort(key=_created)
    return out


def _index_of(messages: Sequence[Dict[str, Any]], name: str) -> Optional[int]:
    for i, m in enumerate(messages):
        if m.get("name") == name:
            return i
    return None


def format_with_anchor(
    messages: Sequence[Dict[str, Any]],
    resolve: Optional[Callable[[Optional[str]], str]],
    anchor_names: Sequence[str] = (),
) -> str:
    """組對話文本，錨點那幾行前面加 ▶。

    逐則呼叫 `format_conversation()` 而不是自己拼字串，是為了保證格式與摘要
    完全一致——`format_conversation` 會過濾「沒有文字也沒有附件」的訊息，
    自己拼就會漏掉那個規則，而那種差異只有在真實資料上才看得出來。
    """
    marks = set(anchor_names)
    lines: List[str] = []
    for m in messages:
        line = format_conversation([m], resolve)
        if not line:
            continue
        lines.append(ANCHOR_MARK + line if m.get("name") in marks else line)
    return "\n".join(lines)


def _collect_anchor_run(
    window: Sequence[Dict[str, Any]], idx: int, self_user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """從錨點往前後收攏成「這個人講了但我還沒回的那一段」。

    **判準是「我回了沒」，不是「隔多久」。** 一開始這裡用「同一人 ＋ 間隔 < 5 分鐘」
    收攏，理由是私訊常把一個問題拆三則發（「你好」「想問一下 X」「方便的話今天回我」）。
    但 2026-09-07 實測踩到反例：對方 11:33 問白名單、12:32 問 LINE 推播，
    相隔 **59 分鐘**，於是只有後面那則被標成要回的，前面那則掉進背景脈絡——
    草稿就只回了一半，另一半被寫成「我另外看，確認完再回你」。
    間隔多久跟「這則我回了沒」根本沒有關係。

    收攏規則（純結構，不做任何相關性評分——那會踩到 ADR-0003 的界線）：
      * 只收**與錨點同一個發話者**的訊息。碰到別人講話（包含我自己）就停：
        我講過話代表前面那些已經回過了；別人講話在群組裡是另一個人的事。
      * 前後都收。錨點是「最後一則不是自己發的」時往前收；錨點是真的 Mention 時，
        對方可能在 @ 完之後又補了幾句，那些也要收。
      * 受 48h 上界限制（同 `DRAFT_WINDOW_HOURS`）——三天前那則沒回的，
        現在硬要一起回反而奇怪。

    `self_user_id` 為 None 時退回舊行為（只往前、用間隔判斷），
    因為「碰到我自己就停」這條規則沒有 self_user_id 就無從判斷。
    """
    anchor = window[idx]
    sender = _sender(anchor)
    if not sender:
        return [anchor]

    if not self_user_id:
        gap = timedelta(minutes=cfg.DRAFT_ANCHOR_RUN_GAP_MINUTES)
        start = idx
        while start > 0:
            prev, cur = window[start - 1], window[start]
            if _sender(prev) != sender:
                break
            t_prev, t_cur = _created_dt(prev), _created_dt(cur)
            if t_prev is None or t_cur is None or (t_cur - t_prev) > gap:
                break
            start -= 1
        return list(window[start : idx + 1])

    anchor_time = _created_dt(anchor)
    limit = timedelta(hours=cfg.DRAFT_WINDOW_HOURS)

    def in_window(m: Dict[str, Any]) -> bool:
        if anchor_time is None:
            return True
        t = _created_dt(m)
        return t is None or abs(t - anchor_time) <= limit

    start = idx
    while start > 0 and _sender(window[start - 1]) == sender and in_window(window[start - 1]):
        start -= 1
    end = idx
    while (
        end + 1 < len(window)
        and _sender(window[end + 1]) == sender
        and in_window(window[end + 1])
    ):
        end += 1
    return list(window[start : end + 1])


def cluster_messages(messages: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """把一段連發切成「幾個問題」：同一人、間隔夠短的算同一群。

    這是 `DRAFT_ANCHOR_RUN_GAP_MINUTES` 現在唯一的用途。它決定 prompt 要不要
    切換成「這是 N 個各自獨立的問題，要一則回話全部回完」的模式——
    「你好／想問 X／今天回我」是一個問題，「白名單」與「LINE 推播」是兩個。
    """
    gap = timedelta(minutes=cfg.DRAFT_ANCHOR_RUN_GAP_MINUTES)
    groups: List[List[Dict[str, Any]]] = []
    for m in messages:
        if groups:
            prev = groups[-1][-1]
            t_prev, t_cur = _created_dt(prev), _created_dt(m)
            same = _sender(prev) == _sender(m)
            close = t_prev is not None and t_cur is not None and (t_cur - t_prev) <= gap
            if same and close:
                groups[-1].append(m)
                continue
        groups.append([m])
    return groups


def _apply_time_bound(
    messages: Sequence[Dict[str, Any]],
    anchor_time: Optional[datetime],
    hours: int,
    *,
    min_keep: int = 0,
    side: str = "before",
) -> List[Dict[str, Any]]:
    """則數窗與時間窗取交集，但保證至少留下 `min_keep` 則。

    時間上界會切掉「隔很久重問同一件事」的脈絡（設計文件 T-1），這是已知取捨：
    沒有上界時，一個月前的閒聊會以完全相同的格式混在脈絡裡，模型分不出新舊，
    會拿舊結論當現況。**錯的脈絡比缺的脈絡更貴**——缺的時候模型至少可能說
    「資訊不足」。若實測發現 T-1 常發生，調大 DRAFT_WINDOW_HOURS，不要拿掉。
    """
    items = list(messages)
    if anchor_time is None or not items:
        return items
    delta = timedelta(hours=hours)
    if side == "before":
        kept = [m for m in items if (_created_dt(m) or anchor_time) >= anchor_time - delta]
        if len(kept) < min_keep:
            kept = items[-min_keep:] if min_keep else kept
    else:
        kept = [m for m in items if (_created_dt(m) or anchor_time) <= anchor_time + delta]
        if len(kept) < min_keep:
            kept = items[:min_keep] if min_keep else kept
    return kept


def _load_window(
    client: Any, space_id: str, anchor_msg: Dict[str, Any], fetch: int
) -> Tuple[List[Dict[str, Any]], int, str]:
    """撈一段包含錨點的訊息窗，回傳 (窗, 錨點索引, coverage)。

    第一層用已驗證的 `fetch_recent_messages()`。錨點比最近 N 則還舊時
    （收件匣積壓、很久以前的私訊）改用時間錨定——只用實測可行的
    `createTime >`，不依賴 `createTime <`（那個沒有實測過）。

    無論如何都把錨點併進窗裡：錨點一定存在於這個 Space，缺了它整個切片就沒有基準。
    """
    window = client.fetch_recent_messages(space_id, limit=fetch)
    window = _dedup_sorted(window)
    anchor_name = anchor_msg.get("name") or ""
    idx = _index_of(window, anchor_name)
    if idx is not None:
        return window, idx, "full"

    anchor_time = _created_dt(anchor_msg)
    if anchor_time is not None:
        try:
            near = client.list_messages_since(
                space_id,
                anchor_time - timedelta(hours=cfg.DRAFT_WINDOW_HOURS),
                max_messages=fetch,
            )
        except Exception:  # noqa: BLE001 - 取不到就退回只有錨點，不要讓草稿整個失敗
            near = []
        near = _dedup_sorted(near)
        idx = _index_of(near, anchor_name)
        if idx is not None:
            return near, idx, "full"
        merged = _dedup_sorted(list(near) + [anchor_msg])
        merged_idx = _index_of(merged, anchor_name)
        return merged, merged_idx if merged_idx is not None else 0, "partial"

    return [anchor_msg], 0, "partial"


# ---------------------------------------------------------------------------
# 主要進入點
# ---------------------------------------------------------------------------


def _apply_hook(
    hook: Optional[Callable[[List[Dict[str, Any]]], Any]],
    messages: List[Dict[str, Any]],
    resolve: Optional[Callable[[Optional[str]], str]],
) -> Optional[Callable[[Optional[str]], str]]:
    """跑「取回後、組文字前」的鉤子，鉤子回傳可呼叫物就換掉 resolver。"""
    if hook is None:
        return resolve
    replacement = hook(messages)
    return replacement if callable(replacement) else resolve


def build(
    client: Any,
    *,
    space_id: str,
    space_type: Optional[str],
    threading_state: Optional[str],
    anchor_msg: Dict[str, Any],
    extra_anchor_msgs: Sequence[Dict[str, Any]] = (),
    thread_name: Optional[str] = None,
    resolve: Optional[Callable[[Optional[str]], str]] = None,
    on_retrieved: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    self_user_id: Optional[str] = None,
) -> DraftContext:
    """依 Space 的結構語意決定脈絡的形狀。

    `extra_anchor_msgs` 是「一起回」的其他錨點（收件匣多選合併）。同一個人在同一個
    對話裡連問兩件事時，分兩次產草稿會得到兩份各自正確、但要分兩次送出的回話。
    多錨點讓模型知道「這幾則要用**一則**回話一次回完」。
    呼叫端必須先驗證所有錨點屬於同一個 Space（群組還要同一個討論串）——
    回話只能送到一個 thread，跨串合併會讓其中一則的提問者看不到回覆。

    `client` 只需要三個方法：`fetch_recent_messages`、`list_messages_since`、
    `list_thread_messages`。傳 client 而不是 GoogleChatClient 型別是為了讓
    單元測試能餵假的訊息列表，不打 API。

    `self_user_id` 是 Viewer 自己的 `users/{id}`。有給的話，「要回哪幾則」的判準
    會從「間隔夠短的連發」升級成「**從我上次發言到現在，對方講了什麼我還沒回**」
    （見 `_collect_anchor_run`）。沒給就退回舊行為。

    `on_retrieved` 是「訊息都取回來了、但還沒組成文字」這個時間點的鉤子。
    存在的理由很具體：Google 在使用者驗證下不回傳 `sender.displayName`，
    名字要從這批訊息的 mention annotation 現學（`core/directory.py`）。
    學名字必須發生在組文字**之前**，否則發言者全是「未知成員」。
    鉤子回傳新的 resolver 就換用新的；回傳 None 就沿用原本那個。
    （不這樣做的話呼叫端只能「先 build 一次學名字、再 build 一次組文字」，
    那會讓每產一份草稿都多打一輪 Google API。）

    三種 mode：
      * `flat_window` —— 私訊／不分串群組。thread 不是對話單位，
        取錨點前後窗（前 15 後 10 ＋ 48h 上界 ＋ 6 則保底）。
      * `thread` —— 群組長串。**維持現狀**，這是最常走的路徑，任何改動都是純風險。
      * `thread_thin` —— 群組薄串（有人 @ 你但還沒人回）。原串 ＋ 一個帶警語的
        跨串小窗，兩者是**獨立區塊**。
    """
    label = space_type_label(space_type, threading_state)
    anchors = _dedup_sorted([anchor_msg, *extra_anchor_msgs])

    if is_flat_space(space_type, threading_state):
        return _build_flat(
            client, space_id, anchors, resolve, label, on_retrieved, self_user_id
        )
    return _build_threaded(
        client, space_id, anchors, thread_name, resolve, label, on_retrieved, self_user_id
    )


def _run_span(
    window: Sequence[Dict[str, Any]], idx: int, self_user_id: Optional[str]
) -> Tuple[int, int]:
    """錨點所屬「未回覆連發」在 window 裡的 [起, 迄] 索引（含兩端）。"""
    run = _collect_anchor_run(window, idx, self_user_id)
    names = {m.get("name") for m in run}
    idxs = [i for i, m in enumerate(window) if m.get("name") in names]
    return (min(idxs), max(idxs)) if idxs else (idx, idx)


def _cap_clusters(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只保留最近的 N 個「問題」（同一群連發算一個）。

    對方連續丟了七八個不相干的問題時，一則回話全部回完就不像人話了。
    超出的較舊問題**仍然看得到**（它們還在脈絡裡），只是不標成「要回的」。
    """
    groups = cluster_messages(messages)
    if len(groups) <= cfg.DRAFT_ANCHOR_MAX_CLUSTERS:
        return messages
    kept = groups[-cfg.DRAFT_ANCHOR_MAX_CLUSTERS :]
    return [m for g in kept for m in g]


def _build_flat(
    client: Any,
    space_id: str,
    anchors: List[Dict[str, Any]],
    resolve: Optional[Callable[[Optional[str]], str]],
    label: str,
    on_retrieved: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    self_user_id: Optional[str] = None,
) -> DraftContext:
    window, _, coverage = _load_window(
        client, space_id, anchors[0], cfg.DRAFT_WINDOW_FETCH
    )
    # 其餘錨點可能落在窗外（例如兩則相隔很久），一律併進來再重算索引
    if len(anchors) > 1:
        window = _dedup_sorted(list(window) + anchors)
    idxs = sorted(
        i
        for i in (_index_of(window, a.get("name") or "") for a in anchors)
        if i is not None
    )
    if not idxs:  # 理論上不會發生（_load_window 保證錨點在窗裡）
        window, idxs = _dedup_sorted(anchors), list(range(len(anchors)))
        coverage = "partial"

    # 每個錨點各自往前後收攏成「這個人講了但我還沒回的那一段」，再取聯集。
    spans = [_run_span(window, i, self_user_id) for i in idxs]
    marked = {
        window[j].get("name")
        for lo, hi in spans
        for j in range(lo, hi + 1)
        if window[j].get("name")
    }
    # 超過 N 個問題就只標最近的幾個——較舊的仍然看得到，只是不標成「要回的」
    capped = _cap_clusters([m for m in window if m.get("name") in marked])
    anchor_names = {m.get("name") for m in capped}
    anchor_idxs = [i for i, m in enumerate(window) if m.get("name") in anchor_names]

    # core＝從最早那則要回的，到最晚那則為止。中間夾著的訊息（常見的是自己
    # 先前的回覆）也要留著——那是這幾則問題之間的來龍去脈。
    first_idx = min(anchor_idxs) if anchor_idxs else min(idxs)
    last_idx = max(anchor_idxs) if anchor_idxs else max(idxs)
    core = list(window[first_idx : last_idx + 1])
    anchor_run = [m for m in core if m.get("name") in anchor_names]
    cluster_count = len(cluster_messages(anchor_run))

    before = list(window[max(0, first_idx - cfg.DRAFT_CTX_BEFORE) : first_idx])
    after = list(window[last_idx + 1 : last_idx + 1 + cfg.DRAFT_CTX_AFTER])

    before = _apply_time_bound(
        before,
        _created_dt(window[first_idx]),
        cfg.DRAFT_WINDOW_HOURS,
        min_keep=cfg.DRAFT_CTX_MIN_BEFORE,
        side="before",
    )
    # after 沒有保底：它的用途是「偵測已經有人回答了」，一個月後的訊息不是這件事的答案
    after = _apply_time_bound(
        after, _created_dt(window[last_idx]), cfg.DRAFT_WINDOW_HOURS, min_keep=0, side="after"
    )

    messages = before + core + after
    resolve = _apply_hook(on_retrieved, list(messages), resolve)
    image_before = before[-cfg.DRAFT_IMAGE_BEFORE :] if cfg.DRAFT_IMAGE_BEFORE else []
    subject = "這幾則" if cluster_count > 1 else "該則"
    block = ContextBlock(
        kind="flat_window",
        label=f"這個{label}在{subject}前後的連續對話"
        f"（前 {len(before)} 則、後 {len(after)} 則）",
        messages=messages,
        text=format_with_anchor(messages, resolve, anchor_names),
        note=f"這個{label}沒有討論串結構，**這個時間序列本身就是對話**。",
    )

    return DraftContext(
        mode="flat_window",
        blocks=[block],
        anchor_run=anchor_run,
        anchor_count=cluster_count,
        anchor_text=format_with_anchor(anchor_run, resolve),
        anchor_plain_text=_plain_text(anchor_run),
        # 用 core 而不是 anchor_run：多錨點時夾在中間的訊息也可能帶圖。
        # 單錨點時 core 就等於 anchor_run，行為完全相同。
        # 排除 after：錨點之後的圖多半是別人回答時貼的，給模型看等於誘導它抄
        # 別人的答案，而使用者要的是自己的回話。那些訊息的**文字**仍在脈絡裡。
        image_messages=_dedup_sorted(core + image_before),
        coverage=coverage,
        space_type_label=label,
    )


def _build_threaded(
    client: Any,
    space_id: str,
    anchors: List[Dict[str, Any]],
    thread_name: Optional[str],
    resolve: Optional[Callable[[Optional[str]], str]],
    label: str,
    on_retrieved: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    self_user_id: Optional[str] = None,
) -> DraftContext:
    anchor_msg = anchors[0]
    if thread_name:
        thread_msgs = client.list_thread_messages(
            space_id, thread_name, limit=cfg.DRAFT_THREAD_LIMIT
        )
    else:
        # Google Chat 的 Message 必然帶 thread，這裡實務上不會執行；
        # 留著是因為「Google 破例不回」的成本是整個草稿失敗。
        thread_msgs = list(anchors)
    thread_msgs = _dedup_sorted(thread_msgs)
    missing = [a for a in anchors if _index_of(thread_msgs, a.get("name") or "") is None]
    if missing:
        thread_msgs = _dedup_sorted(list(thread_msgs) + missing)

    # 薄串的跨串小窗要在學名字之前取回來，否則那個區塊的發言者會全是「未知成員」
    cross: List[Dict[str, Any]] = []
    thin = len(thread_msgs) < 2
    if thin and cfg.DRAFT_CROSS_THREAD_ENABLED and cfg.DRAFT_CROSS_BEFORE > 0:
        cross = _cross_thread_messages(client, space_id, anchor_msg, thread_msgs)

    resolve = _apply_hook(on_retrieved, list(thread_msgs) + list(cross), resolve)

    # 同一個人在這一串裡連問了幾句、而我一句都還沒回時，那幾句都要標成「要回的」。
    # 判準與 flat_window 完全一樣（見 _collect_anchor_run）——只是這裡的搜尋範圍
    # 是這一串，不是時間窗。撈回來的訊息一則都沒變，變的只有「哪幾則標 ▶」。
    marked = set()
    for a in anchors:
        i = _index_of(thread_msgs, a.get("name") or "")
        if i is None:
            if a.get("name"):
                marked.add(a["name"])
            continue
        lo, hi = _run_span(thread_msgs, i, self_user_id)
        marked.update(
            thread_msgs[j].get("name") for j in range(lo, hi + 1) if thread_msgs[j].get("name")
        )
    anchor_run = _cap_clusters([m for m in thread_msgs if m.get("name") in marked])
    if not anchor_run:
        anchor_run = list(anchors)
    anchor_names = [m.get("name") for m in anchor_run if m.get("name")]
    cluster_count = len(cluster_messages(anchor_run))

    thread_block = ContextBlock(
        kind="thread",
        label=f"該討論串的完整對話（共 {len(thread_msgs)} 則）",
        messages=thread_msgs,
        text=format_with_anchor(thread_msgs, resolve, anchor_names),
    )

    if not thin:
        return DraftContext(
            mode="thread",
            blocks=[thread_block],
            anchor_run=anchor_run,
            anchor_count=cluster_count,
            anchor_text=format_with_anchor(anchor_run, resolve),
            anchor_plain_text=_plain_text(anchor_run),
            image_messages=thread_msgs,
            coverage="full",
            space_type_label=label,
        )

    # 薄串：有人 @ 你但還沒人回。這種訊息（「@你 這個怎麼辦」）不可能自足，
    # 但周圍的訊息是**別人的討論串**，語意與私訊完全相反——所以給的是
    # 獨立區塊 ＋ 警語 ＋ 更小的窗，不能跟私訊走同一條路徑。
    blocks = [thread_block]
    if cross:
        blocks.append(
            ContextBlock(
                kind="cross_thread",
                label=f"⚠️ 同一聊天室其他討論串的近期訊息（共 {len(cross)} 則）",
                messages=cross,
                text=format_with_anchor(cross, resolve),
                note=(
                    "這些訊息**不屬於**你要回覆的那一串，只是時間相近的其他討論。"
                    "僅供理解背景，**不可**當成本串已經談定的結論，也不要在回話中引用。"
                ),
            )
        )

    return DraftContext(
        mode="thread_thin",
        blocks=blocks,
        anchor_run=anchor_run,
        anchor_count=cluster_count,
        anchor_text=format_with_anchor(anchor_run, resolve),
        anchor_plain_text=_plain_text(anchor_run),
        # 排除 cross_thread：別串的截圖幾乎必然不相關，而 attachments 依
        # 「越新越優先」排序，一張較新的無關圖會排在同串較舊的相關圖前面，
        # 把 8 張／8000 tokens 吃掉。priority_message_names 只保障第 1 張。
        image_messages=thread_msgs,
        coverage="full",
        space_type_label=label,
    )


def _cross_thread_messages(
    client: Any,
    space_id: str,
    anchor_msg: Dict[str, Any],
    thread_msgs: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """薄串時的跨串小窗：錨點之前的其他討論串訊息，最多 DRAFT_CROSS_BEFORE 則。

    只往前取。錨點之後的別串訊息對「這串該怎麼回」沒有幫助，只會增加噪音。
    """
    try:
        window = client.fetch_recent_messages(space_id, limit=cfg.DRAFT_CROSS_FETCH)
    except Exception:  # noqa: BLE001 - 這是補充脈絡，取不到不該讓草稿失敗
        return []
    window = _dedup_sorted(window)
    exclude = {m.get("name") for m in thread_msgs}
    anchor_time = _created_dt(anchor_msg)
    anchor_created = _created(anchor_msg)

    out: List[Dict[str, Any]] = []
    for m in window:
        if m.get("name") in exclude:
            continue
        if anchor_created and _created(m) >= anchor_created:
            continue
        out.append(m)
    out = _apply_time_bound(
        out, anchor_time, cfg.DRAFT_WINDOW_HOURS, min_keep=0, side="before"
    )
    return out[-cfg.DRAFT_CROSS_BEFORE :]


def _plain_text(messages: Sequence[Dict[str, Any]]) -> str:
    """只取內文，不帶發話者與時間戳。

    給 `code_search.extract_search_terms()` 用：它抽的是看起來像識別字的 token，
    餵進 `[2026-09-06 14:33] 王小明:` 這種前綴只會多出一堆垃圾詞。
    """
    parts = [(m.get("text") or "").strip() for m in messages]
    return "\n".join(p for p in parts if p)
