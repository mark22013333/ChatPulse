"""ResponsePolisher：Draft Reply 產出後、落地前的最後一道文字處理。

## 為什麼這一層要獨立存在

潤稿與「產生草稿」是兩件不同的事，混在一起會同時弄壞兩者：

* 產生草稿要的是**忠於證據**——prompt 裡塞滿脈絡、程式碼、參考聊天室，
  規則的重心是「不要臆測」。
* 潤稿要的是**忠於原文**——它不看證據，只看已經寫好的那段回話，
  規則的重心是「不要改掉事實、不要倒填充進去」。

把潤稿規則加進 `draft_reply_prompt` 會讓一份 prompt 同時追兩個目標，
而且沒有辦法驗證「潤稿有沒有改掉事實」——因為沒有「潤稿前」可以比對。
所以這裡的設計是：**產生完 → 拿產出當輸入 → 再過一次 → 比對前後**。

## 這一層刻意不做的事

* **不擴張 `AIProvider` 的介面。** polisher 透過既有的 `generate()` 呼叫模型
  （`stream_text()` 也可以，但潤稿沒有逐字顯示的需求）。provider 仍然只有
  「產生文字」與「串流產生文字」兩個能力，沒有變成 agent runtime。
* **不打開任何工具、MCP 或 skill 載入。** Sepia 的規則是以**文字**形式
  進 prompt 的，不是靠 CLI 去載入 skill——那會需要拆掉 `claude_cli` 的
  `--restricted` / `--disallowedTools` / `--strict-mcp-config` / 暫存 cwd，
  而那些是成本與隔離的承重牆（見 ADR-0007）。
* **不決定要不要潤稿。** 那是呼叫端依 `sepia_enabled` 決定的。

## 完整性驗證是這一層的重點，不是加分項

潤稿是「讓一個模型改寫另一個模型寫的事實陳述」。它會壞在一個非常具體的
地方：**把數字順順地改掉**。「timeout 是 30 秒」潤成「timeout 大約半分鐘」
讀起來更自然，但那是錯的答案；「TimeoutConfig.java:88」潤成
「TimeoutConfig.java」讀起來更順，但對方就打不開來對了。

這種錯誤不會有例外、不會有警告，而且**潤稿後的版本讀起來比原版更可信**。
所以 `verify_integrity()` 不是選項：潤稿結果必須通過錨點比對才會被採用，
沒通過就退回未潤稿的版本並在 UI 標示（見 `PolishResult.fallback_reason`）。
「潤稿改壞了所以退回」與「Sepia 根本不可用」是兩種不同的情況，
前者退回、後者報錯（`SepiaUnavailable`），不可混為一談。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 錨點類別。分類的意義在於**容忍度不同**（見 `_STRICT_KINDS`）。
_ANCHOR_KINDS = (
    "url",
    "commit_sha",
    "file_line",
    "date",
    "time",
    "number",
    "path",
    "code_symbol",
)

#: 零容忍的類別：這些東西遺失或被發明，一律判定潤稿失敗。
#:
#: `number` 在裡面，因為改數字正是這一層要擋的主要失敗形態。
#: 用**集合**比對而不是計次，所以「同一個數字在原文出現兩次、潤稿後只留一次」
#: 不會被誤判——那是正常的刪除重複，不是改掉事實。
_STRICT_KINDS = ("url", "commit_sha", "file_line", "date", "number")

#: 只記錄、不判定失敗的類別。
#:
#: `path` 與 `code_symbol` 的邊界本來就模糊：模型可能把 `TimeoutConfig.java`
#: 寫成 `TimeoutConfig.java:88`（更精確）、把 `` `retry()` `` 的反引號去掉
#: （格式變了、事實沒變）。把它們列為嚴格會讓正常潤稿被大量誤退，
#: 而誤退的代價是「這個功能看起來壞掉」。`time` 同理（「30 秒」的 30 已由
#: `number` 守住，`time` 只是輔助資訊）。
_SOFT_KINDS = tuple(k for k in _ANCHOR_KINDS if k not in _STRICT_KINDS)


# 抽取順序有意義：先抽結構完整的（URL、commit、file:line、日期），
# 抽到就把該段文字遮蔽掉，再往下抽較零散的（數字）。
# 不遮蔽的話 `2026-09-07` 會同時被 date 與 number 抽到，
# 而 `TimeoutConfig.java:88` 的 88 會變成一個獨立的數字錨點——
# 於是「把 file:line 整個刪掉」會同時報 file_line 與 number 遺失，
# 錯誤訊息變得無法判讀。
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("url", re.compile(r"https?://[^\s<>()\[\]，。、；：！？「」『』]+", re.IGNORECASE)),
    # commit SHA：7–40 位十六進位，且前後不是英數字（避免抓到 UUID 片段或
    # 一長串英文字裡剛好都是 abcdef 的部分）。要求至少一個數字，
    # 否則 "deadbeef" 這種純字母的英文單字會被當成 SHA。
    (
        "commit_sha",
        re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![0-9A-Za-z])"),
    ),
    # 檔案:行號（含 `:88` 與 `:88-92` 兩種），這是 _CODE_RULES 要求的形態
    (
        "file_line",
        re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,12}:\d+(?:-\d+)?"),
    ),
    ("date", re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[/-]\d{1,2}(?![\d/-])")),
    ("time", re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")),
    # 反引號包住的東西是 markdown 的 code span，內容是識別字
    ("code_symbol", re.compile(r"`([^`\n]{1,80})`")),
    # 路徑：有副檔名或有斜線的 token
    (
        "path",
        re.compile(r"(?<![\w/])(?:[\w-]+/)+[\w.-]+|[\w-]+\.(?:py|ts|tsx|js|jsx|java|kt|go|rs|rb|php|c|h|cpp|cs|sql|md|json|yaml|yml|toml|ini|sh|xml|html|css)\b"),
    ),
    # 數字最後抽：整數、小數、含千分位。附帶的單位不進錨點
    # （「30 秒」與「30秒」要視為同一件事，否則排版差異會造成假失敗）。
    ("number", re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])")),
)

#: 問句符號。潤稿把「問回去」刪掉是一種特別需要擋的失敗——
#: `_BASE_RULES` 與 Sepia 的 density 檢查都明確要求「資料不足要問回去」，
#: 而潤稿的天性是刪東西，最容易刪掉的就是結尾那句提問。
_QUESTION_MARKS = ("?", "？")


@dataclass(frozen=True)
class IntegrityReport:
    """潤稿前後的錨點比對結果。"""

    ok: bool
    #: 原文有、潤稿後不見了。key 是錨點類別。
    lost: Dict[str, List[str]] = field(default_factory=dict)
    #: 潤稿後多出來、原文沒有的。**發明事實比遺失更危險。**
    invented: Dict[str, List[str]] = field(default_factory=dict)
    #: 原文有問句、潤稿後一個問號都沒有。
    lost_question: bool = False
    #: 只記錄不判定失敗的差異（`_SOFT_KINDS`），供 log 與 debug。
    soft_diff: Dict[str, List[str]] = field(default_factory=dict)

    def reason(self) -> str:
        """給人看的一句話理由。空字串代表通過。

        寫成「哪一類、哪幾個值」而不是「驗證失敗」——後者會讓人無法判斷
        是模型真的改壞了，還是我們的抽取規則誤判。
        """
        parts: List[str] = []
        for kind, values in sorted(self.invented.items()):
            parts.append(f"新增了原文沒有的{_kind_label(kind)}：{'、'.join(values[:5])}")
        for kind, values in sorted(self.lost.items()):
            parts.append(f"遺失了原文的{_kind_label(kind)}：{'、'.join(values[:5])}")
        if self.lost_question:
            parts.append("原文有問回去的句子，潤稿後問號消失了")
        return "；".join(parts)


def _kind_label(kind: str) -> str:
    return {
        "url": "連結",
        "commit_sha": "commit SHA",
        "file_line": "檔案行號",
        "date": "日期",
        "time": "時間",
        "number": "數字",
        "path": "檔案路徑",
        "code_symbol": "程式碼識別字",
    }.get(kind, kind)


def extract_anchors(text: str) -> Dict[str, List[str]]:
    """抽出一段文字裡的事實錨點。

    回傳每個類別的**排序後去重清單**（不是計次）。用集合語意的理由見
    `_STRICT_KINDS` 的註解：刪掉重複提到的同一個數字是合理的潤稿動作。
    """
    remaining = text or ""
    found: Dict[str, List[str]] = {}

    for kind, pattern in _PATTERNS:
        values: List[str] = []
        spans: List[Tuple[int, int]] = []
        for match in pattern.finditer(remaining):
            # code_symbol 有 capture group，取內容不取反引號
            value = match.group(1) if pattern.groups else match.group(0)
            value = (value or "").strip()
            if value:
                values.append(value)
            spans.append(match.span())
        if values:
            found[kind] = sorted(set(values))
        # 遮蔽已匹配的區段（用空白保持長度，避免相鄰 token 黏在一起）
        if spans:
            chars = list(remaining)
            for start, end in spans:
                for i in range(start, end):
                    chars[i] = " "
            remaining = "".join(chars)

    return found


def verify_integrity(base: str, polished: str) -> IntegrityReport:
    """比對潤稿前後的事實錨點。

    判準（任一成立即失敗）：
      1. `_STRICT_KINDS` 有任何**新增**——模型發明了原文沒有的具體資訊
      2. `_STRICT_KINDS` 有任何**遺失**——模型刪掉了原文的具體資訊
      3. 原文有問號、潤稿後完全沒有——把「問回去」潤掉了

    `_SOFT_KINDS` 的差異只記錄在 `soft_diff`，不影響 `ok`。
    """
    before = extract_anchors(base)
    after = extract_anchors(polished)

    lost: Dict[str, List[str]] = {}
    invented: Dict[str, List[str]] = {}
    soft: Dict[str, List[str]] = {}

    for kind in _ANCHOR_KINDS:
        old = set(before.get(kind, ()))
        new = set(after.get(kind, ()))
        missing = sorted(old - new)
        added = sorted(new - old)
        if kind in _STRICT_KINDS:
            if missing:
                lost[kind] = missing
            if added:
                invented[kind] = added
        else:
            entries = [f"-{v}" for v in missing] + [f"+{v}" for v in added]
            if entries:
                soft[kind] = entries

    had_question = any(mark in (base or "") for mark in _QUESTION_MARKS)
    has_question = any(mark in (polished or "") for mark in _QUESTION_MARKS)
    lost_question = had_question and not has_question

    return IntegrityReport(
        ok=not lost and not invented and not lost_question,
        lost=lost,
        invented=invented,
        lost_question=lost_question,
        soft_diff=soft,
    )


@dataclass(frozen=True)
class PolishRequest:
    """潤稿的輸入。

    **只有 `reply` 是要被改的文字。** tone／persona／custom_instruction 是
    「往哪個方向改」的參考，不是新的內容來源——polisher 不得依據它們
    加入任何原文沒有的事實。
    """

    reply: str
    #: 已驗證的 tone id（僅供 polisher 對齊語域，可為 None）
    tone: Optional[str] = None
    #: tone 的語氣指示原文
    tone_instruction: Optional[str] = None
    #: 已淨化的 persona 摘要（`PersonaProfile.to_prompt_dict()` 的結果）
    persona: Optional[Dict[str, Any]] = None
    #: Viewer 這次明確寫的要求
    custom_instruction: Optional[str] = None
    language: str = "zh-TW"


@dataclass(frozen=True)
class PolishResult:
    """潤稿的輸出。

    `text` 永遠是**可以直接用的文字**：潤稿成功時是潤稿版，
    完整性驗證沒過時是原文。呼叫端不需要自己判斷要用哪一個，
    但**必須**讀 `fallback_reason` 決定要不要在 UI 上標示。
    """

    text: str
    #: 有沒有真的採用潤稿結果。`False` 代表 `text` 就是原文。
    polished: bool
    #: 實際執行的 polisher 名稱（`noop` / `sepia`）
    polisher: str
    integrity: Optional[IntegrityReport] = None
    #: 退回原文的原因；`None` 代表沒有退回。
    #: 這與「Sepia 不可用」不同——後者在呼叫 polisher 之前就會拋錯。
    fallback_reason: Optional[str] = None
    #: 這次潤稿實際用掉的 provider 名稱與模型，供 meta 與用量比對
    provider: Optional[str] = None
    model: Optional[str] = None

    def to_meta(self) -> Dict[str, Any]:
        """給 SSE meta 與 `draft_replies.generation_config_json` 的形狀。"""
        meta: Dict[str, Any] = {"polisher": self.polisher, "polished": self.polished}
        if self.fallback_reason:
            meta["fallback_reason"] = self.fallback_reason
        if self.model:
            meta["polish_model"] = self.model
        return meta


class ResponsePolisher(ABC):
    """潤稿器的窄介面。

    刻意與 `AIProvider` 同構（`name` / `label` / `available()`），
    這樣 API 層列出「有哪些 polisher 可用」的程式碼可以照抄
    `providers.describe_all()` 的形狀。
    """

    #: 程式識別字，也是 API 與設定值接受的名稱
    name: str = "base"
    #: 前端顯示用的繁中名
    label: str = "未命名潤稿器"

    @abstractmethod
    def available(self) -> Tuple[bool, str]:
        """`(可用, 原因)`——**含 runtime 依賴**（例如有沒有 AI 供應商）。

        這是 `polish()` 執行前的完整檢查。不該打網路、不該呼叫模型。
        """

    def static_available(self) -> Tuple[bool, str]:
        """`(可用, 原因)`——**只看安裝狀態**，不含 runtime 依賴。

        為什麼要與 `available()` 分開：列出選項的端點（`GET /api/v1/polishers`）
        沒有 AI 供應商實例可以傳，而 `available()` 會因此回 false。

        2026-09-07 實測到的後果：那個端點的 `sepia.available` **恆為 false**，
        而前端正是用它決定「使用 Sepia 潤稿」這個勾選框能不能勾——
        於是整個功能從 UI 上完全打不開，而且沒有任何錯誤訊息，
        看起來就只是「這個選項是灰的」。

        「規則有沒有裝好」與「現在有沒有可用的供應商」是兩個不同的問題，
        後者由 `GET /api/v1/providers` 回答。混在一個布林裡，前端就無法
        判斷該叫使用者去補裝規則、還是去設定 API key。

        預設等同 `available()`，只有真正有 runtime 依賴的實作才需要覆寫。
        """
        return self.available()

    @abstractmethod
    def polish(self, request: PolishRequest) -> PolishResult:
        """潤稿。實作必須自己跑 `verify_integrity()` 並在沒過時退回原文。"""


class NoopPolisher(ResponsePolisher):
    """不潤稿，原文照回。

    存在的理由不只是「關閉時的預設值」：它讓「有沒有開潤稿」在程式碼裡
    是同一條路徑（都呼叫 `polish()`），呼叫端不需要寫 `if sepia_enabled`
    的分支去繞過整段邏輯。少一個分支就少一個「開著卻沒生效」的失敗形態。
    """

    name = "noop"
    label = "不潤稿"

    def available(self) -> Tuple[bool, str]:
        return True, ""

    def polish(self, request: PolishRequest) -> PolishResult:
        return PolishResult(text=request.reply, polished=False, polisher=self.name)


def resolve(
    name: Optional[str],
    *,
    provider: Any = None,
    available_only: bool = False,
) -> ResponsePolisher:
    """名稱 → polisher 實例。

    `provider` 是已經建好的 `AIProvider`（潤稿用**同一個**供應商，
    見需求「Claude CLI → Claude CLI、Gemini → Gemini」）。
    延遲 import `SepiaPolisher` 是為了讓 `sepia.py` 能 import 這個模組
    而不產生循環。
    """
    key = (name or "noop").strip().lower()
    if key in ("", "noop", "none", "off"):
        return NoopPolisher()
    if key == "sepia":
        from .sepia import SepiaPolisher

        return SepiaPolisher(provider=provider)

    from ..errors import InvalidParameter

    raise InvalidParameter(f"polisher 只接受 noop／sepia，收到 {name!r}")


def describe_all(*, provider: Any = None) -> List[Dict[str, Any]]:
    """列出所有 polisher 與**安裝層**的可用性，給 API 與前端。

    用的是 `static_available()` 而不是 `available()`：這個清單要回答的是
    「規則裝好了嗎」，而不是「現在這一刻能不能跑」。傳了 `provider` 也一樣——
    前者對同一個部署是穩定的，後者會隨使用者選的供應商而變，
    把兩者混在一個布林裡前端就無法判斷該去補什麼（見 `static_available`）。

    比照 `providers.describe_all()`：單一 polisher 初始化失敗不讓整個清單掛掉。
    """
    out: List[Dict[str, Any]] = []
    for key in ("noop", "sepia"):
        try:
            polisher = resolve(key, provider=provider)
            ok, reason = polisher.static_available()
            out.append(
                {
                    "name": polisher.name,
                    "label": polisher.label,
                    "available": ok,
                    "reason": reason,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 清單端點不該因單一項目失敗而全掛
            out.append(
                {
                    "name": key,
                    "label": key,
                    "available": False,
                    "reason": f"初始化失敗：{exc}",
                }
            )
    return out
