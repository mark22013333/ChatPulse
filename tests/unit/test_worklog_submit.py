"""`core.worklog_submit` 的純判斷層測試（工時草稿送出，ADR-0008 ＋ plan.md D-5）。

這個模組裡的判斷有一個共通點：**算錯不會有人發現**。送出流程的其他部分（打
API、寫 DB、回應形狀）錯了會當場爆，而這裡錯了只會多出或少掉一筆工時——而工時
進了月結就刪不掉，只能走 correction-request。所以這份測試守的是**代價不可逆的
那幾條**，不是「函式有沒有回東西」：

  1. **未指派閘**（AC-1）。有任何一筆沒選 issue 就擋下整批，不是跳過那筆照送
     其餘。部分送出會讓那天的工時停在半送出狀態，而使用者看不出哪幾筆送了。
  2. **去重比對**（AC-3／D-2）。比 issue ＋ hours，**不比 note**（note 會被人
     編輯過，比對它會漏判而造出重複工時）。時數兩側都要正規化到同一刻度——
     `"2"` 與 `"2.00"` 是同一個數，這是規劃的風險段唯一點名的漏判來源。
  3. **例外 → state 分類**（AC-5／AC-6）。分界線只有一條：**只有拿到 code != 200
     的回應信封才證明沒有寫進去**。把 unknown 誤判成 failed 會讓它被重送，在
     ZPlanner 那邊變成兩筆。
  4. **草稿 status 推導**（D-9(c)）。沒有 partial 的話，混合狀態的那天看起來像
     「完全還沒送」，人再按一次就重複了。

⚠️ 這個模組是純函式：不碰 DB、不碰網路（比照 allocation／clustering／evidence
三支），所以這份測試也不需要暫存資料庫、不需要假 client。

⚠️ 每個測試類別都刻意帶一條**正對照**（「不該跳過的不准跳過」「閘不是恆真」）。
沒有它的話，「一律回空」與「一律判重複」這兩種實作會讓整份測試全綠，而那正好是
最貴的兩種壞法。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_submit.py'
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import (  # noqa: E402
    InvalidParameter,
    ZPlannerApiError,
    ZPlannerAuthError,
    ZPlannerForbidden,
    ZPlannerInvalidParameter,
    ZPlannerNotFound,
    ZPlannerUnavailable,
    ZPlannerWriteIndeterminate,
)
from core.worklog_submit import (  # noqa: E402
    classify_write_error,
    derive_draft_status,
    find_duplicates,
    find_missing_hours,
    find_unassigned,
    select_entries,
)

_SEQ = [0]


def entry(**extra):
    """一筆已經選好 issue、還沒送出的草稿 entry。

    `id` 與 `seq` 自動遞增——回報要指得回「畫面上的第幾張卡」，所以這兩個欄位
    不能在 fixture 裡全部長一樣，否則「回錯筆」這種錯就驗不出來。
    """
    _SEQ[0] += 1
    row = {
        "id": 1000 + _SEQ[0],
        "seq": _SEQ[0],
        "issue_id": 501,
        "issue_key": "PEI-133",
        "hours": "2.00",
        "note": "做了匯出效能",
        "state": "pending",
        "zplanner_worklog_id": None,
    }
    row.update(extra)
    return row


def exported(issue_key, hours, *, work_date="2026-09-13", note=""):
    """一列 `export_worklogs` 的回傳。

    🔴 **每個值都是字串**——client 刻意不轉型（見 `export_worklogs` 的
    docstring：轉 float 會讓 `2` 與 `2.0` 在比對時錯開）。fixture 也不准偷偷用
    數字，不然就驗不到真正會發生的那種輸入。
    """
    return {
        "date": work_date,
        "user_name": "我",
        "project_code": "PEI",
        "issue_key": issue_key,
        "issue_title": "匯出效能",
        "hours": hours,
        "note": note,
    }


# --------------------------------------------------------------------------
# AC-7：選取要處理的筆
# --------------------------------------------------------------------------


class SelectEntriesTest(unittest.TestCase):
    """`entry_ids` 是 AC-7 單筆重打的入口（D-8：走同一支端點、用 body 欄位）。"""

    def test_沒給就是整批(self):
        rows = [entry(), entry()]
        self.assertEqual(tuple(rows), select_entries(rows).selected)

    def test_給了就只處理指名的那幾筆(self):
        a, b, c = entry(), entry(), entry()
        picked = select_entries([a, b, c], [b["id"]]).selected
        self.assertEqual([b["id"]], [row["id"] for row in picked])

    def test_順序照草稿而不照_entry_ids(self):
        """送出順序要和畫面上看到的順序一致。

        否則「送到第 3 筆失敗」這句話對不上任何一張卡——而這正是使用者在混合
        狀態下唯一能拿來定位問題的線索。
        """
        a, b, c = entry(), entry(), entry()
        picked = select_entries([a, b, c], [c["id"], a["id"]]).selected
        self.assertEqual([a["id"], c["id"]], [row["id"] for row in picked])

    def test_指到不存在的_id_要回報而不是靜默忽略(self):
        """靜默忽略會讓使用者以為重打過了，而實際上一筆都沒動。"""
        a = entry()
        result = select_entries([a], [a["id"], 99999])
        self.assertEqual([a["id"]], [row["id"] for row in result.selected])
        self.assertEqual((99999,), result.unknown_ids)

    def test_重複指名同一個_id_只算一次(self):
        a = entry()
        result = select_entries([a], [a["id"], a["id"]])
        self.assertEqual(1, len(result.selected))

    def test_空的_entry_ids_不等於整批(self):
        """`[]` 與 `None` 是兩件事：前者是「一筆都沒選」，後者是「整批」。

        混為一談的話，前端送了一個空陣列就會意外把整天都送出去。
        """
        rows = [entry(), entry()]
        self.assertEqual((), select_entries(rows, []).selected)

    def test_不是整數的_id_直接擋下(self):
        # 這是呼叫端傳錯型別（契約寫的是整數陣列），與「這份草稿沒有這一筆」
        # 是兩件不同的事。混在一起會讓前端的 bug 看起來像資料過期
        with self.assertRaises(InvalidParameter):
            select_entries([entry()], ["abc"])


# --------------------------------------------------------------------------
# AC-1：未指派閘
# --------------------------------------------------------------------------


class UnassignedGateTest(unittest.TestCase):
    """ADR-0008 規定歸屬由人選，所以「還沒選」是預期中的狀態，不是例外。

    閘要回的是**哪幾筆**，不是一個布林——呼叫端要把筆數與位置放進
    `error.detail`，前端才不必從中文句子裡用正則挖數字。
    """

    def test_沒選_issue_的筆會被挑出來(self):
        rows = [entry(), entry(issue_id=None, issue_key=None)]
        self.assertEqual([rows[1]["id"]], [u.entry_id for u in find_unassigned(rows)])

    def test_全部選好時回空(self):
        """正對照：閘不是恆真。

        少了這條，「永遠擋下」也會讓上一個測試變綠——而那個實作會讓這個功能
        一筆工時都送不出去。
        """
        self.assertEqual((), find_unassigned([entry(), entry(issue_id=502)]))

    def test_已經是_skipped_的筆不算未指派(self):
        """AC-1 的原文是「未指派 issue **且非 skipped**」。

        被併成 0 小時、或上一輪去重跳過的筆本來就不會送出去。把它們算進閘裡
        會讓整天永遠送不出去，而使用者看到的訊息是「請先選專案」，指著一筆
        根本不需要選的空筆。
        """
        rows = [entry(), entry(issue_id=None, issue_key=None, state="skipped")]
        self.assertEqual((), find_unassigned(rows))

    def test_判準是_issue_id_不是_issue_key(self):
        """🔴 三種寫法只有「只看 `issue_id`」是對的，另外兩種各開一個洞。

        | 判準 | id 有、key 無 | key 有、id 無 |
        |------|--------------|--------------|
        | 兩個都要 | ❌ 誤判成沒選，擋住整天 | 擋下（對） |
        | id **或** key | 放行（對） | ❌ 放行 → 部分送出 |
        | **只看 id** | 放行（對） | 擋下（對） |

        右下角那一格才是關鍵：只有 key 沒有 id 的筆**送不出去**——
        `create_worklog(issue_id, …)` 的 `_require_int(None)` 會拋。閘放它過去，
        結果是那筆變 `failed`、其餘照送 ＝ **部分送出**，正是 D-3 明文否決、也
        正是這道閘存在的理由。而使用者會看到「送出成功」卻少一筆，沒有任何訊號。

        api-contract 把兩欄當一組講，講的是「產生時一律留空、有值代表人選過了」
        （重產守門用），不是「送得出去」的條件——後者由 `create_worklog` 的簽章
        決定。
        """
        self.assertEqual((), find_unassigned([entry(issue_key=None)]), "有 id 就送得出去")
        only_key = entry(issue_id=None)
        self.assertEqual(
            [only_key["id"]],
            [u.entry_id for u in find_unassigned([only_key])],
            "只有 key 沒有 id 送不出去，放行等於部分送出",
        )

    def test_零不算有選過(self):
        # `0` 是 falsy 但也是一個合法的整數字面。用「是不是 None」判斷會讓
        # issue_id=0 矇混過關，然後在 ZPlanner 那邊變成一個不存在的 issue
        row = entry(issue_id=0, issue_key=None)
        self.assertEqual([row["id"]], [u.entry_id for u in find_unassigned([row])])

    def test_回報帶得出畫面上的第幾張卡(self):
        """只回 entry_id 的話，前端要自己去草稿裡反查才畫得出「這張卡要補選」。

        而 `to_dict()` 刻意只留這兩個欄位：整筆 entry 帶著證據 ref 與候選清單，
        塞進 `error.detail` 只會把真正要看的東西淹掉。
        """
        row = entry(issue_id=None, issue_key=None, seq=7)
        found = find_unassigned([row])[0]
        self.assertEqual(7, found.seq)
        self.assertEqual({"entry_id": row["id"], "seq": 7}, found.to_dict())

    def test_帶_entry_ids_時只檢查選中的那幾筆(self):
        """🔴 D-8 的連帶條款。

        否則當天別處有一筆沒選專案，`failed` 的那筆就**永遠重打不了**——閘會
        每次都擋在它前面，而人根本沒打算送那一筆。
        """
        target = entry(state="failed")
        rows = [target, entry(issue_id=None, issue_key=None)]
        self.assertEqual((), find_unassigned(rows, entry_ids=[target["id"]]))
        # 正對照：不帶 entry_ids 時那一筆照樣擋得下來
        self.assertEqual(1, len(find_unassigned(rows)))

    def test_空的草稿沒有未指派的筆(self):
        self.assertEqual((), find_unassigned([]))


# --------------------------------------------------------------------------
# AC-1 的另一半：沒有時數的閘
# --------------------------------------------------------------------------


class MissingHoursGateTest(unittest.TestCase):
    """⚠️ **這不是罕見的邊界情況。**

    產草稿時省略 `total_hours` 就不做分配，那時候整份草稿的 `hours` 一律是
    null——那是正常流程的中間狀態，不是壞掉的資料。

    為什麼擋在前面而不是讓它們各自變成 `failed`：這種筆在 `create_worklog` 會
    於**發出 HTTP 之前**就被 `_require_hours` 擋下，所以確實不會造成重複工時。
    但那樣等於部分送出——D-3 否決過：那天的工時會變成不完整，而畫面上沒有任何
    訊號說明少了什麼。
    """

    def test_沒填時數的筆會被挑出來(self):
        for hours in (None, "", "   ", "兩小時"):
            with self.subTest(hours=repr(hours)):
                row = entry(hours=hours)
                self.assertEqual(
                    [row["id"]], [e.entry_id for e in find_missing_hours([row])]
                )

    def test_填了時數就放行(self):
        """正對照：閘不是恆真。"""
        self.assertEqual((), find_missing_hours([entry(hours="2.00"), entry(hours="0.5")]))

    def test_零小時不算沒填(self):
        """🔴 `"0.00"` 是**一個有意義的值**——被併掉的那一筆（allocation 的
        `merged`）就是 0。

        判準是「認不認得這個數」，不是「等不等於 0」。把 0 當成沒填會讓任何
        含併入筆的草稿整天送不出去，而那是分配演算法的正常輸出。
        """
        self.assertEqual((), find_missing_hours([entry(hours="0.00")]))

    def test_已經結束的筆不算(self):
        # submitted／skipped 的筆不會再送出去，沒有時數也擋不住任何人
        for state in ("submitted", "skipped"):
            with self.subTest(state=state):
                self.assertEqual((), find_missing_hours([entry(hours=None, state=state)]))

    def test_帶_entry_ids_時只檢查選中的那幾筆(self):
        target = entry(hours="2.00", state="failed")
        rows = [target, entry(hours=None)]
        self.assertEqual((), find_missing_hours(rows, entry_ids=[target["id"]]))
        self.assertEqual(1, len(find_missing_hours(rows)), "不帶時那筆照樣擋得下來")

    def test_回報帶得出畫面上的第幾張卡(self):
        row = entry(hours=None, seq=4)
        self.assertEqual({"entry_id": row["id"], "seq": 4}, find_missing_hours([row])[0].to_dict())

    def test_空的草稿沒有缺時數的筆(self):
        self.assertEqual((), find_missing_hours([]))


# --------------------------------------------------------------------------
# AC-3：時數的刻度（D-2 點名的漏判來源）
# --------------------------------------------------------------------------


class HoursScaleTest(unittest.TestCase):
    """🔴 `"2"` 與 `"2.00"` 必須是同一個數。

    這是整個去重裡唯一一個「看起來不可能錯」的地方，也是規劃的風險段唯一點名
    的：export 回的 `hours` 是 CSV **字串**且 client 刻意不轉型，而本地存的是
    `format_hours()` 產出的兩位小數字串。兩邊直接用 `==` 比，遠端回 `"2"`、本地
    是 `"2.00"` 就漏判 → 那天多出一筆 2 小時的工時 → **月結後刪不掉**。

    這裡刻意透過 `find_duplicates` 驗而不是直接戳正規化函式：正規化是私有實作，
    真正的契約是「這兩筆會不會被判為重複」。
    """

    def _is_duplicate(self, local_hours, remote_hours):
        rows = [entry(hours=local_hours)]
        return bool(find_duplicates(rows, [exported("PEI-133", remote_hours)]))

    def test_同一個數的各種寫法都判得出重複(self):
        for local, remote in (
            ("2.00", "2"),
            ("2.00", "2.0"),
            ("2.00", "2.000"),
            ("2", "2.00"),
            ("2.0", "2.00"),
            ("0.50", ".5"),
            ("0.50", "0.5"),
            ("0.50", "0.500"),
            ("2.00", " 2.00 "),
        ):
            with self.subTest(本地=local, 遠端=remote):
                self.assertTrue(
                    self._is_duplicate(local, remote),
                    f"本地 {local!r} 與遠端 {remote!r} 是同一個數，漏判會造出重複工時",
                )

    def test_不同的數不可以被判成重複(self):
        """反向的正對照：正規化不是「把所有東西都變成同一個數」。

        少了這條，一個「一律判重複」的實作會讓上面整組測試全綠——而它會把一
        整天的工時全部跳過，一筆都不送。
        """
        for local, remote in (
            ("2.00", "2.50"),
            ("2.00", "20.00"),
            ("0.50", "0.05"),
            ("1.00", "1.50"),
            ("2.00", "-2.00"),
        ):
            with self.subTest(本地=local, 遠端=remote):
                self.assertFalse(self._is_duplicate(local, remote))

    def test_大小寫不同的_issue_key_仍然對得上(self):
        """往「多跳過」的方向倒：判成重複只是漏送一筆（人看得見、補得回），
        判成不重複則是多一筆刪不掉的工時。代價不對稱時要往可回復的那邊倒。"""
        rows = [entry(issue_key="pei-133")]
        self.assertTrue(find_duplicates(rows, [exported("PEI-133", "2.00")]))


# --------------------------------------------------------------------------
# AC-3：去重比對
# --------------------------------------------------------------------------


class DuplicateMatchTest(unittest.TestCase):
    """比對 issue ＋ hours，**不比 note**（D-2）。"""

    def test_同_issue_同時數判為重複(self):
        rows = [entry()]
        found = find_duplicates(rows, [exported("PEI-133", "2.00")])
        self.assertEqual([rows[0]["id"]], [d.entry_id for d in found])

    def test_時數不同就不是重複(self):
        """正對照：去重不是「同一個 issue 就不送」。

        一天在同一個 issue 上填兩筆不同時數是完全正常的事（早上兩小時、下午
        一小時），判成重複會安靜吃掉下午那筆。
        """
        self.assertEqual((), find_duplicates([entry(hours="1.50")], [exported("PEI-133", "2.00")]))

    def test_issue_不同就不是重複(self):
        self.assertEqual((), find_duplicates([entry()], [exported("PEI-140", "2.00")]))

    def test_note_不一樣照樣判為重複(self):
        """🔴 D-2 的核心：**不比 note**。

        note 會被使用者編輯過——系統產的是「fix: PEI-133 匯出逾時」，人送出前
        改成「處理匯出逾時」。把 note 納入比對，這筆就會被判成新的一筆而重複
        送出，而那正是 D-2 明文否決的做法。
        """
        rows = [entry(note="我自己改過的說明")]
        self.assertTrue(find_duplicates(rows, [exported("PEI-133", "2.00", note="系統產的原文")]))

    def test_遠端一筆都沒有時誰都不是重複(self):
        self.assertEqual((), find_duplicates([entry()], []))

    def test_同一個_issue_有多列時逐列比時數(self):
        # 一天在同一個 issue 上有好幾筆是常態。索引只留最後一列的話，前面那幾
        # 筆的時數就永遠對不上 → 漏判 → 重複工時
        remote = [
            exported("PEI-133", "1.00"),
            exported("PEI-133", "2.5"),
            exported("PEI-133", "3.00"),
        ]
        for hours in ("1.00", "2.50", "3.00"):
            with self.subTest(hours=hours):
                self.assertTrue(find_duplicates([entry(hours=hours)], remote))
        self.assertEqual((), find_duplicates([entry(hours="4.00")], remote))

    def test_已經送出的筆不參與比對(self):
        """帶著 `zplanner_worklog_id` 的筆由 AC-2 直接跳過。

        算進來只會在回應裡多出幾筆看起來像「被去重擋下」的假訊息，而人會以為
        自己少送了一筆。
        """
        rows = [entry(zplanner_worklog_id=99310, state="submitted")]
        self.assertEqual((), find_duplicates(rows, [exported("PEI-133", "2.00")]))

    def test_unknown_的筆要參與比對(self):
        """⚠️ 這是刻意的，而且是 `unknown` 唯一的出路。

        `unknown` 的意思是「可能已經寫進去了」，而對帳的唯一管道正是 export。
        真的寫進去了，這裡就會比對到並跳過——不必先自動重試一次才發現重複。
        """
        rows = [entry(state="unknown")]
        self.assertTrue(find_duplicates(rows, [exported("PEI-133", "2.00")]))

    def test_沒選_issue_的筆交給閘處理(self):
        # 同一個問題不要有兩種說法：AC-1 的閘已經會擋下它
        self.assertEqual(
            (), find_duplicates([entry(issue_id=None, issue_key=None)], [exported("", "2.00")])
        )

    def test_選了_id_卻沒有_key_時擋下整批(self):
        """匯出的 CSV 只有 `issue_key` 沒有 `issue_id`，所以本地缺 key 就沒有
        東西可以跟遠端對——那一筆會**完全沒有經過去重**就送出去。

        回「沒有重複」是把一個不可逆的風險靜默吞掉（D-2：重複工時月結後刪不掉，
        只能走 correction-request）。去重是唯一擋得住它的那一道，寧可整批停下來
        要人處理，也不要讓那道無聲地失效。
        """
        with self.assertRaises(InvalidParameter):
            find_duplicates([entry(issue_key=None)], [exported("PEI-133", "2.00")])

    def test_遠端時數讀不懂時擋下整批(self):
        """判不出時數就判不出是不是重複，而「判不出就當沒有」正是 D-2 要防的
        那個不可逆錯誤——也違反這個 codebase 的硬規則「認不出來就拋，絕不猜」。"""
        with self.assertRaises(ZPlannerApiError):
            find_duplicates([entry()], [exported("PEI-133", "兩小時")])

    def test_無關_issue_的壞資料不該拖垮整批(self):
        """正對照：遠端那一天可能有幾十列與這份草稿完全無關的工時。

        為它們的格式問題停掉整批送出是沒有道理的——那會讓一列別人專案的壞資料
        害我今天一筆工時都填不了。
        """
        remote = [exported("ZZZ-999", "壞掉的時數"), exported("PEI-133", "2.00")]
        self.assertTrue(find_duplicates([entry()], remote))

    def test_命中時說得出是哪一筆與比對到什麼(self):
        """🔴 D-9(b)：被跳過的筆**必須列進回報讓人看得見**。

        AC-3 會誤殺「同一天、同一 issue、同時數」的合法第二筆。取捨是寧可漏送
        也不要重複，**代價是那筆必須看得見**——靜默跳過的話，使用者永遠不知道
        自己少填了兩小時。
        """
        row = entry(seq=3, hours="2.00")
        hit = find_duplicates([row], [exported("PEI-133", "2", note="遠端既有的")])[0]
        self.assertEqual(row["id"], hit.entry_id)
        self.assertEqual(3, hit.seq)
        payload = hit.to_dict()
        self.assertEqual("PEI-133", payload["issue_key"])
        # 兩邊的原字串都要留著：人要靠 "2.00" vs "2" 看得出來這是刻度差異
        self.assertEqual("2.00", payload["hours"])
        self.assertEqual("2", payload["existing"]["hours"])
        self.assertEqual("遠端既有的", payload["existing"]["note"])

    def test_不認領遠端的_worklog_id(self):
        """🔴 D-9(b) 的另一半：**不**把比對到的遠端 id 寫進 `zplanner_worklog_id`。

        那筆不是我們建的。認領它會讓 D-2 自承的誤殺變成**永久且看不見**——以後
        重產草稿時守門會說「已經送出了」，而其實我們從來沒送過它。
        （匯出的 CSV 根本不含 id 欄位，所以這裡的斷言是「回報裡不准冒出一個」。）
        """
        hit = find_duplicates([entry()], [exported("PEI-133", "2.00")])[0]
        self.assertNotIn("zplanner_worklog_id", hit.to_dict())
        self.assertNotIn("id", hit.to_dict()["existing"])

    def test_帶_entry_ids_時只比對選中的那幾筆(self):
        target = entry()
        rows = [target, entry(issue_id=502, issue_key="PEI-140", hours="1.50")]
        remote = [exported("PEI-133", "2.00"), exported("PEI-140", "1.50")]
        found = find_duplicates(rows, remote, entry_ids=[target["id"]])
        self.assertEqual([target["id"]], [d.entry_id for d in found])


# --------------------------------------------------------------------------
# AC-5／AC-6：例外 → state 分類
# --------------------------------------------------------------------------


class ErrorClassificationTest(unittest.TestCase):
    """分界線只有一條，而且是 `core/errors.py` 明文寫下的：

        **只有拿到 code != 200 的回應信封才證明沒有寫進去**——那是 ZPlanner
        應用層親口說的。

    所以分類不是按「錯誤嚴不嚴重」，是按「這個例外證不證明得了沒寫進去」。
    證明得了 → `failed`（可以重送）；證明不了 → `unknown`（**一律不自動重試**，
    改請人用 `export_worklogs` 對帳）。

    誤判的代價不對稱：把 unknown 當 failed 會讓人放心再按一次，然後 ZPlanner
    那邊多一筆，月結後刪不掉；把 failed 當 unknown 只是多要人對帳一次。
    """

    def test_傳輸層失敗是_unknown(self):
        """AC-5：`ZPlannerWriteIndeterminate` 就是為這件事存在的。"""
        self.assertEqual(
            "unknown", classify_write_error(ZPlannerWriteIndeterminate("連線在讀回應時斷掉"))
        )

    def test_信封說失敗才是_failed(self):
        """AC-6：這幾種都是 ZPlanner 回了 `code != 200` 的信封。"""
        for exc in (
            ZPlannerForbidden("你在該專案沒有填工時的權限"),
            ZPlannerInvalidParameter("hours 格式不合"),
            ZPlannerNotFound("找不到這個 issue"),
            ZPlannerAuthError("token 過期"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual("failed", classify_write_error(exc))

    def test_五百與逾時也是_unknown_而不是_failed(self):
        """⚠️ 反直覺，所以單獨列一條。

        `ZPlannerUnavailable` 來自 **HTTP 狀態碼**（5xx／429），不是信封。代理層
        的 502／504 完全可能發生在後端已經寫進去之後，所以它證明不了「沒寫進
        去」。歸到 failed 會讓一次 ZPlanner 重啟造成整天的工時被重送一遍。
        """
        self.assertEqual("unknown", classify_write_error(ZPlannerUnavailable("連線逾時")))

    def test_讀不出信封也是_unknown(self):
        """`ZPlannerApiError` 的 docstring 自己寫著：「讀不出 code」等於「不知道
        成功了沒有」。不知道 ＝ unknown，不是 failed。

        這一類直覺上最像「明顯壞掉 ＝ 失敗」，所以特別容易被歸錯邊。
        """
        self.assertEqual(
            "unknown", classify_write_error(ZPlannerApiError("回傳了預期外的資料形狀"))
        )

    def test_不認得的例外一律當_unknown(self):
        """認不出來就往安全那邊倒。

        這裡的「安全」是**不要自動重送**：一個沒預期到的例外可能發生在請求送出
        之後（例如解析回應時炸掉），當成 failed 等於鼓勵重送。
        """
        for exc in (RuntimeError("誰知道發生什麼事"), TypeError("x"), KeyError("id")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual("unknown", classify_write_error(exc))

    def test_分類只會回這兩種狀態之一(self):
        """`state` 拼錯不會有人發現——SQLite 沒有 CHECK 約束擋它，畫面上只會
        少一種顏色。"""
        for exc in (
            ZPlannerWriteIndeterminate("x"),
            ZPlannerUnavailable("x"),
            ZPlannerApiError("x"),
            ZPlannerForbidden("x"),
            ZPlannerInvalidParameter("x"),
            ZPlannerNotFound("x"),
            ZPlannerAuthError("x"),
            InvalidParameter("x"),
            RuntimeError("x"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertIn(classify_write_error(exc), {"failed", "unknown"})


# --------------------------------------------------------------------------
# D-9(c)：草稿 status 推導
# --------------------------------------------------------------------------


class DraftStatusTest(unittest.TestCase):
    """`worklog_drafts.status` 是 `draft｜partial｜submitted` 三態。

    ⚠️ **`partial` 在這個 codebase 至今從未被寫入過，送出是第一處。** 它必須是
    獨立狀態（DDL 原話）：算成 draft 會讓人以為整天都還沒送，再按一次就把已經
    成功的那幾筆重複填一遍；算成 submitted 則會讓失敗的那幾筆永遠沒人管。
    """

    def status(self, *states):
        return derive_draft_status([{"state": s} for s in states])

    def test_每筆都_submitted_才轉_submitted(self):
        self.assertEqual("submitted", self.status("submitted", "submitted"))

    def test_submitted_混_skipped_也算整天送完了(self):
        """D-9(c) 的原文是「每筆都 submitted **或 skipped**」。

        skipped 的筆（去重跳過、或被併成 0 小時）本來就不會送出去，把它們算成
        「還沒完成」會讓週檢視的 `totals.days_submitted` 永遠是 0——只要那天有
        任何一筆重複，整天就再也標不成已送出。
        """
        self.assertEqual("submitted", self.status("submitted", "skipped"))

    def test_全部_skipped_算送完了(self):
        # 每一筆在 ZPlanner 那邊都已經有了：這天的工時**確實是完整的**，只是
        # 不是這次送的。標成 draft 會讓人再按一次去製造重複
        self.assertEqual("submitted", self.status("skipped"))

    def test_有成功也有沒成功是_partial(self):
        for other in ("failed", "unknown", "pending"):
            with self.subTest(other=other):
                self.assertEqual("partial", self.status("submitted", other))

    def test_一筆都沒動過仍然是_draft(self):
        self.assertEqual("draft", self.status("pending", "pending"))

    def test_只剩_pending_與_failed_時狀態不前進(self):
        """`failed` 的定義是拿到 `code != 200` 的信封——ZPlanner **親口說沒寫進去**。

        所以零筆成功時狀態不該前進：`partial` 在畫面上的意思是「部分已送出」
        （`core/db.py` 的定義是「有幾筆送成功、有幾筆沒有」），而這裡一筆都沒有。
        標成 partial 會讓使用者不敢重送，而此時重送是完全安全的（例如 403 權限
        修好之後）。
        """
        for states in (("failed", "failed"), ("failed", "pending"), ("pending",)):
            with self.subTest(states=states):
                self.assertEqual("draft", self.status(*states))

    def test_有_unknown_就不能說整天還沒送(self):
        """🔴 即使一筆都沒成功。

        `unknown` 的定義就是「**可能已經寫進去了**」。標成 `draft` 等於對使用者
        宣稱「整天都還沒送」——那句話我們沒有證據，而它正好會誘使人再按一次，
        那正是 `ZPlannerWriteIndeterminate` 整條規則要防的重複工時。

        這是它與 `failed` 的分野：failed 有信封當證據，unknown 沒有。
        """
        for states in (("unknown", "unknown"), ("unknown", "pending"), ("failed", "unknown")):
            with self.subTest(states=states):
                self.assertEqual("partial", self.status(*states))

    def test_沒有任何_entries_時是_draft(self):
        """`all()` 對空清單回 `True`，少了這個分支的話，空草稿會被判成
        `submitted`——「這天什麼都沒有」被算成「這天填完了」。"""
        self.assertEqual("draft", derive_draft_status([]))

    def test_只會回三種狀態之一(self):
        for states in (
            ("submitted",),
            ("skipped",),
            ("submitted", "failed"),
            ("pending",),
            ("failed",),
            ("unknown",),
            ("submitted", "skipped", "unknown"),
            (),
        ):
            with self.subTest(states=states):
                self.assertIn(self.status(*states), {"draft", "partial", "submitted"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
