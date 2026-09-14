"""Draft Worklog 的證據蒐集（ADR-0008，計畫 §8）。

回答的問題只有一個：**這位 Viewer 在這一天做了什麼？** 證據其實都在——
git commit 的作者信箱與時間、Chat 上的發言——只是散在沒有人會回頭翻的地方。
這個模組把兩種來源撈出來、正規化成同一個 `Evidence` 形狀，交給上層去
分配時數（`core/worklog_allocation.py`）與挑候選 issue。

**這裡不做判斷，只做蒐集。** 專案歸屬永遠由 Viewer 在確認畫面自己選
（ADR-0008 的唯一硬規則）；本模組產出的 `weight_input` 與 `terms` 都是
啟發式，猜錯的代價是排序難看，不是有人被填錯工時。

四條不可以動的規則：

1. **讀 git 一律不碰工作區**（ADR-0006 鐵律）。只跑 `rev-parse` 與 `log`，
   讀 refs 與 object database；不 checkout、不動 index、不讀 diff 內容。
   `--numstat` 的數字只當權重輸入。
2. **git 呼叫沿用 `code_search._git`**，不複製第二份。那裡把 argv list
   （repo_path 是使用者輸入，不組 shell 字串）、`GIT_OPTIONAL_LOCKS=0`
   （不搶使用者的 `index.lock`）、`GIT_TERMINAL_PROMPT=0`（憑證提示會把
   SSE 串流卡到逾時）綁在一起，複製出去就是第二個會各自漂移的地方。
3. **日界由呼叫端傳入的 `tz_offset` 決定，不讀伺服器的本地時區。**
   `%aI` 是作者當地時間、Chat 的 `createTime` 是 UTC、ZPlanner 的 `date`
   根本沒有時區——「半夜一點的 commit 算哪一天」必須有一個寫得出來的依據，
   而那個依據是 Viewer 的偏移，不是這台機器剛好設成什麼。
4. **Chat 的母體絕不是全部的 Space。** 整個帳號有四百多個，母體是
   「釘選的 Space ∪ 當天有 Mention 命中的 Space」，再套 `WORKLOG_MAX_SPACES`。

**這個模組刻意不 import repository**（同 `code_search` 的可測性慣例）：
呼叫端把查好的 work_repos／space_ids／偏好傳進來，讓這裡是一層純
git ＋ HTTP ＋ 文字處理，不接資料庫也能測。連 `verify_and_record_work_repo`
的「寫回驗證結果」都是靠傳進來的 callable，不是自己去碰 DB。
"""

import math
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import code_search
from . import config as cfg
from .chat_client import parse_rfc3339
from .errors import (
    ChatPulseError,
    CodeProjectUnavailable,
    InvalidParameter,
    WorkRepoUnavailable,
)


# --------------------------------------------------------------------------
# 時區與日界
#
# 這一段是整個模組風險最高的地方，不是因為難，是因為錯了看不出來：
# 一筆半夜的 commit 落到前一天，草稿照樣產得出來，只是那天少了一筆。
# --------------------------------------------------------------------------

_TZ_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hh>\d{2}):?(?P<mm>\d{2})$")

#: 真實存在的 UTC 偏移落在 −12:00 ～ +14:00。超出範圍一律當成打錯字，
#: 不去寬容解析——寬容解析的結果是工時默默落在錯的一天。
_TZ_OFFSET_MAX_MINUTES = 14 * 60


def normalize_tz_offset(value: Optional[str]) -> str:
    """把 `tz_offset` 正規化成 `+08:00` 這種寫法，非法值直接擋。

    接受 `+08:00`／`+0800`／`Z`／`UTC`；其餘（含空字串、`Asia/Taipei`
    這種 IANA 名稱）一律 `InvalidParameter`。**不接受 IANA 名稱是刻意的**：
    那需要 tz 資料庫，而且同一個名稱在不同日期的偏移不同，前端已經知道
    自己當下的偏移了，傳偏移比傳名稱少一層會漂移的轉換。

    比照 `prompts.validate_style`／`code_search.validate_environment`：
    非法值直接擋，不做寬容解析。
    """
    raw = (value or "").strip()
    if raw.upper() in ("Z", "UTC", "GMT"):
        return "+00:00"
    m = _TZ_OFFSET_RE.match(raw)
    if not m:
        raise InvalidParameter(
            f"tz_offset 必須是 +08:00 這種 UTC 偏移，收到 {value!r}"
        )
    minutes = int(m.group("hh")) * 60 + int(m.group("mm"))
    if int(m.group("mm")) > 59 or minutes > _TZ_OFFSET_MAX_MINUTES:
        raise InvalidParameter(f"tz_offset 超出合理的 UTC 偏移範圍：{value!r}")
    if m.group("sign") == "-":
        minutes = -minutes
    sign = "-" if minutes < 0 else "+"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def tzinfo_of(tz_offset: str) -> timezone:
    """`+08:00` → `datetime.timezone`。輸入會先過 `normalize_tz_offset`。"""
    canonical = normalize_tz_offset(tz_offset)
    sign = -1 if canonical[0] == "-" else 1
    hours, minutes = int(canonical[1:3]), int(canonical[4:6])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_work_date(work_date: str) -> datetime:
    """`2026-09-13` → naive date-time（00:00）。格式錯直接擋。"""
    raw = (work_date or "").strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise InvalidParameter(
            f"work_date 必須是 YYYY-MM-DD，收到 {work_date!r}"
        ) from exc
    return parsed


def day_bounds(work_date: str, tz_offset: str) -> Tuple[datetime, datetime]:
    """回傳 `[start, end)`，兩端都是帶偏移的 aware datetime。

    **end 是開區間**：`00:00:00` 的那一刻屬於新的一天。用閉區間會讓
    整點的 commit 同時出現在兩天的草稿裡，而兩天的時數都會算到它。
    """
    tz = tzinfo_of(tz_offset)
    start = datetime.combine(parse_work_date(work_date).date(), time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _within(moment: Optional[datetime], start: datetime, end: datetime) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        # 沒有偏移的時間戳不猜：當成不屬於任何一天，寧可漏也不要算錯人的工時。
        return False
    return start <= moment < end


# --------------------------------------------------------------------------
# 權重輸入
#
# ⚠️ **這是啟發式，不是科學。** 它唯一的用途是決定「同一天的兩件事，
# 哪一件看起來花的時間比較多」，而且 Viewer 在確認畫面可以逐筆 ±0.5 改掉。
# 不要拿它當工時的估算模型，也不要因為它「不準」就去調參數——真正的
# 修正入口是人，不是這兩行公式。
# --------------------------------------------------------------------------

#: 兩種來源都先取對數再比大小。不取的話，一則 200 字的訊息（200）會壓過
#: 一次 50 行的改動（50），而那顯然不是花比較久的那件事。
def commit_weight_input(insertions: int, deletions: int) -> float:
    """commit 的權重輸入：`log(1 + 增行 + 刪行)`。

    用增刪行數而不是檔案數，是因為「動了 30 個檔各一行」（多半是改名或
    批次套用）與「動了 1 個檔 300 行」在花費上不是同一件事；用對數是因為
    行數本身是重尾分布，一次 build 產物的 commit 動輒上萬行。
    """
    total = max(0, int(insertions or 0)) + max(0, int(deletions or 0))
    return math.log(1.0 + total)


def chat_weight_input(char_count: int, is_mention: bool = False) -> float:
    """發言的權重輸入：`log(1 + 字數)`，@ 了人再乘上 `WORKLOG_CHAT_MENTION_BONUS`。

    ⚠️ **與計畫 §8 的字面寫法有一處刻意的差異**：那裡寫「chat 用字數」。
    直接用字數的話，兩種來源不同尺度（字數是線性、commit 是對數），
    一則長訊息會把整天的時數吸走。這裡把**字數**餵進與 commit 相同的
    對數壓縮——輸入仍然是字數，只是換到同一把尺上才比得了大小。
    """
    count = max(0, int(char_count or 0))
    weight = math.log(1.0 + count)
    if is_mention:
        weight *= cfg.WORKLOG_CHAT_MENTION_BONUS
    return weight


# --------------------------------------------------------------------------
# 關鍵字抽取
#
# 基底直接用 `code_search.extract_search_terms`（它就是 `_STOPWORDS` ＋
# `_IDENT_RE` 那一套），再補兩種它抓不到、但在這個場景最有價值的形狀。
# --------------------------------------------------------------------------

#: ZPlanner／Jira 式的 issue key。**這是配對候選 issue 最強的訊號**，
#: 一旦 commit subject 寫了 `PEI-133`，就不必猜這筆屬於哪個專案。
_ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")

#: Conventional Commits 的 scope：`feat(worklog): …` 的 `worklog`。
_CONVENTIONAL_SCOPE_RE = re.compile(
    r"^\s*[a-z]{3,10}(?:\(([^)\n]{1,40})\))?!?\s*:", re.IGNORECASE
)

#: 連續的中日韓字元。**不做斷詞**：沒有斷詞器的情況下切 n-gram 只會產生
#: 一堆沒有意義的兩字組合，整段當一個詞至少還能拿去做子字串比對。
_CJK_RUN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]{2,16}")

#: 中文的「動作詞」。它們出現在幾乎每一則 commit subject 裡，留著等於
#: 每一筆證據都命中同一個詞——和 `_STOPWORDS` 擋掉 `error` 是同一個理由。
_CJK_STOPWORDS = frozenset(
    """
    新增 修正 修改 更新 調整 移除 刪除 重構 優化 改成 改用 加上 拿掉 補上
    測試 文件 說明 問題 功能 支援 處理 實作 完成 目前 因為 所以 可以 不要
    作為 這個 那個 一下 目前 之後 之前 已經 還是 但是 而且 或是
    """.split()
)

#: 落在詞首／詞尾一定是贅字的單字。整段 CJK 當一個詞的代價是會把助詞一起
#: 吃進來（`的基礎層`），修掉兩端就好——中間的不動，動了就是在做斷詞了。
_CJK_EDGE_PARTICLES = "的與和在是了把被對於就都也很更最個們之而且或把將從"


def find_issue_keys(*texts: str) -> List[str]:
    """從文字裡抽出 issue key（`PEI-133`、`FIA01P2403-391`），保留出現順序、去重。

    存在的理由是**不讓 `_ISSUE_KEY_RE` 被複製第二份**：分群
    （`core/worklog_clustering.py`）要用同一個判準，而「什麼形狀算 issue key」
    一旦有兩份定義，兩邊漂移的當天不會有任何測試變紅——只會有一群工時
    莫名其妙沒被合在一起。

    大小寫**照原樣**回傳（正規表示式本來就只認大寫開頭），比對時才不會把
    `PEI-133` 與 `pei-133` 當成兩張不同的單——後者根本不會被認出來。
    """
    out: List[str] = []
    seen = set()
    for text in texts:
        for m in _ISSUE_KEY_RE.finditer(text or ""):
            key = m.group(0)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def is_standards_reference(text: str) -> bool:
    """這個「長得像 issue key」的字串其實是標準／文件編號嗎（`ADR-0008`、`UTF-8`）？

    `ADR-0008`、`RFC-3339`、`ISO-8601`、`UTF-8`、`SHA-256` 全都完全符合
    `_ISSUE_KEY_RE`（大寫前綴 ＋ 連字號 ＋ 數字），而技術團隊的 commit message
    裡遍地都是。誤判的代價不只是難看：那個字串會被當成**最強訊號**拿去
    ZPlanner 撈候選，於是那一群的候選清單必然是空的。

    **判準只看前綴**（清單見 `cfg.WORKLOG_NON_ISSUE_PREFIXES`，可用環境變數
    覆寫），所以它一定會誤殺——真的有一個代號叫 `SQL` 的專案時，`SQL-1234`
    也會被歸到這裡。取捨寫在 config 那一段：誤殺只是退回用 repo 分群、換一個
    關鍵字去查候選，沒有人會被填錯工時；而放過 `ADR-0008` 是每個 commit 都在
    發生的事。

    **`find_issue_keys` 刻意不套這個判準**：那個函式是「什麼形狀叫 issue key」
    的唯一定義，該保持純粹。這裡是另一個問題——「這個形狀的東西是不是真的
    單號」，兩邊分開才不會互相汙染。判準放在本模組而不是分群那側，是因為
    `extract_terms` 的排序也需要它（`terms[0]` 就是送去查候選的 `q`），
    複製第二份的那天兩邊會各自漂移，而且不會有任何測試變紅。
    """
    raw = (text or "").strip()
    if not _ISSUE_KEY_RE.fullmatch(raw):
        return False
    return raw.split("-", 1)[0].upper() in cfg.WORKLOG_NON_ISSUE_PREFIXES


def extract_terms(text: str, extra: str = "", *, max_terms: Optional[int] = None) -> List[str]:
    """從 commit subject 或發言抽出拿得去配 issue 的關鍵字。

    優先序（前面的先進清單，額滿就截斷）：
      1. issue key（`PEI-133`）——最強的訊號，直接指到某一張單
      2. Conventional Commits 的 scope（`feat(worklog):` 的 `worklog`）
      3. `code_search.extract_search_terms` 的程式碼形狀 token
      4. 中日韓連續字串（扣掉每則都會出現的動作詞）
      5. 標準／文件編號（`ADR-0008`、`UTF-8`）——形狀是 issue key，但**排到最後**

    第 4 項是相對計畫 §8 的**增補**，不是取代：這個團隊的 commit subject
    多半是中文，只跑 `_IDENT_RE` 的話整天的證據會一個詞都抽不出來，而
    `terms` 的唯一用途就是去撈候選 issue。抽錯不會有人被填錯工時（歸屬
    一律由 Viewer 選），抽不到才是真的沒用。

    第 5 項是 2026-09-13 補的：`terms[0]` 會原封不動變成送去 ZPlanner 的 `q`
    （`worklog_clustering.Cluster.query_term`），所以 `docs(zplanner): 補
    ADR-0008 …` 的候選清單必然是空的。**降級而不是刪掉**：那個編號仍然是這筆
    commit 在講什麼的線索，只是它不該排在第一個。
    """
    limit = max_terms or cfg.WORKLOG_MAX_TERMS
    primary = text or ""
    secondary = extra or ""

    ordered: List[str] = []
    seen = set()

    def add(token: str) -> None:
        token = token.strip().strip(".,;:!?()[]{}\"'「」『』，。、（）")
        if not token or len(token) < 2:
            return
        low = token.lower()
        if low in seen or low in code_search._STOPWORDS or token in _CJK_STOPWORDS:
            return
        seen.add(low)
        ordered.append(token)

    # 標準編號先擱著，最後才補進清單（優先序第 5 項）。
    deferred: List[str] = []
    for src in (primary, secondary):
        for m in _ISSUE_KEY_RE.finditer(src):
            key = m.group(0)
            if is_standards_reference(key):
                deferred.append(key)
            else:
                add(key)

    scope = _CONVENTIONAL_SCOPE_RE.match(primary)
    if scope and scope.group(1):
        for part in re.split(r"[,\s/]+", scope.group(1)):
            add(part)

    for token in code_search.extract_search_terms(primary, secondary, max_terms=limit):
        add(token)

    for src in (primary, secondary):
        for m in _CJK_RUN_RE.finditer(src):
            add(m.group(0).strip(_CJK_EDGE_PARTICLES))

    for key in deferred:
        add(key)

    return ordered[:limit]


# --------------------------------------------------------------------------
# 正規化後的證據
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """一件「這天做過的事」。兩種來源正規化成同一個形狀。

    `ref` 是**證據欄回溯的鍵**，格式固定為 `commit:<sha>` 或
    `chat:<message_name>`。SSE 的 `clusters[].evidence_refs[]` 用字串指回
    `meta` 裡的陣列，所以「無法回溯到 meta 的東西不准進證據欄」在結構上
    就成立——前端拿到指不到的 ref 直接丟棄即可。

    `occurred_at` 一律換算成 Viewer 傳進來的 `tz_offset`，讓證據欄顯示的
    時刻與「這筆算哪一天」用的是同一把尺。
    """

    kind: str  # "commit" | "chat"
    ref: str
    occurred_at: str  # ISO-8601，帶 Viewer 的偏移
    title: str
    source_label: str  # repo 名 或 Space 顯示名
    terms: Tuple[str, ...]
    weight_input: float

    # --- commit 專屬（chat 一律留預設值）---
    sha: str = ""
    short_sha: str = ""
    author_email: str = ""
    insertions: int = 0
    deletions: int = 0
    files_changed: int = 0
    branches: Tuple[str, ...] = ()

    # --- chat 專屬（commit 一律留預設值）---
    space_id: str = ""
    message_name: str = ""
    is_mention: bool = False
    char_count: int = 0

    def to_meta(self) -> Dict[str, Any]:
        """SSE `meta` 用的 JSON 形狀（計畫 §5）。

        **不含訊息內文**：Chat 的文字與 `mentions` 表同一條鐵律，只存／只傳
        識別資訊，要顯示時再即時取回。commit subject 可以帶（ADR-0008 明說
        只用作者信箱、時間、訊息三樣）。
        """
        common = {
            "ref": self.ref,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "source_label": self.source_label,
            "terms": list(self.terms),
            "weight_input": round(self.weight_input, 4),
        }
        if self.kind == "commit":
            common.update(
                {
                    "sha": self.sha,
                    "short_sha": self.short_sha,
                    "subject": self.title,
                    "authored_at": self.occurred_at,
                    "author_email": self.author_email,
                    "insertions": self.insertions,
                    "deletions": self.deletions,
                    "files_changed": self.files_changed,
                    "branches": list(self.branches),
                }
            )
        else:
            common.update(
                {
                    "message_name": self.message_name,
                    "space_id": self.space_id,
                    "space_name": self.source_label,
                    "is_mention": self.is_mention,
                    "char_count": self.char_count,
                }
            )
        return common


@dataclass(frozen=True)
class RepoScan:
    """一個 repo 掃過之後的帳。對應 SSE `meta.repos[]`。"""

    repo_id: Optional[int]
    name: str
    commit_count: int
    branches_scanned: Tuple[str, ...]
    scanned_all_branches: bool
    author_emails: Tuple[str, ...]
    truncated: bool = False

    def to_meta(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "name": self.name,
            "commit_count": self.commit_count,
            # 空陣列＋`scanned_all_branches: true` 才是「掃了全部」，
            # 不要把它讀成「一個分支都沒掃」——語意與 work_repos.branch_filter 一致。
            "branches_scanned": list(self.branches_scanned),
            "scanned_all_branches": self.scanned_all_branches,
            "author_emails": list(self.author_emails),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class SkippedSource:
    """跳過的來源。**一定要有 reason**：草稿少了一個 repo 的證據，

    使用者唯一能察覺的方式就是這句話。靜默跳過等於讓他以為那天沒做事。
    """

    kind: str  # "repo" | "space"
    label: str
    reason: str
    ref_id: Optional[Any] = None

    def to_meta(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.ref_id,
            "label": self.label,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SpaceScan:
    """一個 Space 掃過之後的帳。對應 SSE `meta.chat` 的來源清單。"""

    space_id: str
    space_name: str
    message_count: int
    truncated: bool = False

    def to_meta(self) -> Dict[str, Any]:
        return {
            "space_id": self.space_id,
            "space_name": self.space_name,
            "message_count": self.message_count,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class DayEvidence:
    """一天份的證據總帳。時數分配與候選 issue 都只吃這個。"""

    work_date: str
    tz_offset: str
    since: str  # 日界起（含），帶 tz_offset
    until: str  # 日界迄（不含）
    evidence: Tuple[Evidence, ...] = ()
    repos: Tuple[RepoScan, ...] = ()
    spaces: Tuple[SpaceScan, ...] = ()
    skipped: Tuple[SkippedSource, ...] = ()

    @property
    def is_empty(self) -> bool:
        """真的一件事都沒有。呼叫端據此發 SSE 的 `NO_EVIDENCE`。"""
        return not self.evidence

    def to_meta(self) -> Dict[str, Any]:
        return {
            "work_date": self.work_date,
            # 把偏移一起回報出去，是因為草稿會落地、之後還要寫進
            # worklog_drafts.tz_offset：沒有它，「這筆為什麼算在這天」
            # 在事後完全無法重建。
            "tz_offset": self.tz_offset,
            "since": self.since,
            "until": self.until,
            "repos": [r.to_meta() for r in self.repos],
            "repos_skipped": [s.to_meta() for s in self.skipped if s.kind == "repo"],
            "spaces": [s.to_meta() for s in self.spaces],
            "spaces_skipped": [s.to_meta() for s in self.skipped if s.kind == "space"],
            "commits": [e.to_meta() for e in self.evidence if e.kind == "commit"],
            "chat": [e.to_meta() for e in self.evidence if e.kind == "chat"],
        }


# --------------------------------------------------------------------------
# git 證據
# --------------------------------------------------------------------------

#: 分隔符刻意用 ASCII 的 unit／record separator。**不要換成 tab 或換行**：
#: commit subject 可以含任何字元，用 tab 分欄的那一刻，任何一則帶 tab 的
#: subject 都會讓後面的欄位整排錯位，而且錯得很安靜。
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

#: 欄位順序：完整 sha、短 sha、author date（ISO，帶偏移）、author email、
#: decorate（分支歸屬）、subject。subject 放最後，才能用 maxsplit 把
#: 「subject 裡剛好有一個 \x1f」吸收掉而不錯位。
_PRETTY_FORMAT = _FIELD_SEP.join(["%H", "%h", "%aI", "%aE", "%D", "%s"]) + _RECORD_SEP

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: `--numstat` 的行：`<增>\t<刪>\t<路徑>`。二進位檔的增刪會是 `-`。
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$")


def _parse_decorations(raw: str) -> Tuple[str, ...]:
    """把 `%D` 拆成分支名。

    只取 decorate 是**刻意的成本取捨**（計畫 §8 明寫）：要精確知道一個
    commit 在哪些分支上，得對每個 sha 跑 `branch --contains`，那是 N 次
    git 呼叫。分支歸屬在這裡只是顯示用的線索，不值這個價。
    """
    out: List[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if not name or name.startswith("tag:"):
            continue
        if "->" in name:  # `HEAD -> main`
            name = name.split("->", 1)[1].strip()
        if name in ("HEAD",) or not name:
            continue
        if name not in out:
            out.append(name)
    return tuple(out)


@dataclass
class _RawCommit:
    """解析中的暫存體。刻意可變——subject 可能要跨 chunk 續接。"""

    sha: str
    short_sha: str
    authored_at: str
    author_email: str
    decorations: str
    subject: str
    insertions: int = 0
    deletions: int = 0
    files_changed: int = 0


def _parse_log_output(stdout: str) -> List[_RawCommit]:
    """解析 `log --numstat --pretty=format:…%x1e` 的輸出。

    git 的實際排版是：`<header>\\x1e\\n<numstat 行>…\\n\\n<下一個 header>`。
    所以用 `\\x1e` 切開之後，**每一塊的內容屬於「上一筆」**，塊尾才是
    這一筆的 header。

    三種畸形輸入都不會讓這個函式壞掉（subject 可以含任何字元）：
      * subject 含 `\\x1f` → header 用 maxsplit 切，多的分隔符留在 subject 裡
      * subject 含 `\\x1e` → 下一塊找不到 header，整塊當成 subject 的續接
      * subject 含換行 → header 之後的行接回 subject（numstat 一定在下一塊）
    """
    commits: List[_RawCommit] = []
    current: Optional[_RawCommit] = None

    for chunk_index, chunk in enumerate(stdout.split(_RECORD_SEP)):
        lines = chunk.split("\n")
        header_idx: Optional[int] = None
        header_fields: List[str] = []
        for i, line in enumerate(lines):
            if _FIELD_SEP not in line:
                continue
            fields = line.split(_FIELD_SEP, 5)
            if len(fields) == 6 and _SHA_RE.match(fields[0]):
                header_idx = i
                header_fields = fields
                break

        before = lines[:header_idx] if header_idx is not None else lines
        after = lines[header_idx + 1 :] if header_idx is not None else []

        # `before` 屬於上一筆：先是 numstat，剩下的是 subject 被 \x1e 切斷的殘段。
        first_extra = True
        for line in before:
            if current is None:
                break
            stat = _NUMSTAT_RE.match(line)
            if stat:
                current.files_changed += 1
                if stat.group(1) != "-":
                    current.insertions += int(stat.group(1))
                if stat.group(2) != "-":
                    current.deletions += int(stat.group(2))
                first_extra = False
                continue
            if not line:
                continue
            # 非空又不是 numstat：subject 裡有 \x1e（或換行）造成的續接。
            joiner = _RECORD_SEP if (chunk_index > 0 and first_extra) else "\n"
            current.subject += joiner + line
            first_extra = False

        if header_idx is None:
            continue

        if current is not None:
            commits.append(current)
        current = _RawCommit(
            sha=header_fields[0],
            short_sha=header_fields[1],
            authored_at=header_fields[2],
            author_email=header_fields[3],
            decorations=header_fields[4],
            subject=header_fields[5],
        )
        for line in after:  # subject 含換行時的其餘各行
            current.subject += "\n" + line

    if current is not None:
        commits.append(current)
    return commits


def _log_args(
    *,
    author_emails: Sequence[str],
    branch_filter: Sequence[str],
    since: datetime,
    until: datetime,
) -> List[str]:
    """組 `git log` 的 argv。

    🔴 **沒有 `--until` 上界，這是刻意的。** `--since`／`--until` 濾的是
    committer date 而不是 `%aI`，而 rebase 可以發生在寫程式之後的**任何時候**
    ——把功能分支擺兩週再 rebase 是日常。上界放寬固定時數只覆蓋得了「N 小時內
    完成的 rebase」，超過的那些會被 git 直接濾掉，`truncated` 還是 `False`、
    `skipped` 還是空的，使用者完全看不出那天少了東西（2026-09-13 實測：
    author date 9/01、committer date 9/13 的 commit，查 9/01 撈不到）。
    未來的 committer date 本來就不存在，上界本身沒有在擋任何東西。

    代價是視窗變成 `[日界 − padding, ∞)`，而 git log 是 **committer date 由新
    到舊**輸出，`-n` 砍掉的正好是最舊的那一端（＝目標日）。所以 `-n` 用的是
    `WORKLOG_GIT_SCAN_LIMIT`（掃描預算，遠大於單日保留上限），而且
    `collect_repo_commits` 碰到這個上限一定會標 `truncated`——那是使用者唯一
    能察覺「可能漏了」的訊號。

    `until` 參數保留但只用來算下界的合理性，不再進 argv：拿掉它會讓呼叫端
    以為日界只有一半，而日界的兩端在 Python 那側都還要用。

    `--fixed-strings` ＋ `--regexp-ignore-case` 是給 `--author=` 用的：
    預設 `--author` 吃正規表示式，信箱裡的 `.` 與 `+` 會被當成元字元。
    多個 `--author` 之間是 **OR**（git 的既有行為），正是我們要的。
    """
    pad = timedelta(hours=cfg.WORKLOG_GIT_WINDOW_PADDING_HOURS)
    args: List[str] = [
        "log",
        "--no-merges",
        "--numstat",
        "--fixed-strings",
        "--regexp-ignore-case",
        f"-n{cfg.WORKLOG_GIT_SCAN_LIMIT}",
        f"--since={(since - pad).isoformat()}",
        f"--pretty=format:{_PRETTY_FORMAT}",
    ]
    for email in author_emails:
        args.append(f"--author={email}")
    if branch_filter:
        args.extend(branch_filter)
    else:
        # 空的 branch_filter ＝ 掃全部分支（與 code_projects 相反，見 db.py）
        args.append("--all")
    # 收尾的 `--` 讓「分支名剛好與檔名相同」不會變成 pathspec
    args.append("--")
    return args


def ensure_work_repo(repo_path: str, name: str) -> None:
    """驗證路徑是可讀的 git repo，不是就拋 `WORK_REPO_UNAVAILABLE`。

    **沿用 `code_search._ensure_repo`**（同一個 `rev-parse --git-dir`），
    只把錯誤翻譯成工作 repo 的措辭與錯誤碼：那邊的訊息寫「請到設定頁的
    參考專案更新路徑」，對工時草稿的使用者是指到錯的設定頁。
    """
    try:
        code_search._ensure_repo(repo_path, name)
    except CodeProjectUnavailable as exc:
        raise WorkRepoUnavailable(
            f"工作 repo「{name}」讀不到（{exc}）。"
            "請到設定頁的「我的工作 repo」更新路徑。"
        ) from exc


def collect_repo_commits(
    *,
    repo_path: str,
    repo_name: str,
    author_emails: Sequence[str],
    branch_filter: Sequence[str] = (),
    work_date: str,
    tz_offset: str,
    repo_id: Optional[int] = None,
) -> Tuple[List[Evidence], RepoScan]:
    """撈一個 repo 在這一天、這些信箱底下的 commit。

    路徑壞掉會拋 `WorkRepoUnavailable`；`collect_git_evidence` 會把它降級
    成 `skipped`，讓一個壞掉的 repo 不會把整份草稿弄掉（形狀同
    `code_search.collect` 的局部失敗處理）。
    """
    canonical_tz = normalize_tz_offset(tz_offset)
    tz = tzinfo_of(canonical_tz)
    start, end = day_bounds(work_date, canonical_tz)

    ensure_work_repo(repo_path, repo_name)
    if not author_emails:
        # 不填信箱就掃全部作者，等於把同事的 commit 算成自己的工時。
        # 這比「少一個 repo」嚴重得多，所以擋在這裡而不是默默放行。
        raise WorkRepoUnavailable(
            f"工作 repo「{repo_name}」還沒填作者信箱，無法分辨哪些 commit 是你的。"
            "請到設定頁按「重新檢查」，從 detected_emails 挑一個填上。"
        )

    proc = code_search._git(
        repo_path,
        *_log_args(
            author_emails=author_emails,
            branch_filter=branch_filter,
            since=start,
            until=end,
        ),
        timeout=cfg.WORKLOG_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        raise WorkRepoUnavailable(
            f"工作 repo「{repo_name}」的 git log 失敗："
            f"{(proc.stderr or '').strip()[:200] or '未知錯誤'}"
        )

    raw_commits = _parse_log_output(proc.stdout)
    evidence: List[Evidence] = []
    for raw in raw_commits:
        authored = parse_rfc3339(raw.authored_at)
        if not _within(authored, start, end):
            continue  # 粗篩用 committer date，這裡才是真正的日界
        assert authored is not None
        subject = raw.subject.strip()
        evidence.append(
            Evidence(
                kind="commit",
                ref=f"commit:{raw.sha}",
                occurred_at=authored.astimezone(tz).isoformat(),
                title=subject,
                source_label=repo_name,
                terms=tuple(extract_terms(subject)),
                weight_input=commit_weight_input(raw.insertions, raw.deletions),
                sha=raw.sha,
                short_sha=raw.short_sha,
                author_email=raw.author_email,
                insertions=raw.insertions,
                deletions=raw.deletions,
                files_changed=raw.files_changed,
                branches=_parse_decorations(raw.decorations),
            )
        )

    evidence.sort(key=lambda e: (e.occurred_at, e.sha))

    # `truncated` 只代表一件事：**有東西沒進來**。兩種情況都算。
    #   (a) git 的掃描預算用完 —— 輸出是 committer date 由新到舊，砍掉的是最舊
    #       的一端，也就是目標日；漏掉的正是我們要的那些。
    #   (b) 這一天自己的 commit 多到超過單日保留上限。
    # 先排序再截斷，留下來的才是決定性的（同一天重產兩次要得到同一份草稿）。
    scan_limit_hit = len(raw_commits) >= cfg.WORKLOG_GIT_SCAN_LIMIT
    day_limit_hit = len(evidence) > cfg.WORKLOG_MAX_COMMITS_PER_REPO
    if day_limit_hit:
        evidence = evidence[: cfg.WORKLOG_MAX_COMMITS_PER_REPO]

    scan = RepoScan(
        repo_id=repo_id,
        name=repo_name,
        commit_count=len(evidence),
        branches_scanned=tuple(branch_filter),
        scanned_all_branches=not branch_filter,
        author_emails=tuple(author_emails),
        truncated=scan_limit_hit or day_limit_hit,
    )
    return evidence, scan


def collect_git_evidence(
    work_repos: Iterable[Mapping[str, Any]],
    *,
    work_date: str,
    tz_offset: str,
) -> Tuple[List[Evidence], List[RepoScan], List[SkippedSource]]:
    """對一組 work_repos 逐一撈 commit。

    `work_repos` 的每一筆是 `repository.list_work_repos()` 回傳的 dict
    （`id`／`name`／`repo_path`／`author_emails`／`branch_filter`／`enabled`），
    但這裡**不 import repository**，只吃 Mapping。`enabled` 為假的直接跳過
    且不列進 `skipped`——那是使用者自己關的，不是壞掉。
    """
    evidence: List[Evidence] = []
    scans: List[RepoScan] = []
    skipped: List[SkippedSource] = []

    for row in work_repos or ():
        name = str(row.get("name") or row.get("repo_path") or "(未命名)")
        if not row.get("enabled", True):
            continue
        try:
            found, scan = collect_repo_commits(
                repo_path=str(row.get("repo_path") or ""),
                repo_name=name,
                author_emails=[str(e) for e in (row.get("author_emails") or [])],
                branch_filter=[str(b) for b in (row.get("branch_filter") or [])],
                work_date=work_date,
                tz_offset=tz_offset,
                repo_id=row.get("id"),
            )
        except subprocess.TimeoutExpired:
            skipped.append(
                SkippedSource(
                    kind="repo",
                    label=name,
                    reason=f"git log 逾時（{cfg.WORKLOG_GIT_TIMEOUT} 秒）",
                    ref_id=row.get("id"),
                )
            )
            continue
        except ChatPulseError as exc:
            skipped.append(
                SkippedSource(
                    kind="repo", label=name, reason=str(exc), ref_id=row.get("id")
                )
            )
            continue
        evidence.extend(found)
        scans.append(scan)

    evidence.sort(key=lambda e: (e.occurred_at, e.ref))
    return evidence, scans, skipped


# --------------------------------------------------------------------------
# Chat 證據
# --------------------------------------------------------------------------


def resolve_chat_space_ids(
    *,
    pinned_space_ids: Sequence[str] = (),
    mention_space_ids: Sequence[str] = (),
    limit: Optional[int] = None,
) -> List[str]:
    """決定要掃哪些 Space：釘選的 ∪ 當天有 Mention 命中的。

    🔴 **這個函式存在的唯一理由是「絕不掃全部的 Space」。** 整個帳號有
    四百多個，逐一 `list_messages_since` 是幾百次 HTTP，SSE 會先逾時。
    呼叫端要嘛給母體，要嘛得不到 Chat 證據——沒有「全掃」這個選項。

    釘選的排前面（那是使用者主動選的），Mention 命中的補在後面，最後套
    `WORKLOG_MAX_SPACES` 硬上限。
    """
    cap = limit or cfg.WORKLOG_MAX_SPACES
    out: List[str] = []
    seen = set()
    for group in (pinned_space_ids or (), mention_space_ids or ()):
        for raw in group:
            space_id = (raw or "").strip()
            if not space_id or space_id in seen:
                continue
            seen.add(space_id)
            out.append(space_id)
    return out[:cap]


def _message_is_mention(message: Mapping[str, Any]) -> bool:
    """這則發言有沒有 @ 人。

    ⚠️ **歧義點**：計畫 §5 的 `chat[].is_mention` 沒有寫清楚主詞。這裡採用
    「這則訊息 @ 了某個人」（`USER_MENTION` ＋ `userMention.type == MENTION`，
    判準與 `core/mentions.py` 一致，只是不比對是誰）。理由是本模組撈的是
    **Viewer 自己發的話**，而他 @ 了人的那幾則多半在交辦或回報，比自言自語
    更像「做了一件事」。若之後要改成「Viewer 被 @ 的那則」，改這一個函式即可。
    """
    for ann in message.get("annotations") or []:
        if ann.get("type") != "USER_MENTION":
            continue
        if ((ann.get("userMention") or {}).get("type")) == "MENTION":
            return True
    return False


def _message_text(message: Mapping[str, Any]) -> str:
    return (message.get("text") or message.get("formattedText") or "").strip()


#: 每次向 `list_messages_since` 要幾則。`GoogleChatClient` 內部以 100 一頁自己
#: 翻，所以這個值等於「一次外層呼叫最多翻幾頁」。取太大會在訊息稀疏的 Space
#: 白白多打幾次 API，取太小則是外層多轉幾圈——200 是兩頁，兩邊都不難看。
_CHAT_SCAN_CHUNK = 200


def _scan_space_messages(
    client: Any,
    space_id: str,
    *,
    start: datetime,
    end: datetime,
) -> Tuple[List[Mapping[str, Any]], bool]:
    """把一個 Space 從日界起翻到**越過日界**為止，回 `(訊息, 掃描預算用完了嗎)`。

    🔴 **停止條件是「訊息時間越過日界」，不是「額度用完」。**
    `list_messages_since` 的 filter 只有 `createTime >`（沒有上界，見
    `docs/R1-findings.md:68`）、排序是 ASC（見 `docs/HANDOFF.md:147`），所以撈
    回來的是**整個 Space 的**訊息，不是 Viewer 的。用「額度用完」當停止條件的
    話，額度會被別人的閒聊吃光：2026-09-13 實測，一個從 09:00 起每 2 分鐘一則
    的聊天室，Viewer 當天 5 則發言只蒐集到 3 則——17:00 與 18:20 那兩則不見了，
    而 `skipped` 是空的、`message_count` 看起來就像那天只講了 3 句話。

    第二個回傳值只在「**還沒翻到日界就用完掃描預算**」時為真。翻到日界自然
    停下、或 API 已經沒有更多訊息，都是正常結束，不該讓 `truncated` 亮起來
    ——一個同時代表正常與異常的旗標，等於沒有旗標。
    """
    collected: List[Mapping[str, Any]] = []
    seen: set = set()
    # filter 是嚴格大於（`>=` 實測回 400），退一秒才不會掉整點發的那一則。
    cursor = start - timedelta(seconds=1)
    budget = cfg.WORKLOG_CHAT_SCAN_PER_SPACE

    while budget > 0:
        want = min(_CHAT_SCAN_CHUNK, budget)
        batch = list(client.list_messages_since(space_id, cursor, max_messages=want) or ())
        budget -= len(batch)

        newest: Optional[datetime] = None
        for message in batch:
            name = message.get("name") or ""
            if name:
                if name in seen:
                    continue
                seen.add(name)
            collected.append(message)
            created = parse_rfc3339(message.get("createTime") or "")
            if created is not None and (newest is None or created > newest):
                newest = created

        if len(batch) < want:
            return collected, False  # API 沒有更多訊息了
        if newest is None:
            # 整批的 createTime 都解析不出來 ⇒ 游標推不動。只能停，而且必須誠實：
            # 這不是「翻到日界」，是翻不下去了。
            return collected, True
        if newest >= end:
            return collected, False  # 已經越過日界，後面的都不屬於這一天
        cursor = newest

    return collected, True


def collect_chat_evidence(
    client: Any,
    space_ids: Sequence[str],
    *,
    work_date: str,
    tz_offset: str,
    google_user_id: str,
    space_names: Optional[Mapping[str, str]] = None,
) -> Tuple[List[Evidence], List[SpaceScan], List[SkippedSource]]:
    """撈 Viewer 當天在指定 Space 的發言。

    `client` 只需要有 `list_messages_since(space_id, since, max_messages=…)`
    （`GoogleChatClient` 的既有介面）——收 duck-typed 物件是為了讓測試餵
    假 client，不必碰真的 Google API。**同一個 Space 可能會呼叫它不只一次**：
    停止條件是翻到日界，不是額度用完（見 `_scan_space_messages`），所以假
    client 必須真的照 `since` 過濾、照 `max_messages` 截斷，否則模擬本身是錯的。

    `google_user_id` 是 `users/{id}` 形式，用來比對 `sender.name`。**沒帶就
    回空**：不比對發送者的話，整個 Space 別人的閒聊都會變成你的工時證據。

    訊息**內文不落地**（與 `mentions` 表同一條鐵律）：`Evidence` 只留
    `message_name`／`space_id`／時間／字數，顯示時由前端即時取回。
    `title` 放的是給人辨認用的**節錄**，不是完整內文。
    """
    canonical_tz = normalize_tz_offset(tz_offset)
    tz = tzinfo_of(canonical_tz)
    start, end = day_bounds(work_date, canonical_tz)
    names = dict(space_names or {})

    evidence: List[Evidence] = []
    scans: List[SpaceScan] = []
    skipped: List[SkippedSource] = []

    if not google_user_id:
        skipped.append(
            SkippedSource(
                kind="space",
                label="（全部）",
                reason="不知道你的 Google 使用者 id，無法分辨哪些發言是你的",
            )
        )
        return evidence, scans, skipped

    for space_id in space_ids or ():
        label = names.get(space_id) or space_id
        try:
            messages, scan_exhausted = _scan_space_messages(
                client, space_id, start=start, end=end
            )
        except ChatPulseError as exc:
            skipped.append(
                SkippedSource(kind="space", label=label, reason=str(exc), ref_id=space_id)
            )
            continue

        mine: List[Evidence] = []
        for message in messages:
            if ((message.get("sender") or {}).get("name")) != google_user_id:
                continue
            created = parse_rfc3339(message.get("createTime") or "")
            if not _within(created, start, end):
                continue
            assert created is not None
            text = _message_text(message)
            if len(text) < cfg.WORKLOG_MIN_CHAT_CHARS:
                continue
            message_name = message.get("name") or ""
            if not message_name:
                continue
            is_mention = _message_is_mention(message)
            mine.append(
                Evidence(
                    kind="chat",
                    ref=f"chat:{message_name}",
                    occurred_at=created.astimezone(tz).isoformat(),
                    title=_excerpt(text),
                    source_label=label,
                    terms=tuple(extract_terms(text)),
                    weight_input=chat_weight_input(len(text), is_mention),
                    space_id=space_id,
                    message_name=message_name,
                    is_mention=is_mention,
                    char_count=len(text),
                )
            )

        # 自己一天在同一個 Space 發言多到超過保留上限，也是「有東西沒進來」。
        # 先排序再截斷，同一天重產兩次才會得到同一份草稿（理由同 commit 那側）。
        mine.sort(key=lambda e: (e.occurred_at, e.ref))
        over_cap = len(mine) > cfg.WORKLOG_MAX_CHAT_MESSAGES_PER_SPACE
        if over_cap:
            mine = mine[: cfg.WORKLOG_MAX_CHAT_MESSAGES_PER_SPACE]
        evidence.extend(mine)

        scans.append(
            SpaceScan(
                space_id=space_id,
                space_name=label,
                message_count=len(mine),
                truncated=scan_exhausted or over_cap,
            )
        )

    evidence.sort(key=lambda e: (e.occurred_at, e.ref))
    return evidence, scans, skipped


#: 證據欄要的是「認得出是哪一則」，不是完整內文。節錄長度刻意短。
_EXCERPT_CHARS = 80


def _excerpt(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= _EXCERPT_CHARS:
        return flat
    return flat[: _EXCERPT_CHARS - 1] + "…"


# --------------------------------------------------------------------------
# 一天份的總入口
# --------------------------------------------------------------------------


def collect_day_evidence(
    *,
    work_date: str,
    tz_offset: str,
    work_repos: Iterable[Mapping[str, Any]] = (),
    chat_client: Any = None,
    space_ids: Sequence[str] = (),
    google_user_id: str = "",
    space_names: Optional[Mapping[str, str]] = None,
) -> DayEvidence:
    """把兩種來源合成一天份的總帳。

    局部失敗一律降級成 `skipped`，只有「日期或偏移本身非法」才拋——
    那兩個錯了的話，整份草稿的前提就不成立，繼續產只會產出一份算在錯的
    一天的工時。

    Chat 那一段給不給 `chat_client` 都可以：SSE 端點在使用者沒有釘選任何
    Space 時本來就只有 git 證據。
    """
    canonical_tz = normalize_tz_offset(tz_offset)
    start, end = day_bounds(work_date, canonical_tz)

    git_evidence, repo_scans, skipped = collect_git_evidence(
        work_repos, work_date=work_date, tz_offset=canonical_tz
    )

    chat_evidence: List[Evidence] = []
    space_scans: List[SpaceScan] = []
    if chat_client is not None and space_ids:
        chat_evidence, space_scans, chat_skipped = collect_chat_evidence(
            chat_client,
            space_ids,
            work_date=work_date,
            tz_offset=canonical_tz,
            google_user_id=google_user_id,
            space_names=space_names,
        )
        skipped = skipped + chat_skipped

    merged = sorted(git_evidence + chat_evidence, key=lambda e: (e.occurred_at, e.ref))
    return DayEvidence(
        work_date=parse_work_date(work_date).strftime("%Y-%m-%d"),
        tz_offset=canonical_tz,
        since=start.isoformat(),
        until=end.isoformat(),
        evidence=tuple(merged),
        repos=tuple(repo_scans),
        spaces=tuple(space_scans),
        skipped=tuple(skipped),
    )


# --------------------------------------------------------------------------
# 「我的工作 repo」的登錄驗證
#
# 這兩個函式原本暫放在 dashboard/api/server.py（上一階段的邊界限制）。
# 它們讀 git、不讀 HTTP request，本來就屬於這裡。
# --------------------------------------------------------------------------

#: `detected_emails` 掃幾筆 commit。放在 config 讓它與其他 WORKLOG_* 同一處調。
WORK_REPO_EMAIL_SCAN_COMMITS = cfg.WORKLOG_EMAIL_SCAN_COMMITS


def verify_work_repo(repo_path: str) -> Dict[str, Any]:
    """確認路徑還是一個可讀的 git repo，順便回報偵測到的作者信箱。

    回傳 `{"ok", "error", "head_sha", "detected_emails"}`，**不拋錯**：
    設定頁要能同時顯示「這筆登錄」與「它現在壞了」，拋錯會讓整份清單拿不到。
    真正要擋住動作的地方（產草稿時）才拋 `WorkRepoUnavailable`——那是
    `ensure_work_repo` 的工作。

    git 的呼叫慣例直接沿用 `core.code_search._git`，不另外複製一份：那裡把
    argv list（repo_path 是使用者輸入，不組 shell 字串）、`GIT_OPTIONAL_LOCKS=0`
    （不去搶使用者的 index.lock）、`GIT_TERMINAL_PROMPT=0`（憑證提示會把請求
    卡到逾時）、逾時綁在一起，複製出去就是第二個會各自漂移的地方。

    **全程不碰工作區**（ADR-0006 鐵律）：`rev-parse` 與 `log` 只讀 refs 與
    object database，不 checkout、不動 index。

    `detected_emails` 的去重在 **Python** 做，不在 shell 裡接 `| sort -u`——
    接管線就得組 shell 字串，而 repo_path 是使用者輸入。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "error": None,
        "head_sha": None,
        "detected_emails": [],
    }
    if not repo_path or not os.path.isdir(repo_path):
        result["error"] = "路徑不存在或不是資料夾"
        return result
    proc = code_search._git(repo_path, "rev-parse", "--git-dir")
    if proc.returncode != 0:
        result["error"] = "這個路徑不是 git repo"
        return result
    result["ok"] = True

    head = code_search._git(repo_path, "rev-parse", "--short", "HEAD")
    if head.returncode == 0 and head.stdout.strip():
        result["head_sha"] = head.stdout.strip()
    # 沒有 HEAD 不是錯誤：剛 git init、還沒有任何 commit 的 repo 也是 repo。
    # 回 head_sha=None 讓設定頁自己決定要不要提醒，不要在這裡判死。

    log_proc = code_search._git(
        repo_path,
        "log",
        "--all",
        f"-n{WORK_REPO_EMAIL_SCAN_COMMITS}",
        "--format=%aE",
    )
    if log_proc.returncode == 0:
        emails: List[str] = []
        seen = set()
        for line in log_proc.stdout.splitlines():
            addr = line.strip()
            # 保留原始大小寫但用小寫比對：git 的信箱實務上就是大小寫混寫，
            # 兩份只差大小寫的清單對使用者沒有意義。
            if not addr or addr.lower() in seen:
                continue
            seen.add(addr.lower())
            emails.append(addr)
        # `git log` 預設就是時間由新到舊，所以這份清單的順序是「最近用過的
        # 信箱排前面」——那正是使用者要填進 author_emails 的第一個。
        result["detected_emails"] = emails
    return result


def verify_and_record_work_repo(
    work_repo: Mapping[str, Any],
    *,
    record: Optional[Callable[[Optional[str]], None]] = None,
    on_record_error: Optional[Callable[[BaseException], None]] = None,
) -> Dict[str, Any]:
    """驗證並把結果寫回，回傳帶 `verification` 的 dict。

    形狀與用途比照 `_verify_and_record`（參考專案那一組），但驗的東西不同：
    那邊還要逐一確認每個環境對應的分支存不存在，這邊沒有釘死的分支可以驗。

    **寫回是靠傳進來的 `record` callable，不是自己 import repository**：
    這個模組要能在沒有資料庫的情況下測（同 `code_search` 的慣例）。
    寫回失敗不影響回應——設定頁拿不到驗證結果，比拿不到整份清單糟得多。
    """
    verification = verify_work_repo(str(work_repo.get("repo_path") or ""))
    error: Optional[str] = verification.get("error")
    if record is not None:
        try:
            record(error)
        except Exception as exc:  # noqa: BLE001 - 寫回失敗不該讓設定頁整頁壞掉
            if on_record_error is not None:
                on_record_error(exc)
    out = dict(work_repo)
    out["verification"] = verification
    out["last_verify_error"] = error
    return out
