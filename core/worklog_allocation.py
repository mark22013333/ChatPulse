"""工時草稿的時數分配（ADR-0008，Draft Worklog）。

把「Viewer 輸入的當天總時數」按證據權重分到 N 筆草稿上，每筆湊成 0.5 小時的
倍數。用最大餘數法（Largest Remainder Method）。

**為什麼決定性在這裡不是美觀問題**：這些數字會送進 ZPlanner，月結之後不能
直接改，要走 correction-request。同一天重新產一次草稿卻得到不同的分配，
Viewer 沒有任何辦法判斷哪一份才對——他會兩份都不信。所以平手時的三層
tie-break（weight → occurred_at → issue_key）是**規格的一部分**，不是效能或
可讀性的取捨，刪掉它等於讓 `dict` 的迭代順序決定別人的工時。

**為什麼全程用整數 unit 與 Fraction**：一個 unit ＝ 0.5 小時，分配過程只加減
整數，最後才乘回 0.5（0.5 是 2 的冪，乘回去與格式化都是精確的）。中途的
`exact_i` 與餘數則用 `fractions.Fraction` 從 float 的精確二進位值展開成有理數，
所以「餘數相等」判得出來是真的相等——用 float 比餘數的話，全等權那種本該
落到 tie-break 的情況會被最後一個 bit 的雜訊搶先決定，而那個雜訊看起來就像
一個合理的排名。

**這個模組不碰 DB、不碰網路、不 import repository**：呼叫端把證據整理好的
權重傳進來。分配是純算術，接不上任何外部狀態也能測——而它是整個 Draft
Worklog 裡唯一「算錯了不會有人發現」的一段。

**沒有處理的事**：這裡不決定每筆算在哪個 issue（那是 Viewer 選的，見規劃
§7 的六條反錨定規則），也不決定權重怎麼算出來（那是證據蒐集的事）。
`issue_key` 在這裡只有一個用途：當成 tie-break 的最後一層。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import WorklogAllocationError


# --------------------------------------------------------------------------
# 常數
#
# 刻意放在模組內而不是 core/config.py：這兩個值不是部署時可調的設定。
# 改掉 QUANTUM_HOURS 等於改變 ZPlanner 工時欄位的語意（它的刻度是半小時），
# 而讓它變成環境變數，只會讓「為什麼這台機器算出來的時數不一樣」變成一個
# 沒有人查得出來的問題。
# --------------------------------------------------------------------------

#: 量化單位：1 unit ＝ 0.5 小時。
QUANTUM_HOURS = 0.5

#: 每筆的保底時數。分不到這個數的那筆要嘛向別人借，要嘛被併掉（見 allocate）。
MIN_ENTRY_HOURS = QUANTUM_HOURS


# --------------------------------------------------------------------------
# 資料結構
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocationItem:
    """要分到時數的一筆草稿。

    只需要分配演算法真正用得到的三個欄位；證據內容、候選專案、note 文字那些
    都留在呼叫端，靠 `index` 對回去。
    """

    weight: float  # 證據權重。相對值，不必正規化成 1
    occurred_at: str = ""  # ISO 時間字串。tie-break 第二層，也決定併入誰
    issue_key: Optional[str] = None  # tie-break 第三層。這裡不驗證它存不存在


@dataclass(frozen=True)
class AllocationEntry:
    """一筆的分配結果。輸入的每一筆都會有一筆對應的輸出，順序相同。"""

    index: int  # 對應輸入 items 的位置
    issue_key: Optional[str]
    occurred_at: str
    weight: float
    units: int  # 幾個 0.5 小時
    allocated_hours: str  # 送進 ZPlanner 的字串，例如 "3.50"
    rounded_up: bool  # 有沒有拿到餘數的那 1 unit（UI 的「≈ 湊整 +0.5」）
    merged_into: Optional[int] = None  # 被併進哪一筆的 index；沒被併就是 None

    @property
    def is_submittable(self) -> bool:
        """0 小時的筆不送出，但也不靜默消失——前端仍要顯示成「0.0　不送出」。"""
        return self.units > 0


@dataclass(frozen=True)
class AllocationResult:
    """一天的分配結果。

    `total_hours` 是**量化後**的總時數，不一定等於呼叫端傳進來的
    `total_hours`——後者不是 0.5 的倍數時會被湊到最近的 0.5，並在
    `adjustments` 留一筆 `quantized`。`entries` 的時數相加恆等於這個值，
    這是這個模組唯一保證的不變量。
    """

    total_hours: str
    total_units: int
    entries: Tuple[AllocationEntry, ...]
    adjustments: Tuple[Dict[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        """SSE `done` 事件的 `allocation` 形狀。

        ⚠️ `PUT /worklogs/{date}` **不用**這個形狀（2026-09-25 更正舊說法）：PUT 是
        逐筆欄位更新（D-2），回的是整份草稿重讀後的樣子，沒有「一次分配結果」
        這個概念；分配結果是產生當下的暫態，只在 done 事件裡出現一次。
        """
        return {
            "total_hours": self.total_hours,
            "entries": [
                {
                    "index": e.index,
                    "issue_key": e.issue_key,
                    "allocated_hours": e.allocated_hours,
                    "rounded_up": e.rounded_up,
                    "merged_into": e.merged_into,
                }
                for e in self.entries
            ],
            "adjustments": [dict(a) for a in self.adjustments],
        }


# --------------------------------------------------------------------------
# 格式化
# --------------------------------------------------------------------------


def format_hours(units: int) -> str:
    """把 unit 數轉成 ZPlanner 要的時數字串。

    ZPlanner 的 hours 是**字串**且最多兩位小數（見 `core/zplanner_client`）。
    `units * 0.5` 在二進位裡是精確的（0.5 是 2 的冪），所以這裡不會有
    「2.99999」那種格式化後才看得到的誤差。
    """
    return f"{units * QUANTUM_HOURS:.2f}"


# --------------------------------------------------------------------------
# 輸入正規化與驗證
#
# 全部擋在算術之前。分配跑到一半才發現權重是 NaN 的話，錯誤會以「某一筆
# 莫名其妙拿到 0 小時」的形式出現，那是最難查的一種。
# --------------------------------------------------------------------------


def _require_finite(value: Any, what: str) -> float:
    """把數值欄位轉成有限的 float。bool 不算數字。

    `True` 是 `int` 的子類別，不擋的話「總時數 True」會安靜地變成 1 小時。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorklogAllocationError(f"{what}必須是數字，收到 {value!r}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise WorklogAllocationError(f"{what}必須是有限的數字，收到 {value!r}")
    return number


def _coerce_items(items: Sequence[Any]) -> List[AllocationItem]:
    """接受 AllocationItem 或 dict（證據蒐集那側產出的 cluster 形狀）。

    兩種都吃是為了讓呼叫端不必為了跑一次分配先把 cluster 轉成 dataclass；
    但欄位名不接受別名——`hours`、`w`、`time` 一律不認，因為認錯欄位的後果
    是「權重全部變成預設值」，而那會分出一份看起來很平均、其實沒有依據的工時。
    """
    if items is None:
        raise WorklogAllocationError("沒有可分配的項目")

    prepared: List[AllocationItem] = []
    for idx, raw in enumerate(items):
        if isinstance(raw, AllocationItem):
            item = raw
        elif isinstance(raw, Mapping):
            if "weight" not in raw:
                raise WorklogAllocationError(f"第 {idx + 1} 筆缺少 weight 欄位")
            item = AllocationItem(
                weight=raw["weight"],
                occurred_at=raw.get("occurred_at") or "",
                issue_key=raw.get("issue_key"),
            )
        else:
            raise WorklogAllocationError(
                f"第 {idx + 1} 筆必須是 AllocationItem 或 dict，收到 {type(raw).__name__}"
            )

        weight = _require_finite(item.weight, f"第 {idx + 1} 筆的 weight")
        if weight < 0:
            # 負權重不是「比較不重要」，它會讓權重總和往 0 靠甚至變號，
            # 分出來的時數可以是負的。這一定是上游算錯了，靜默改成 0
            # 只會讓那個 bug 留在證據蒐集那側繼續產生錯的權重。
            raise WorklogAllocationError(
                f"第 {idx + 1} 筆的 weight 不可為負數，收到 {item.weight!r}"
            )
        prepared.append(
            AllocationItem(
                weight=weight,
                occurred_at=(item.occurred_at or "").strip(),
                issue_key=item.issue_key,
            )
        )

    if not prepared:
        # 空清單不回一份空結果：那會讓呼叫端以為「分配成功，只是沒東西」，
        # 而 Viewer 輸入的那幾個小時就這樣消失了。沒有證據是 SSE 的
        # NO_EVIDENCE 要處理的事，不是這裡。
        raise WorklogAllocationError("沒有可分配的項目")
    return prepared


def _quantize_total(total_hours: Any) -> int:
    """把總時數換算成 unit 數。

    照規格用 `round(total / 0.5)`。要注意 Python 的 `round` 是**銀行家捨入**，
    所以剛好落在兩個刻度正中間的輸入會往偶數靠：7.25 → 7.00、7.75 → 8.00。
    這不是 bug（它決定性、而且湊整本來就會在 `adjustments` 留下 `quantized`
    讓 Viewer 看見），但改成 ROUND_HALF_UP 之前先讀這段註解，別以為是誤差。
    """
    hours = _require_finite(total_hours, "總時數")
    if hours < 0:
        raise WorklogAllocationError(f"總時數不可為負數，收到 {total_hours!r}")
    units = int(round(hours / QUANTUM_HOURS))
    if units <= 0:
        # 0 與「不足半小時」一起擋掉。硬分下去只會產出一份每筆都是 0.00 的
        # 草稿，Viewer 會以為系統壞了；直接說「湊不到半小時」他才知道要改輸入。
        raise WorklogAllocationError(
            f"總時數必須至少 {MIN_ENTRY_HOURS} 小時，收到 {hours:.2f}"
        )
    return units


# --------------------------------------------------------------------------
# 排序鍵（決定性的來源）
# --------------------------------------------------------------------------


def _occurred_key(value: str) -> Tuple[int, float, str]:
    """把 occurred_at 轉成可比較的鍵：(解析失敗?, epoch 秒, 原字串)。

    解析不出來的一律排到最後，而不是當成 1970 年排到最前面——讓一個壞掉的
    時間戳去搶別人的 0.5 小時，比讓它排在後面糟得多。

    沒有時區的字串一律當 UTC。這裡不猜 Viewer 的時區：同一批 item 的時間戳
    來源相同（都是 git commit 或都是 Chat 訊息），整批一起平移不影響**相對**
    先後，而這個鍵只用來比先後。
    """
    raw = (value or "").strip()
    if not raw:
        return (1, 0.0, "")
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return (1, 0.0, raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (0, moment.timestamp(), raw)


def _issue_key_sort(issue_key: Optional[str]) -> Tuple[int, str]:
    """沒有 issue_key 的排最後。None 與空字串等價。"""
    if not issue_key:
        return (1, "")
    return (0, issue_key)


@dataclass(frozen=True)
class _Row:
    """演算法內部用的一列。weight 用 Fraction，比較才是精確的。"""

    index: int
    item: AllocationItem
    weight: Fraction
    occurred: Tuple[int, float, str]
    issue_sort: Tuple[int, str]

    @property
    def priority(self) -> Tuple[Any, ...]:
        """三層 tie-break：weight 大者 → occurred_at 早者 → issue_key 字典序。

        第四層的 index 不在規格裡，但它是「完全相同的兩筆」唯一的分辨方式；
        沒有它，兩筆一模一樣的證據誰先拿到餘數就要看排序法的實作細節。
        """
        return (-self.weight, self.occurred, self.issue_sort, self.index)


# --------------------------------------------------------------------------
# 分配
# --------------------------------------------------------------------------


def _largest_remainder(
    rows: Sequence[_Row], units_total: int
) -> Tuple[List[int], List[int]]:
    """最大餘數法本體。回傳 (每筆的 unit 數, 每筆 floor 後的 unit 數)。

    floor 一起回傳是為了最後算 `rounded_up`：那個旗標的意思是「這筆比按比例
    該拿的多了 0.5」，要跟 floor 比才算得出來。拿「有沒有在這一步被 +1」當
    旗標會在保底階段被借走 1 unit 之後變成謊話（淨值根本沒變）。
    """
    weight_sum = sum((r.weight for r in rows), Fraction(0))

    units = []
    remainders = []
    for row in rows:
        exact = units_total * row.weight
        floor_units = exact // weight_sum  # Fraction // Fraction 回傳整數
        units.append(int(floor_units))
        remainders.append(exact - floor_units * weight_sum)

    floors = list(units)
    leftover = units_total - sum(units)
    order = sorted(
        range(len(rows)),
        key=lambda i: (-remainders[i],) + rows[i].priority,
    )
    for i in order[:leftover]:
        units[i] += 1
    return units, floors


def _borrow_budget(floor_units: int) -> int:
    """一筆最多能被借走幾個 unit。

    **這是 2026-09-13 審查發現的缺陷所加的護欄。** 在此之前唯一的條件是
    「借完不會歸零」，而 `_pick_lender` 每一輪都挑「當下最多的那筆」——
    那永遠是同一筆，於是它可以被連續借到只剩 0.5 小時。

    實測（8 小時、一個佔 82.8% 權重的主群 ＋ N 個三言兩語的小群）：

        小群數   主群應得   修復前實得
          12       6.62       2.00
          16       6.26       0.50

    **那種壞法沒有任何跡象**：總和仍然正確、每一筆都是漂亮的 0.5 倍數、
    每一筆也都 >= 0.5。壞掉的只有比例本身，而比例正是這個功能的全部意義。
    一天在十幾個聊天室各講兩句話是很普通的一天（`WORKLOG_MAX_SPACES` 是 20），
    所以這不是邊緣情況。

    四分之一這個數字的理由：它保證每筆至少留下應得的四分之三，而四分之三
    仍然看得出「這件事是那天的主要工作」。借不到的 orphan 會改走合併——
    它們的證據不會消失，只是不獨立成一筆。

    `max(1, …)` 是為了不改變少量 orphan 時的既有行為：floor 很小的筆
    原本就能被借走 1 個 unit，那沒有問題。
    """
    return max(1, floor_units // 4)


def _pick_lender(
    rows: Sequence[_Row],
    units: Sequence[int],
    floors: Sequence[int],
    borrowed: Sequence[int],
) -> Optional[int]:
    """挑一筆可以借出 1 unit 的。

    三個條件缺一不可：
      1. 借完不會歸零（`>= 2 units`）
      2. **已經被借走的量還沒到它的額度**（見 `_borrow_budget`）
      3. 在符合前兩條的裡面挑時數最多的

    一樣多的時候借**優先度最低**的那筆（priority 反過來取最後一個）：
    同樣是拿走 0.5 小時，從證據最少的那筆拿，對 Viewer 讀到的比例失真最小。
    """
    candidates = [
        i
        for i in range(len(rows))
        if units[i] >= 2 and borrowed[i] < _borrow_budget(floors[i])
    ]
    if not candidates:
        return None
    best = max(units[i] for i in candidates)
    tied = [i for i in candidates if units[i] == best]
    return max(tied, key=lambda i: rows[i].priority)


def _pick_merge_target(
    rows: Sequence[_Row], units: Sequence[int], orphan: int
) -> int:
    """被併掉的那筆要併進「時間最近的一筆」。

    只從還有時數的筆裡面挑（併進一筆同樣是 0 小時的等於沒併）。兩邊的時間
    有一邊解析不出來就沒有「最近」可言，退回「時數最多、再照 priority」——
    這種情況下任何答案都是任意的，重要的是它固定。
    """
    candidates = [i for i in range(len(rows)) if units[i] > 0 and i != orphan]
    # units_total >= 1，所以一定有人 > 0；這行只是讓退化情況不會爆掉。
    if not candidates:
        return orphan

    mine = rows[orphan].occurred

    def key(i: int) -> Tuple[Any, ...]:
        theirs = rows[i].occurred
        if mine[0] or theirs[0]:
            return (1, 0.0, -units[i]) + rows[i].priority
        return (0, abs(mine[1] - theirs[1]), -units[i]) + rows[i].priority

    return min(candidates, key=key)


def allocate(total_hours: Any, items: Sequence[Any]) -> AllocationResult:
    """把 `total_hours` 按證據權重分到 `items`，每筆湊成 0.5 小時的倍數。

    步驟（規劃 §6）：

      1. `units = round(total_hours / 0.5)`，之後全程整數
      2. `exact_i = units * w_i / Σw`，取 floor
      3. 餘下的 unit 依餘數由大到小各加 1，平手照三層 tie-break
      4. 每筆保底 0.5 小時：分不到的向時數最多的一筆借；借了會讓對方歸零
         就不借，改成併入時間最近的一筆並在 `adjustments` 記一筆 `merged`

    保證：`entries` 的時數相加恆等於 `result.total_hours`；每筆都是 0.5 的
    倍數；每筆不是 >= 0.5 就是有一筆對應的 `merged` 說明它為什麼是 0。

    ⚠️ **不保證**等於呼叫端傳進來的 `total_hours`：不是 0.5 倍數的輸入會被
    湊整（`adjustments` 會有 `quantized`）。前端的確認框要顯示的是這裡回傳的
    `total_hours`，不是 Viewer 打的那個數字。

    不合法的輸入一律拋 `WorklogAllocationError`，不回一份「看起來能用」的
    結果——見各個 `_coerce_items` / `_quantize_total` 的註解。
    """
    prepared = _coerce_items(items)
    units_total = _quantize_total(total_hours)

    adjustments: List[Dict[str, Any]] = []

    requested = float(total_hours)
    quantized_hours = format_hours(units_total)
    if f"{requested:.2f}" != quantized_hours:
        adjustments.append(
            {
                "kind": "quantized",
                "requested_hours": f"{requested:.2f}",
                "total_hours": quantized_hours,
                "reason": f"總時數不是 {QUANTUM_HOURS} 小時的倍數，已湊到最接近的刻度",
            }
        )

    rows = [
        _Row(
            index=i,
            item=item,
            weight=Fraction(item.weight),
            occurred=_occurred_key(item.occurred_at),
            issue_sort=_issue_key_sort(item.issue_key),
        )
        for i, item in enumerate(prepared)
    ]

    if all(r.weight == 0 for r in rows):
        # 全 0 不能當分母。改成平均分配而不是拋錯：權重是系統算出來的，
        # 「這天的證據一條都沒有權重」是上游的降級結果，不該讓整份草稿掛掉。
        # 但也不能安靜地平均——Viewer 要知道這份比例沒有依據。
        rows = [
            _Row(
                index=r.index,
                item=r.item,
                weight=Fraction(1),
                occurred=r.occurred,
                issue_sort=r.issue_sort,
            )
            for r in rows
        ]
        adjustments.append(
            {
                "kind": "uniform",
                "reason": "所有項目的證據權重都是 0，改為平均分配",
            }
        )

    units, floors = _largest_remainder(rows, units_total)

    # 保底 0.5 小時。處理順序照 priority：都分不到的時候，證據多的先有機會。
    merged_into: Dict[int, int] = {}
    orphans = sorted(
        (i for i in range(len(rows)) if units[i] == 0),
        key=lambda i: rows[i].priority,
    )
    borrowed = [0] * len(rows)
    for orphan in orphans:
        lender = _pick_lender(rows, units, floors, borrowed)
        if lender is not None:
            units[lender] -= 1
            borrowed[lender] += 1
            units[orphan] = 1
            adjustments.append(
                {
                    "kind": "borrowed",
                    "index": orphan,
                    "issue_key": rows[orphan].item.issue_key,
                    "from_index": lender,
                    "from_issue_key": rows[lender].item.issue_key,
                    "hours": format_hours(1),
                    "reason": f"這筆分不到 {MIN_ENTRY_HOURS} 小時，"
                    f"向時數最多的一筆借 {MIN_ENTRY_HOURS} 小時",
                }
            )
            continue

        target = _pick_merge_target(rows, units, orphan)
        merged_into[orphan] = target
        adjustments.append(
            {
                "kind": "merged",
                "index": orphan,
                "issue_key": rows[orphan].item.issue_key,
                "into_index": target,
                "into_issue_key": rows[target].item.issue_key,
                "reason": f"總時數不足以讓每筆至少 {MIN_ENTRY_HOURS} 小時",
            }
        )

    entries = tuple(
        AllocationEntry(
            index=row.index,
            issue_key=row.item.issue_key,
            occurred_at=row.item.occurred_at,
            weight=row.item.weight,
            units=units[i],
            allocated_hours=format_hours(units[i]),
            rounded_up=units[i] > floors[i],
            merged_into=merged_into.get(i),
        )
        for i, row in enumerate(rows)
    )

    # 這一行是最後一道防線，不是保險絲：湊不回去就是演算法壞了，
    # 而壞掉的分配送出去之後要走 correction-request 才改得回來。
    if sum(e.units for e in entries) != units_total:
        raise WorklogAllocationError(
            f"分配結果加總（{sum(e.units for e in entries)} units）"
            f"與總時數（{units_total} units）不符"
        )

    return AllocationResult(
        total_hours=quantized_hours,
        total_units=units_total,
        entries=entries,
        adjustments=tuple(adjustments),
    )
