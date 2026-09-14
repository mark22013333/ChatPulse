"""`core.worklog_allocation` 的分配演算法測試（Draft Worklog，ADR-0008）。

這套測試守的是三條不變量，而不是「某個輸入會得到某個好看的數字」：

  1. **加總恰等於量化後的總時數**。不是近似、不是誤差範圍內——每筆都是整數
     個 0.5 小時，所以「恰等於」是做得到的，做不到就是演算法壞了。
  2. **決定性**。同樣輸入跑 100 次要完全一樣。這些數字會進 ZPlanner，月結後
     要走 correction-request 才改得動；重產一次草稿就換一組數字的話，Viewer
     沒有辦法判斷哪一份才對。
  3. **沒有一筆會靜默消失**。每筆不是 >= 0.5 小時，就是有一筆 `merged`
     adjustment 說明它為什麼是 0。

⚠️ 這個模組是純函式：不碰 DB、不碰網路，所以這份測試也不需要暫存資料庫。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_allocation.py'
"""

import math
import os
import random
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import WorklogAllocationError  # noqa: E402
from core.worklog_allocation import (  # noqa: E402
    AllocationItem,
    allocate,
    format_hours,
)


def item(weight, occurred_at="", issue_key=None):
    return AllocationItem(weight=weight, occurred_at=occurred_at, issue_key=issue_key)


def hours_of(result):
    return [e.allocated_hours for e in result.entries]


def adjustments_of(result, kind):
    return [a for a in result.adjustments if a["kind"] == kind]


# --------------------------------------------------------------------------
# 規格裡已驗算的範例
# --------------------------------------------------------------------------


class SpecExampleTest(unittest.TestCase):
    """規劃 §6 的範例：7h、權重 0.5/0.3/0.2。

    ⚠️ **規劃文件把中間那筆寫成 2.50，那是筆誤。** 同一段自己列出的中間值
    （exact 7.0/4.2/2.8、floor 13、leftover 1、餘數給第三筆）推出來的是
    7/4/3 units ＝ 3.50／2.00／1.50；而文件寫的 3.50／2.50／1.50 相加是
    7.50，與它自己宣稱的「和恰為 7.00」矛盾。這裡照演算法與不變量走，
    並且把加總是不是 7.00 也一起驗——不變量才是這個模組的契約。
    """

    def setUp(self):
        self.items = [
            item(0.5, "2026-09-11T09:00:00+08:00", "PEI-133"),
            item(0.3, "2026-09-11T11:00:00+08:00", "PEI-077"),
            item(0.2, "2026-09-11T15:00:00+08:00", "PEI-999"),
        ]

    def test_七小時按證據比例分配(self):
        result = allocate(7.0, self.items)
        self.assertEqual(["3.50", "2.00", "1.50"], hours_of(result))
        self.assertEqual("7.00", result.total_hours)

    def test_加總恰等於總時數(self):
        result = allocate(7.0, self.items)
        total = sum(Decimal(e.allocated_hours) for e in result.entries)
        self.assertEqual(Decimal("7.00"), total)

    def test_沒有任何湊整以外的調整(self):
        result = allocate(7.0, self.items)
        self.assertEqual((), result.adjustments)

    def test_拿到餘數的那筆會被標記(self):
        # 第三筆的餘數 0.8 最大，它是唯一被 +0.5 的那筆（UI 的「≈ 湊整 +0.5」）
        result = allocate(7.0, self.items)
        self.assertEqual([False, False, True], [e.rounded_up for e in result.entries])


# --------------------------------------------------------------------------
# 邊界情況
# --------------------------------------------------------------------------


class EdgeCaseTest(unittest.TestCase):
    def test_單筆全拿(self):
        result = allocate(6.5, [item(0.7, "2026-09-11T09:00:00Z", "PEI-1")])
        self.assertEqual(["6.50"], hours_of(result))
        self.assertEqual("6.50", result.total_hours)

    def test_單筆的權重是零也全拿(self):
        # 只有一筆的時候「按比例」沒有意義，但總時數不能消失
        result = allocate(4.0, [item(0.0)])
        self.assertEqual(["4.00"], hours_of(result))

    def test_全等權三筆分七小時(self):
        # 14 units ÷ 3 分不盡：floor 4/4/4，餘 2 unit 照 tie-break 給前兩筆
        result = allocate(
            7.0,
            [
                item(1.0, "2026-09-11T09:00:00Z", "A"),
                item(1.0, "2026-09-11T10:00:00Z", "B"),
                item(1.0, "2026-09-11T11:00:00Z", "C"),
            ],
        )
        self.assertEqual(["2.50", "2.50", "2.00"], hours_of(result))
        self.assertEqual(
            Decimal("7.00"), sum(Decimal(h) for h in hours_of(result))
        )

    def test_權重全為零改成平均分配(self):
        result = allocate(
            2.0,
            [
                item(0.0, "2026-09-11T09:00:00Z", "A"),
                item(0.0, "2026-09-11T10:00:00Z", "B"),
                item(0.0, "2026-09-11T11:00:00Z", "C"),
            ],
        )
        self.assertEqual(["1.00", "0.50", "0.50"], hours_of(result))
        self.assertEqual(1, len(adjustments_of(result, "uniform")))

    def test_權重全為零不得除以零(self):
        # 這條單獨留著：分母是 0 的話上面那個測試會變成 ZeroDivisionError，
        # 而不是一份標了 uniform 的正常結果
        try:
            allocate(3.0, [item(0.0), item(0.0)])
        except ZeroDivisionError:  # pragma: no cover - 失敗時才會走到
            self.fail("權重全為零時除到了零")

    def test_總時數不足以讓每筆保底時併掉最近的(self):
        # 1.0h ＝ 2 units、3 筆：第三筆分不到，也借不到（借了會讓對方歸零）
        result = allocate(
            1.0,
            [
                item(0.5, "2026-09-11T09:00:00+08:00", "PEI-133"),
                item(0.3, "2026-09-11T11:00:00+08:00", "PEI-077"),
                item(0.2, "2026-09-11T15:00:00+08:00", "PEI-999"),
            ],
        )
        self.assertEqual(["0.50", "0.50", "0.00"], hours_of(result))
        merged = adjustments_of(result, "merged")
        self.assertEqual(1, len(merged))
        self.assertEqual(2, merged[0]["index"])
        # 併入「時間最近的一筆」：15:00 距 11:00 比距 09:00 近
        self.assertEqual(1, merged[0]["into_index"])
        self.assertEqual("總時數不足以讓每筆至少 0.5 小時", merged[0]["reason"])
        self.assertEqual(1, result.entries[2].merged_into)
        self.assertFalse(result.entries[2].is_submittable)
        self.assertEqual(
            Decimal("1.00"), sum(Decimal(h) for h in hours_of(result))
        )

    def test_分不到的那筆借得到就不併(self):
        # 3.0h ＝ 6 units：第一筆拿 6、第二筆 0，但第一筆借出 1 之後還有 5
        result = allocate(
            3.0,
            [
                item(10.0, "2026-09-11T09:00:00Z", "A"),
                item(0.001, "2026-09-11T10:00:00Z", "B"),
            ],
        )
        self.assertEqual(["2.50", "0.50"], hours_of(result))
        borrowed = adjustments_of(result, "borrowed")
        self.assertEqual(1, len(borrowed))
        self.assertEqual(1, borrowed[0]["index"])
        self.assertEqual(0, borrowed[0]["from_index"])
        self.assertEqual([], adjustments_of(result, "merged"))

    def test_借出不會讓借出方歸零(self):
        """借出方保得住，借不到的那筆改走併入。

        ### 這條原本寫錯了

        它原本斷言「**所有人** units >= 1」，名字卻是「借出之後不會有人歸零」
        ——兩者不是同一件事，而那個較寬的斷言正好放行了 2026-09-13 審查抓到的
        缺陷：主群被連續借到只剩 0.5 小時，但因為每個人都 >= 1，它照樣是綠的。

        加上借出額度（`_borrow_budget`）之後，借不到的 orphan 改走併入：
        `units` 是 0，但帶著 `merged_into`，證據併進另一筆、不會消失。
        這裡權重 0.01 的那筆佔全部的萬分之一，併掉比硬借走主群的 0.5 小時
        更貼近事實。
        """
        result = allocate(
            2.5,
            [
                item(100.0, "2026-09-11T09:00:00Z", "A"),
                item(0.01, "2026-09-11T10:00:00Z", "B"),
                item(0.01, "2026-09-11T11:00:00Z", "C"),
            ],
        )
        # 借出方（佔 99.98% 權重的那筆）不可以被借到歸零
        self.assertGreaterEqual(result.entries[0].units, 1)
        # units 為 0 的一定要有併入紀錄，不可以就這樣消失
        for entry in result.entries:
            if entry.units == 0:
                self.assertIsNotNone(entry.merged_into)
                self.assertGreater(result.entries[entry.merged_into].units, 0)
        self.assertEqual(
            Decimal("2.50"), sum(Decimal(h) for h in hours_of(result))
        )

    def test_湊整後的加總是借出方的淨值而不是謊話(self):
        # 借出 1 unit 的那筆若先前拿過餘數，淨值等於 floor，不該掛「湊整 +0.5」
        result = allocate(
            3.0,
            [
                item(10.0, "2026-09-11T09:00:00Z", "A"),
                item(0.001, "2026-09-11T10:00:00Z", "B"),
            ],
        )
        self.assertFalse(result.entries[0].rounded_up)

    def test_總時數不是零點五的倍數會被湊整(self):
        result = allocate(
            7.3,
            [
                item(0.5, "2026-09-11T09:00:00+08:00", "PEI-133"),
                item(0.3, "2026-09-11T11:00:00+08:00", "PEI-077"),
                item(0.2, "2026-09-11T15:00:00+08:00", "PEI-999"),
            ],
        )
        self.assertEqual("7.50", result.total_hours)
        self.assertEqual(
            Decimal("7.50"), sum(Decimal(h) for h in hours_of(result))
        )
        quantized = adjustments_of(result, "quantized")
        self.assertEqual(1, len(quantized))
        self.assertEqual("7.30", quantized[0]["requested_hours"])
        self.assertEqual("7.50", quantized[0]["total_hours"])

    def test_剛好是零點五倍數時不留湊整紀錄(self):
        result = allocate(7.5, [item(1.0), item(1.0)])
        self.assertEqual([], adjustments_of(result, "quantized"))

    def test_空的項目清單直接擋下(self):
        # 不回一份空結果：那會讓 Viewer 輸入的時數安靜消失
        with self.assertRaises(WorklogAllocationError) as ctx:
            allocate(8.0, [])
        self.assertEqual("WORKLOG_ALLOCATION_ERROR", ctx.exception.code)
        self.assertEqual(400, ctx.exception.http_status)

    def test_總時數為零擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(0, [item(1.0)])

    def test_總時數為負擋下(self):
        with self.assertRaises(WorklogAllocationError) as ctx:
            allocate(-3.0, [item(1.0)])
        self.assertIn("負", ctx.exception.message)

    def test_總時數湊不到半小時擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(0.2, [item(1.0)])

    def test_權重為負擋下(self):
        with self.assertRaises(WorklogAllocationError) as ctx:
            allocate(8.0, [item(1.0), item(-0.5)])
        self.assertIn("第 2 筆", ctx.exception.message)

    def test_權重為NaN擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(8.0, [item(float("nan"))])

    def test_權重為無限大擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(8.0, [item(float("inf"))])

    def test_總時數為NaN擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(float("nan"), [item(1.0)])

    def test_布林值不算數字(self):
        # True 是 int 的子類別：不擋的話「總時數 True」會安靜變成 1 小時
        with self.assertRaises(WorklogAllocationError):
            allocate(True, [item(1.0)])
        with self.assertRaises(WorklogAllocationError):
            allocate(8.0, [item(True)])

    def test_總時數是字串擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate("8.0", [item(1.0)])

    def test_項目是dict也吃(self):
        result = allocate(
            8.0,
            [
                {"weight": 3, "occurred_at": "2026-09-11T09:00:00Z", "issue_key": "A"},
                {"weight": 1, "occurred_at": "2026-09-11T10:00:00Z", "issue_key": "B"},
            ],
        )
        self.assertEqual(["6.00", "2.00"], hours_of(result))

    def test_dict缺少權重擋下(self):
        with self.assertRaises(WorklogAllocationError) as ctx:
            allocate(8.0, [{"issue_key": "A"}])
        self.assertIn("weight", ctx.exception.message)

    def test_不認得的項目型別擋下(self):
        with self.assertRaises(WorklogAllocationError):
            allocate(8.0, ["PEI-133"])


# --------------------------------------------------------------------------
# 平手時的三層 tie-break
# --------------------------------------------------------------------------


class TieBreakTest(unittest.TestCase):
    """餘數完全相同時，誰拿到那 0.5 小時必須是可預測的。

    每個測試只讓一層有差別，前面幾層一律做成完全相同，這樣哪一層壞掉都會
    有測試變紅（三層寫在同一個測試裡的話，第一層就足以讓它通過）。
    """

    def test_第一層_權重大者優先(self):
        # 1.5h ＝ 3 units、2 筆：floor 1/1，餘數相同 → 比權重
        result = allocate(
            1.5,
            [
                item(1.0, "2026-09-11T09:00:00Z", "A"),
                item(3.0, "2026-09-11T09:00:00Z", "A"),
            ],
        )
        self.assertEqual(["0.50", "1.00"], hours_of(result))

    def test_第二層_權重相同時間早者優先(self):
        result = allocate(
            1.5,
            [
                item(2.0, "2026-09-11T14:00:00Z", "A"),
                item(2.0, "2026-09-11T08:00:00Z", "A"),
            ],
        )
        self.assertEqual(["0.50", "1.00"], hours_of(result))

    def test_第三層_權重與時間都相同時看issue_key字典序(self):
        result = allocate(
            1.5,
            [
                item(2.0, "2026-09-11T09:00:00Z", "PEI-200"),
                item(2.0, "2026-09-11T09:00:00Z", "PEI-100"),
            ],
        )
        self.assertEqual(["0.50", "1.00"], hours_of(result))

    def test_沒有issue_key的排在有的後面(self):
        result = allocate(
            1.5,
            [
                item(2.0, "2026-09-11T09:00:00Z", None),
                item(2.0, "2026-09-11T09:00:00Z", "PEI-999"),
            ],
        )
        self.assertEqual(["0.50", "1.00"], hours_of(result))

    def test_時間解析不出來的排在最後(self):
        # 壞掉的時間戳不該因為「當成 1970 年」而搶到優先位置
        result = allocate(
            1.5,
            [
                item(2.0, "上週三", "A"),
                item(2.0, "2026-09-11T09:00:00Z", "A"),
            ],
        )
        self.assertEqual(["0.50", "1.00"], hours_of(result))

    def test_帶Z與帶時區的時間比得出先後(self):
        result = allocate(
            1.5,
            [
                item(2.0, "2026-09-11T09:00:00+08:00", "A"),  # ＝ 01:00Z
                item(2.0, "2026-09-11T05:00:00Z", "A"),
            ],
        )
        self.assertEqual(["1.00", "0.50"], hours_of(result))


# --------------------------------------------------------------------------
# 決定性
# --------------------------------------------------------------------------


class DeterminismTest(unittest.TestCase):
    def test_同一輸入跑一百次結果完全相同(self):
        items = [
            item(2.0, "2026-09-11T09:00:00Z", "PEI-100"),
            item(2.0, "2026-09-11T09:00:00Z", "PEI-200"),
            item(2.0, "2026-09-11T09:00:00Z", None),
            item(0.0, "", None),
            item(1.5, "2026-09-11T13:30:00+08:00", "PEI-077"),
        ]
        first = allocate(7.5, items).to_dict()
        for _ in range(99):
            self.assertEqual(first, allocate(7.5, items).to_dict())

    def test_每次都建新的輸入物件也相同(self):
        # 上一個測試共用同一組物件，理論上可能被「記憶體位址」之類的東西
        # 決定順序；這裡每一輪重建，排除掉那種假的決定性
        def build():
            return [
                {"weight": 1.0, "occurred_at": "2026-09-11T09:00:00Z", "issue_key": "A"},
                {"weight": 1.0, "occurred_at": "2026-09-11T09:00:00Z", "issue_key": "B"},
                {"weight": 1.0, "occurred_at": "2026-09-11T09:00:00Z", "issue_key": "C"},
            ]

        first = allocate(4.0, build()).to_dict()
        for _ in range(99):
            self.assertEqual(first, allocate(4.0, build()).to_dict())


# --------------------------------------------------------------------------
# 不變量（固定 seed 的隨機輸入）
# --------------------------------------------------------------------------


class InvariantTest(unittest.TestCase):
    """200 組隨機輸入，逐條驗三個不變量。

    seed 固定：失敗時重跑會得到同一組輸入，才查得下去。
    """

    SEED = 20260913
    ROUNDS = 200

    def _cases(self):
        rng = random.Random(self.SEED)
        for _ in range(self.ROUNDS):
            # 上限刻意拉到 20（原本是 8）。保底借用的「抽乾」要十幾筆才顯著，
            # 而 8 筆的上限讓這一整組隨機測試系統性地看不到它——2026-09-13
            # 的審查就是這樣漏掉的。20 對得上 WORKLOG_MAX_SPACES。
            count = rng.randint(1, 20)
            style = rng.random()
            items = []
            for i in range(count):
                if style < 0.15:
                    weight = 0.0  # 整批零權重
                elif style < 0.35:
                    weight = float(rng.randint(0, 40))  # 整數權重，容易出現平手
                else:
                    weight = rng.random() * 10
                occurred_at = (
                    ""
                    if rng.random() < 0.1
                    else f"2026-09-11T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00+08:00"
                )
                issue_key = None if rng.random() < 0.1 else f"PEI-{rng.randint(1, 30)}"
                items.append(item(weight, occurred_at, issue_key))
            # 0.25 的刻度：一半以上會是 0.5 的倍數，其餘要走湊整
            total = rng.randint(2, 64) * 0.25
            yield total, items

    def test_加總恰等於量化後的總時數(self):
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                result = allocate(total, items)
                self.assertEqual(
                    Decimal(result.total_hours),
                    sum(Decimal(e.allocated_hours) for e in result.entries),
                )

    def test_每筆都是零點五的倍數(self):
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                result = allocate(total, items)
                for entry in result.entries:
                    remainder = Decimal(entry.allocated_hours) % Decimal("0.5")
                    self.assertEqual(Decimal("0"), remainder, entry.allocated_hours)
                    self.assertEqual(format_hours(entry.units), entry.allocated_hours)

    def test_每筆不是至少半小時就有併入紀錄(self):
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                result = allocate(total, items)
                merged = {a["index"]: a for a in adjustments_of(result, "merged")}
                for entry in result.entries:
                    if entry.units == 0:
                        self.assertIn(entry.index, merged)
                        self.assertEqual(
                            merged[entry.index]["into_index"], entry.merged_into
                        )
                        # 併入的對象自己一定要有時數，否則等於併進黑洞
                        target = result.entries[entry.merged_into]
                        self.assertGreater(target.units, 0)
                    else:
                        self.assertGreaterEqual(Decimal(entry.allocated_hours), Decimal("0.5"))
                        self.assertIsNone(entry.merged_into)

    def test_輸出的筆數與順序對得回輸入(self):
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                result = allocate(total, items)
                self.assertEqual(len(items), len(result.entries))
                for i, entry in enumerate(result.entries):
                    self.assertEqual(i, entry.index)
                    self.assertEqual(items[i].issue_key, entry.issue_key)

    def test_隨機輸入也是決定性的(self):
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                first = allocate(total, items).to_dict()
                self.assertEqual(first, allocate(total, items).to_dict())

    def test_沒被併掉的筆不會被借到低於應得的一半(self):
        """保底借用是為了讓小筆不消失，**不是把大筆抽乾**。

        這條是 2026-09-13 審查發現「借用會把權重最大的那筆抽到只剩 0.5 小時」
        之後補的。前面那幾條不變量都看不到它——總和仍然正確、每筆都是漂亮的
        0.5 倍數、每筆也都 >= 0.5，只有「誰該拿多少」整個顛倒了。
        """
        for total, items in self._cases():
            with self.subTest(total=total, count=len(items)):
                result = allocate(total, items)
                weight_sum = sum(e.weight for e in result.entries)
                if weight_sum <= 0:
                    continue  # 零權重走平均分配，比例無從談起
                for entry in result.entries:
                    if entry.merged_into is not None:
                        continue
                    # 用 floor 而不是精確的比例值當基準——借出額度
                    # （`_borrow_budget`）就是以 floor 算的，兩邊要用同一把尺。
                    # 拿精確值比會在「floor 4／應得 4.3」這種邊界上誤報。
                    floor = int(result.total_units * entry.weight / weight_sum)
                    # 應得不到 2 小時的跳過：保底規則本來就可能借走它的 0.5，
                    # 那是這條規則存在的理由，不是缺陷。要抓的是「同一筆被
                    # 連續借十幾次」那種。
                    if floor < 4:
                        continue
                    self.assertGreaterEqual(
                        entry.units * 4,
                        floor * 3,
                        f"index={entry.index} 按比例應得 {floor} units，"
                        f"實得 {entry.units}——被借走超過四分之一",
                    )


class BorrowDrainTest(unittest.TestCase):
    """保底 0.5 的借用不可以把權重最大的那筆抽乾。

    ### 這個 bug 長什麼樣

    `_pick_lender` 每一輪都挑「當下 units 最多的那筆」，而那**永遠是同一筆**；
    唯一的護欄是「借完不會歸零」，所以它可以被連續借到只剩 0.5 小時。

    2026-09-13 審查實測（8 小時、一個佔 82.8% 權重的主群 ＋ N 個三言兩語的
    小群）：

        小群數   主群應得   主群實得
           2       7.73      7.00
           8       7.02      4.00
          12       6.62      2.00
          16       6.26      0.50

    **沒有任何一個數字看起來不對**：總和正確、每筆都是 0.5 的倍數、每筆都
    >= 0.5。壞掉的只有比例本身。而 `WORKLOG_MAX_SPACES = 20` 讓 16 個小群
    完全寫實——一天在十幾個聊天室各講兩句話是很普通的一天。
    """

    def _run(self, small_count: int):
        # 主群：十個大 commit。小群：各一則三言兩語的發言
        items = [item(80.1, "2026-09-11T09:00:00+08:00", "MAIN-1")]
        items += [
            item(1.39, f"2026-09-11T{10 + (i % 10):02d}:00:00+08:00", f"SMALL-{i}")
            for i in range(small_count)
        ]
        return allocate(8.0, items)

    def test_主群不會被借到只剩零點五小時(self):
        for small_count in (2, 8, 12, 16):
            with self.subTest(小群數=small_count):
                result = self._run(small_count)
                main = result.entries[0]
                self.assertGreaterEqual(
                    Decimal(main.allocated_hours),
                    Decimal("4.0"),
                    f"佔 82.8% 權重的主群只拿到 {main.allocated_hours} 小時"
                    f"（小群 {small_count} 個）",
                )

    def test_借不動的時候要改成併入而不是硬借(self):
        # 借用有上限，超過之後剩下的小群應該走 merge——它們的證據不會消失，
        # 只是不獨立成一筆
        result = self._run(16)
        merged = adjustments_of(result, "merged")
        borrowed = adjustments_of(result, "borrowed")
        self.assertTrue(merged, "小群這麼多卻一筆都沒有併入，代表全部硬借了")
        # 每一筆併入都要說得出併去哪裡
        for adj in merged:
            self.assertIsNotNone(result.entries[adj["index"]].merged_into)
        # 借用仍然存在（少量 orphan 時借比併好）
        self.assertTrue(borrowed or merged)

    def test_主群仍然是拿最多的那一筆(self):
        # 單調性：權重最大的不該拿得比別人少
        result = self._run(12)
        main = result.entries[0]
        for entry in result.entries[1:]:
            self.assertGreaterEqual(main.units, entry.units)


# --------------------------------------------------------------------------
# 格式化
# --------------------------------------------------------------------------


class FormatHoursTest(unittest.TestCase):
    def test_一律兩位小數的字串(self):
        self.assertEqual("0.00", format_hours(0))
        self.assertEqual("0.50", format_hours(1))
        self.assertEqual("4.00", format_hours(8))
        self.assertEqual("7.50", format_hours(15))

    def test_大數字也不會跑出浮點誤差(self):
        # 0.5 是 2 的冪，乘回去是精確的；這裡順手釘住這個前提
        for units in range(0, 200):
            text = format_hours(units)
            self.assertEqual(Decimal(text), Decimal(units) / 2)
            self.assertFalse(math.isnan(float(text)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
