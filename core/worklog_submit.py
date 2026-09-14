"""工時草稿送出的純判斷（ADR-0008 的送出環節）。

**為什麼這幾段要獨立成一個模組**：整條送出流程裡真正「算錯了不會有人發現」
的就是這幾段判斷——

* 未指派閘沒擋住，那天的工時會變成不完整，而畫面上沒有任何訊號；
* 去重比對漏判，ZPlanner 那邊就多一筆重複工時，**月結之後刪不掉**，
  只能走 correction-request（要人審核，不是一次呼叫就結束）；
* 例外分類判錯方向，`failed` 會誘使人再按一次送出，於是同一筆填兩遍。

三者都是「送出去之後才會發現」的錯，而逐筆送出的迴圈要打 API、要落 DB，
那一層測不到這些判斷。所以判斷留在這裡，編排留在 `dashboard/api/server.py`。

**這個模組不碰 DB、不碰網路，不 import repository 也不 import 任何 client**：
比照 `worklog_allocation`／`worklog_clustering`／`worklog_evidence`。輸入是
呼叫端整理好的值（草稿 entries 的 dict、`ZPlannerClient.export_worklogs()`
回來的 CSV 列），輸出是判斷結果。四支 worklog 模組只要有一支開始有副作用，
「純邏輯必須單測得到」這條界線就守不住了。

**沒有處理的事**：不決定送出順序、不呼叫 ZPlanner、不寫回 DB、不決定 HTTP
狀態碼（「只要送出任何一筆就回 200」是路由層的事）。這裡也不判斷 issue 存不
存在或有沒有填工時的權限——那是 ZPlanner 的權威，問它比猜它可靠。
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import (
    ChatPulseError,
    InvalidParameter,
    ZPlannerApiError,
    ZPlannerUnavailable,
    ZPlannerWriteIndeterminate,
)


# --------------------------------------------------------------------------
# 狀態常數
#
# 值本身是 DB 欄位的一部分（見 core/db.py 的 worklog_draft_entries.state 與
# worklog_drafts.status）。這裡給名字是為了讓判斷式讀得懂，不是為了讓它可設定
# ——改掉任何一個字串等於改 schema 的語意，不是改這個模組。
# --------------------------------------------------------------------------

#: worklog_draft_entries.state 的五態
STATE_PENDING = "pending"
STATE_SKIPPED = "skipped"
STATE_SUBMITTED = "submitted"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"

#: worklog_drafts.status 的三態
DRAFT_STATUS_DRAFT = "draft"
DRAFT_STATUS_PARTIAL = "partial"
DRAFT_STATUS_SUBMITTED = "submitted"

#: 「這一筆已經結束了，不該再送」的狀態。`submitted` 顯然如此；`skipped` 是
#: 人或去重判過「不要送」，把它重新算進送出對象等於推翻那個決定。
_TERMINAL_STATES = (STATE_SUBMITTED, STATE_SKIPPED)


# --------------------------------------------------------------------------
# 資料結構
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """這次要處理哪幾筆（`entry_ids` 的解析結果）。"""

    selected: Tuple[Mapping[str, Any], ...]  # 照草稿原本的 seq 排，不照 entry_ids 的順序
    unknown_ids: Tuple[int, ...]  # 指名了、但這份草稿裡沒有的 id；呼叫端要當 400 擋掉


@dataclass(frozen=True)
class EntryRef:
    """指回草稿某一筆的最小識別，用在「哪幾筆擋住了送出」這種回報。

    只帶 id 與 seq，**不帶整筆 entry**：entry 身上有證據 ref 與候選清單，塞進
    錯誤訊息只會把真正要看的東西淹掉。`seq` 一定要在——只給 entry_id 的話，
    前端得自己回草稿裡反查才畫得出「是這張卡要補」。
    """

    entry_id: int
    seq: int

    def to_dict(self) -> Dict[str, Any]:
        return {"entry_id": self.entry_id, "seq": self.seq}


#: 未指派閘回傳的就是 `EntryRef`。留這個名字是因為「未指派的那一筆」在讀
#: 呼叫端的程式碼時比「一個 entry 參照」具體。
UnassignedEntry = EntryRef


@dataclass(frozen=True)
class DuplicateMatch:
    """這一筆在 ZPlanner 當天已經有一模一樣的工時了（同 issue ＋ 同時數）。

    ⚠️ **被跳過的筆一定要列進回應**（規劃「已知取捨與風險」）：比對只看
    issue ＋ hours，所以「同一天、同一個 issue、同樣時數」的第二筆合法工時
    會被誤殺。誤殺是刻意選的方向（寧可漏送也不要造出刪不掉的重複），但前提
    是人看得見它被跳過了——靜默跳過的話，那筆工時就這樣消失了。

    `remote_*` 是比對到的那一列遠端工時，帶著讓人一眼判斷是不是誤殺；兩邊的
    時數都留**原字串**，人才看得出 `"2.00"` 與 `"2"` 只是刻度差異。

    **沒有遠端 worklog id**：匯出的 CSV 根本不含 id 欄位（見
    `zplanner_client._EXPORT_COLUMNS`），而那正好與 D-9(b) 一致——那筆不是
    我們建的，不該被認領到 `zplanner_worklog_id` 上。
    """

    entry_id: int
    seq: int
    issue_key: str
    hours: str  # 草稿這一筆的時數原字串
    remote_hours: str  # 遠端那一列的時數原字串
    remote_note: str  # 遠端那筆的備註。判斷「是不是我自己填的另一件事」就靠它
    remote_issue_title: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "seq": self.seq,
            "issue_key": self.issue_key,
            "hours": self.hours,
            "existing": {
                "hours": self.remote_hours,
                "note": self.remote_note,
                "issue_title": self.remote_issue_title,
            },
        }


# --------------------------------------------------------------------------
# 正規化
# --------------------------------------------------------------------------


def _hours_key(value: Any) -> Optional[Decimal]:
    """把時數轉成可以互相比較的值；認不出來回 `None`。

    ⚠️ **這是去重唯一會漏判的地方，也是規劃的風險段唯一點名的。**
    `export_worklogs()` 回的是 CSV 且刻意不轉型（見該方法的 docstring），所以
    遠端可能是 `"2"`，本地是 `format_hours()` 產出的 `"2.00"`——兩邊直接用
    `==` 比就漏判，然後那天多出一筆 2 小時的工時，而它月結後刪不掉。

    用 `Decimal` 而不是 `float`：float 在這裡看起來也能用（`"2"` 與 `"2.0"`
    轉 float 是相等的），但那要靠「哪些十進位字串轉 float 之後仍然相等」這個
    論證撐著，而論證只要有一天錯一次，代價是一筆刪不掉的重複工時。`Decimal`
    從十進位字串精確展開，相等與雜湊都是數值語意（`Decimal("2")` 等於
    `Decimal("2.0")`），不需要那個論證。

    🔴 **認不出來一律回 `None`，不回 `Decimal(0)` 之類的預設值。** 回預設值會讓
    一列讀不懂的遠端資料跟一筆真的 0 小時工時對上，把本地一筆正常工時吃掉——
    而「為什麼那天少一筆」不會出現在任何地方。

    ⚠️ **布林值不算數字。** `isinstance(True, int)` 在 Python 是 True，不擋的話
    `True` 會被當成 1 小時（`worklog_allocation` 的測試裡有同一條，這個 codebase
    已經被這件事咬過一次）。
    """
    if isinstance(value, bool):
        return None
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    # Decimal("NaN") 與 Decimal("Infinity") 都是合法的 Decimal，卻不是時數；
    # 而 NaN 連「等於自己」都不成立，放它進比對會得到無聲的假陰性。
    return amount if amount.is_finite() else None


def _issue_key(value: Any) -> str:
    """把 issue key 正規化成比對用的形式（去空白、忽略大小寫）。

    忽略大小寫是**刻意往「多跳過」的方向倒**：判成重複只是漏送一筆（人看得見，
    自己補即可），判成不重複則是多一筆刪不掉的工時。兩種錯的代價不對稱時，
    要往可回復的那一邊倒。
    """
    return str(value or "").strip().casefold()


# --------------------------------------------------------------------------
# 選取要處理的筆（AC-7 的單筆重打）
# --------------------------------------------------------------------------


def select_entries(
    entries: Sequence[Mapping[str, Any]],
    entry_ids: Optional[Iterable[Any]] = None,
) -> Selection:
    """決定這次要處理草稿裡的哪幾筆。

    `entry_ids` 是 `None` 代表整批送；給了清單就只處理指名的那幾筆
    （AC-7 的「failed／unknown 單筆重打」走同一支端點，用 body 欄位表達變體）。

    ⚠️ **`[]` 與 `None` 是兩件事**：前者是「一筆都沒選」，後者才是「整批」。
    混為一談的話，前端送一個空陣列就會意外把整天都送出去。

    ⚠️ **順序照草稿的 seq，不照 `entry_ids` 的順序。** 送出順序要和畫面上看到
    的順序一致，否則「送到第 3 筆失敗」這句話對不上任何一張卡——而那是使用者
    在混合狀態下唯一能拿來定位問題的線索。

    指名了但草稿裡沒有的 id 放進 `unknown_ids` 回去，**不是靜默忽略**：那代表
    前端拿著別份草稿（或已經被重產清掉）的 id 在送，靜默忽略會讓使用者以為
    重打過了，而實際上一筆都沒動。

    id 轉不成整數時直接拋 `InvalidParameter`，不併進 `unknown_ids`：那是呼叫端
    傳錯型別（契約寫的是整數陣列），與「這份草稿沒有這一筆」是兩件不同的事，
    混在一起會讓前端的 bug 看起來像資料過期。
    """
    rows = list(entries or [])
    if entry_ids is None:
        return Selection(selected=tuple(rows), unknown_ids=())

    wanted: List[int] = []
    for raw in entry_ids:
        try:
            wanted.append(int(raw))
        except (TypeError, ValueError):
            raise InvalidParameter(f"entry_ids 只收整數，收到 {raw!r}") from None

    available = {int(row["id"]) for row in rows if row.get("id") is not None}
    wanted_set = set(wanted)
    selected = tuple(
        row
        for row in rows
        if row.get("id") is not None and int(row["id"]) in wanted_set
    )
    # dict.fromkeys 去重但保留順序——回報裡的 id 順序要和使用者送進來的一致。
    unknown = tuple(dict.fromkeys(eid for eid in wanted if eid not in available))
    return Selection(selected=selected, unknown_ids=unknown)


# --------------------------------------------------------------------------
# AC-1：未指派 issue 的閘
# --------------------------------------------------------------------------


def find_unassigned(
    entries: Sequence[Mapping[str, Any]],
    *,
    entry_ids: Optional[Iterable[Any]] = None,
) -> Tuple[UnassignedEntry, ...]:
    """找出「還沒選 issue 且不是 skipped」的筆。回傳非空就要擋下整批。

    為什麼是擋下整批而不是跳過那幾筆照送其餘：ADR-0008 規定歸屬由人選，
    部分送出會讓草稿停在半送出狀態，而**使用者看不出哪幾筆送了**。

    回的是**哪幾筆**而不是一個布林：呼叫端要把筆數與位置放進 `error.detail`，
    前端才不必從中文句子裡用正則挖數字。

    🔴 **判斷看的是 `issue_id`，不是 `issue_key`，也不是兩者的 OR。** 能不能填
    工時是 `issue_id` 說了算（`create_worklog` 吃的是它），`issue_key` 只是給人
    看的字串。三種判準攤開比較，只有「只看 id」兩個方向都對：

    | 判準 | id 有、key 無 | key 有、id 無 |
    |------|---------------|----------------|
    | 兩個都要 | ❌ 誤擋（人明明選過了） | 擋下（對） |
    | `id or key` | 放行（對） | ❌ 放行 → 那筆必然 failed ⇒ 部分送出 |
    | **只看 `id`** | 放行（對） | 擋下（對） |

    ⚠️ 這與重產守門（`_refuse_destructive_regeneration`）的 `issue_id or issue_key`
    **刻意不同**，不要看到那支就跟著抄：它問的是「人選過了沒」（兩個都算選過），
    這道閘問的是「送得出去嗎」。把這裡放寬成 OR，「只有 key」那筆會在迴圈裡變
    `failed` 而其餘照送——正是 D-3 明文否決的部分送出。

    （這張表寫在這裡是因為它反覆被推翻過：閘紅了就把判準改成 OR 是最順手的
    修法，而它會靜默地把 D-3 的保證拆掉。）

    ⚠️ **帶 `entry_ids` 時只檢查選中的那幾筆。** 不這樣做的話，當天別處有一筆
    沒選專案，`failed` 的那筆就永遠重打不了——閘會每次都擋在它前面，而人根本
    沒打算送那一筆。

    ⚠️ 已經是 `skipped` 的筆不算未指派：被併成 0 小時、或上一輪去重跳過的筆
    本來就不會送出去，把它們算進閘裡會讓整天永遠送不出去，而使用者看到的訊息
    是「請先選專案」，指著一筆根本不需要選的空筆。
    """
    selected = select_entries(entries, entry_ids).selected
    return tuple(
        EntryRef(entry_id=int(row["id"]), seq=int(row.get("seq") or 0))
        for row in selected
        if not row.get("issue_id")
        and str(row.get("state") or STATE_PENDING) != STATE_SKIPPED
    )


def find_missing_hours(
    entries: Sequence[Mapping[str, Any]],
    *,
    entry_ids: Optional[Iterable[Any]] = None,
) -> Tuple[EntryRef, ...]:
    """找出「還沒有時數」的筆。與未指派閘同樣是送出前的整批擋下。

    ⚠️ **這不是罕見的邊界情況。** 產草稿時省略 `total_hours` 就不做分配，那時候
    整份草稿的 `hours` 一律是 null——那是正常流程的中間狀態，不是壞掉的資料。

    為什麼要擋在前面，而不是讓它們在迴圈裡各自變成 `failed`：這種筆送到
    `create_worklog` 會在**發出 HTTP 之前**就被 `_require_hours` 擋下，所以確實
    不會造成重複工時。但那樣等於部分送出——D-3 明文否決過：那天的工時會變成
    不完整，而畫面上沒有任何訊號說明少了什麼。

    判準是 `_hours_key()` 認不認得（涵蓋 null、空字串、亂碼），**不是「等不等於
    0」**：`"0.00"` 是一個有意義的值（被併掉的那一筆），它要不要送出由 state
    決定，不由這道閘決定。

    已經結束的筆（`submitted`／`skipped`）不算：它們不會再送出去，沒有時數也
    擋不住任何人。

    ⚠️ **已知且刻意的殘留缺口**：格式讀得懂、卻違反 ZPlanner 欄位規則的值仍然
    會漏過這道閘（`_require_hours` 的 pattern 只收三位整數兩位小數，所以
    `"1000.5"` 這裡認得、ZPlanner 那邊不收），那一筆會變成單筆 `failed`。
    **不把那個 pattern 抄進來是刻意的**：抄過來等於把 client 的驗證規則複製一份，
    兩邊遲早不同步，而不同步的那天這裡會擋掉 ZPlanner 其實收得下的值——整天
    送不出去，比漏掉一筆嚴重。這道閘要擋的是「使用者還沒填」這個正常中間狀態，
    不是替 client 做欄位驗證。
    """
    selected = select_entries(entries, entry_ids).selected
    return tuple(
        EntryRef(entry_id=int(row["id"]), seq=int(row.get("seq") or 0))
        for row in selected
        if str(row.get("state") or STATE_PENDING) not in _TERMINAL_STATES
        and _hours_key(row.get("hours")) is None
    )


# --------------------------------------------------------------------------
# AC-3：送出前的去重
# --------------------------------------------------------------------------


def find_duplicates(
    entries: Sequence[Mapping[str, Any]],
    existing_worklogs: Sequence[Mapping[str, Any]],
    *,
    entry_ids: Optional[Iterable[Any]] = None,
) -> Tuple[DuplicateMatch, ...]:
    """比對草稿與 ZPlanner 當天既有的工時，挑出「已經填過」的筆。

    `existing_worklogs` 就是 `ZPlannerClient.export_worklogs(date, date)` 的回傳
    值：鍵為 `date`／`user_name`／`project_code`／`issue_key`／`issue_title`／
    `hours`／`note`，**值全是字串**。

    **比對 issue ＋ hours，不比對 note**（D-2）：note 會被使用者編輯過（系統產
    的是「fix: PEI-133 匯出逾時」，人送出前改成「處理匯出逾時」），把它算進比對
    會漏判——而漏判造出來的重複工時在月結後刪不掉。比對 note 的那個版本看起來
    比較精確，代價卻是不可逆的。

    **哪些筆參與比對**：帶著 `zplanner_worklog_id` 的由 AC-2 直接跳過，
    `skipped`／`submitted` 的人已經說不送了——把它們算進來只會在回應裡多出幾筆
    看起來像「被去重擋下」的假訊息，而人會以為自己少送了一筆。沒選 issue 的交給
    AC-1 的閘，同一個問題不要有兩種說法。
    `unknown` 的筆**會**參與，而且這是刻意的：那種筆的意思是「可能已經寫進去
    了」，而對帳的唯一管道正是 export。真的寫進去了，這裡就會比對到並跳過——
    不必先自動重試一次才發現重複。

    ⚠️ **刻意不用日期再濾一次。** 呼叫端查的就是當天（`date_from == date_to`），
    再濾一次看起來更保險，實際上是把「CSV 的日期字面與我們的 `YYYY-MM-DD` 不同
    格式」變成一次**全部漏判**——濾光了就等於「當天沒有任何工時」，然後整天重填
    一遍。多濾一次的收益是 0，失敗模式卻是最貴的那個。

    兩種「判不出來」一律拋，寧可整批停下來也不要帶著沒把握的比對去送
    （「認不出來就拋，絕不猜」；D-9(a) 的「不得查不到就當沒重複照送」同理）：

    * 某筆選了 `issue_id` 卻沒有 `issue_key` → `InvalidParameter`。少了 key 就
      沒有東西可以跟遠端對，回「沒有重複」會讓它安靜送出去，而去重是唯一擋得住
      重複工時的那一道。
    * 遠端某列的 issue **正是我們要送的那個**，它的 `hours` 卻讀不出來 →
      `ZPlannerApiError`。判不出時數就判不出是不是重複。
      ⚠️ 只有相關的 issue 才拋：遠端那一天可能有幾十列與這份草稿完全無關的工時，
      為它們的格式問題停掉整批是沒有道理的——那會讓一列別人專案的壞資料害這個人
      今天一筆工時都填不了。
    """
    selected = select_entries(entries, entry_ids).selected

    # 先照 issue 分桶。⚠️ **一個 issue 要留住所有列，不是只留最後一列**：一天
    # 在同一個 issue 上有好幾筆是常態（早上兩小時、下午一小時），只留一列的話
    # 其餘那幾筆的時數就永遠對不上 → 漏判 → 重複工時。
    remote_by_issue: Dict[str, List[Mapping[str, Any]]] = {}
    for row in existing_worklogs or []:
        remote_by_issue.setdefault(_issue_key(row.get("issue_key")), []).append(row)

    out: List[DuplicateMatch] = []
    for entry in selected:
        if entry.get("zplanner_worklog_id"):
            continue
        if str(entry.get("state") or STATE_PENDING) in _TERMINAL_STATES:
            continue
        if not entry.get("issue_id"):
            continue

        key = _issue_key(entry.get("issue_key"))
        if not key:
            raise InvalidParameter(
                f"草稿第 {entry.get('seq')} 筆選了 issue_id={entry.get('issue_id')}，"
                "卻沒有 issue_key，沒有辦法跟 ZPlanner 既有的工時比對。"
                "不比對就送出可能造成重複工時，而重複工時月結後刪不掉。"
            )

        candidates = remote_by_issue.get(key, [])
        for row in candidates:
            if _hours_key(row.get("hours")) is not None:
                continue
            raise ZPlannerApiError(
                f"ZPlanner 匯出的工時裡，{row.get('issue_key')!r} 有一列的時數"
                f"讀不出來（{row.get('hours')!r}），無法判斷草稿第 "
                f"{entry.get('seq')} 筆是不是重複。整批停下來，"
                "不要在判不出重複的情況下送出。",
                detail=repr(dict(row))[:400],
            )

        hours = _hours_key(entry.get("hours"))
        if hours is None:
            # 本地時數壞掉或還沒填。這裡不擋——它送到 ZPlanner 之前會先被
            # `_require_hours` 擋下（那一筆會變成 failed，訊息也講得清楚），
            # 在這裡多攔一次只會讓同一個錯誤有兩種說法。
            continue

        for row in candidates:
            if _hours_key(row.get("hours")) != hours:
                continue
            out.append(
                DuplicateMatch(
                    entry_id=int(entry["id"]),
                    seq=int(entry.get("seq") or 0),
                    issue_key=str(entry.get("issue_key") or ""),
                    hours=str(entry.get("hours") or ""),
                    remote_hours=str(row.get("hours") or ""),
                    remote_note=str(row.get("note") or ""),
                    remote_issue_title=str(row.get("issue_title") or ""),
                )
            )
            # 一筆草稿對到一列就夠了，**遠端那一列不會被「用掉」**：草稿裡同
            # issue 同時數的第二筆也會對到同一列而被跳過——那正是規劃寫明接受
            # 的誤殺（寧可漏送也不要重複），不是這裡少寫了配對邏輯。
            break
    return tuple(out)


# --------------------------------------------------------------------------
# AC-5／AC-6：例外 → state
# --------------------------------------------------------------------------


def classify_write_error(error: BaseException) -> str:
    """一筆送出失敗時，這筆該標成 `failed` 還是 `unknown`。

    判準只有一條，而且不是「錯誤嚴不嚴重」：

    > 🔴 **只有拿到 ZPlanner 的回應信封、而且 `code != 200`，才證明沒有寫進去。**

    那是 ZPlanner 應用層親口說「我拒絕了」。其餘任何失敗——逾時、連線被拒、
    HTTP 5xx、回應讀不出 `code`——都**證明不了**沒寫進去，一律 `unknown`。
    誤判的代價是不對稱的：把 `unknown` 當 `failed`，人看了會放心再按一次，
    ZPlanner 那邊就多一筆，而月結之後那筆刪不掉（只能走 correction-request）；
    把 `failed` 當 `unknown` 只是多要人對帳一次。

    對照表（型別的來源見 `core/zplanner_client._send` 與 `._request`）：

    * `ZPlannerWriteIndeterminate`：傳輸層就斷了，連請求有沒有送達都不知道 →
      `unknown`（這是它存在的唯一理由）。
    * `ZPlannerUnavailable`：HTTP 5xx／429 且沒有信封。⚠️ 反直覺，但代理層的
      502／504 完全可能發生在後端已經寫進去之後 → `unknown`。
    * `ZPlannerApiError`：連上了、也回了東西，但**讀不出 `code`**（非 JSON、
      HTTP 4xx 沒信封、缺 code 欄位）。⚠️ 這一類直覺上最像「明顯壞掉 ＝ 失敗」，
      所以特別容易歸錯邊；它自己的 docstring 就寫著「讀不出 code」等於「不知道
      成功了沒有」，而 `create_worklog` 也說「拿不到 id 就等同一筆孤兒工時」
      → `unknown`。
    * 其餘 `ChatPulseError`（`ZPlannerForbidden`、`ZPlannerInvalidParameter`、
      `ZPlannerNotFound`、`ZPlannerAuthError`、送出前就擋下的 `InvalidParameter`
      與 `ConfigurationError`…）：要嘛是 `code != 200` 的信封，要嘛根本還沒送出
      去 → `failed`。
    * 完全沒預期到的例外（`TypeError`、`KeyError` 之類）→ `unknown`。它可能發生
      在**請求送出之後**（例如解析回應時炸掉），當成 `failed` 等於鼓勵重送。
      認不出來的東西一律往「不要自動重送」那一邊倒。
    """
    if isinstance(error, (ZPlannerWriteIndeterminate, ZPlannerUnavailable, ZPlannerApiError)):
        return STATE_UNKNOWN
    if isinstance(error, ChatPulseError):
        return STATE_FAILED
    return STATE_UNKNOWN


# --------------------------------------------------------------------------
# D-9(c)：草稿整體的 status
# --------------------------------------------------------------------------


def derive_draft_status(entries: Sequence[Mapping[str, Any]]) -> str:
    """由各筆的 state 推出 `worklog_drafts.status`（`draft｜partial｜submitted`）。

    ⚠️ **要傳整份草稿的 entries，不是這次選中的那幾筆。** 只重打一筆就把整份
    草稿標成 submitted，週檢視會顯示這天填完了，而實際上還有幾筆沒送。

    `partial` 在這個 codebase 至今從未被寫入過，送出是第一處。它必須是獨立狀態
    （DDL 原話）：算成 `draft` 會讓人以為整天都還沒送，再按一次就把已經成功的
    那幾筆重複填一遍；算成 `submitted` 則會讓沒送成功的那幾筆永遠沒人管。

    三條規則。判準是**「ZPlanner 那邊有沒有可能多了東西」**，不是「這次跑得順
    不順」——status 的用途是告訴人「還能不能放心再按一次」：

    * 每一筆都 `submitted` 或 `skipped` → `submitted`。要等到這時候才轉，否則
      週檢視的 `totals.days_submitted` 永遠是 0。`skipped` 也算數——那幾筆的工時
      ZPlanner 那邊本來就有了（去重比對到的），只是不是我們建的；把它們算成
      「還沒完成」的話，只要那天有任何一筆重複，整天就再也標不成已送出。
    * 有任何一筆 `submitted`／`skipped`／`unknown`，但還沒到齊 → `partial`。
      ⚠️ **`unknown` 也算「動過了」，即使一筆都沒成功**：它的意思就是「可能已經
      寫進去了」，標成 `draft` 會讓人放心再按一次，而那正是
      `ZPlannerWriteIndeterminate` 整條規則要防的重複工時。
    * 其餘（只剩 `pending` 與 `failed`）→ `draft`，狀態不前進。`failed` 是**拿到
      `code != 200` 的信封**才會標的，也就是 ZPlanner 親口說「我沒寫」——那天
      那幾筆確實一筆都不存在，標成 `partial` 會讓畫面顯示「部分已送出」，
      而使用者於是不敢重送。

    一筆 entries 都沒有的草稿回 `draft`：`all()` 對空清單回 `True`，少了這個
    分支的話，空草稿會被判成 `submitted`，於是「這天什麼都沒有」被算成
    「這天填完了」。
    """
    states = [str(row.get("state") or STATE_PENDING) for row in entries or []]
    if not states:
        return DRAFT_STATUS_DRAFT
    if all(state in _TERMINAL_STATES for state in states):
        return DRAFT_STATUS_SUBMITTED
    moved = _TERMINAL_STATES + (STATE_UNKNOWN,)
    if any(state in moved for state in states):
        return DRAFT_STATUS_PARTIAL
    return DRAFT_STATUS_DRAFT
