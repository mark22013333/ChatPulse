"""單日工時草稿的讀寫端點：`GET`／`PUT /api/v1/worklogs/{work_date}`（D-8～D-21）。

這兩支端點守的事情，失敗時都是**安靜的**，所以每一道守門都配一條正對照——
「一律拒絕」的實作也會讓所有拒絕測試變綠：

  1. **PUT 是逐筆欄位更新，不是整組取代**（D-2）。沒帶的鍵不准被清掉；尤其
     `state`／`zplanner_worklog_id`／`is_locked`／`last_error_*`／`submitted_at`
     這幾個不是 Viewer 能改的欄位（AC-8）。
  2. **已送出的守門是逐筆的**（D-21 取代 D-3）：要改的筆裡有已送出者才 409，
     且整批一筆都不動；failed 的那筆必須改得動，否則選錯專案的人無路可修。
  3. **PUT 與 submit 共用 `_claim_worklog_submit`**（D-14）：送出中改值會讓
     ZPlanner 收舊值、本地存新值，沒有任何訊號。
  4. **PUT 存了歸屬之後重產**會撞上 `_refuse_destructive_regeneration` 的
     `assigned_count` 400——plan.md 風險段第一條：這個分支過去幾乎沒被真的
     觸發過，這裡**實際走一次**，再帶 force 驗證會清成 null。

⚠️ 直接呼叫路由函式，不經 FastAPI：拋出的是 Python 例外本身，detail 從
`ctx.exception.detail` 讀（`chatpulse_error_handler` 把 dict detail 放進回應
主體這件事，`test_worklog_endpoints.py` 已經驗過）。🔴 不斷言錯誤訊息的措辭。

⚠️ 全部使用暫存檔資料庫；ZPlanner 一律是假的 client，不會發出任何請求。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_day_endpoints*.py'
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest import mock

import pydantic

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from core.errors import (  # noqa: E402
    InvalidParameter,
    WorklogAlreadySubmitted,
    WorklogDraftNotFound,
    WorklogSubmitInProgress,
)
from core.worklog_evidence import DayEvidence, Evidence, extract_terms  # noqa: E402
from dashboard.api import server  # noqa: E402

DATE = "2026-09-13"
TZ = "+08:00"

REFS_A = ["commit:" + "a" * 40, "chat:spaces/AAA/messages/m1"]
REFS_B = ["commit:" + "b" * 40]

#: `generation_meta.clusters[]` 的最小形狀（只放 D-9 比對會用到的鍵）。
META = {
    "type": "meta",
    "clusters": [
        {"key": "c-a", "label": "匯出逾時", "reason": "同一個 issue key", "evidence_refs": REFS_A},
        {"key": "c-b", "label": "整理設定", "reason": "同一個 repo", "evidence_refs": REFS_B},
    ],
}


# --------------------------------------------------------------------------
# 假資料（刻意不 import 其他測試檔的 fixture——這個 repo 的慣例）
# --------------------------------------------------------------------------


def commit(sha: str, subject: str, *, repo_name: str = "cs-backend") -> Evidence:
    return Evidence(
        kind="commit",
        ref=f"commit:{sha}",
        occurred_at=f"{DATE}T10:00:00+08:00",
        title=subject,
        source_label=repo_name,
        terms=tuple(extract_terms(subject)),
        weight_input=1.0,
        sha=sha,
        short_sha=sha[:7],
        author_email="me@corp.com",
    )


def day_evidence(*items) -> DayEvidence:
    return DayEvidence(
        work_date=DATE,
        tz_offset=TZ,
        since=f"{DATE}T00:00:00+08:00",
        until="2026-09-14T00:00:00+08:00",
        evidence=tuple(items),
    )


class FakeZPlanner:
    """不可用的假 client：草稿產生時只會走降級路徑，不發任何請求。"""

    def available(self):
        return False, "未設定 ZPLANNER_BASE_URL"


def entry(**extra) -> Dict[str, Any]:
    """一筆還沒選歸屬、還沒送出的 entry（`replace_worklog_entries` 的輸入形狀）。"""
    row: Dict[str, Any] = {
        "issue_id": None,
        "issue_key": None,
        "project_id": None,
        "project_key": None,
        "hours": "2.00",
        "allocated_hours": "2.00",
        "note": "做了匯出效能",
        "evidence_refs": list(REFS_A),
        "state": "pending",
    }
    row.update(extra)
    return row


ASSIGN = {"issue_id": 501, "issue_key": "PEI-133", "project_id": 42, "project_key": "PEI"}
UNASSIGN = {"issue_id": None, "issue_key": None, "project_id": None, "project_key": None}


async def _collect(iterator):
    return [chunk async for chunk in iterator]


def _parse(frames) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for frame in frames:
        text = frame if isinstance(frame, str) else frame.decode("utf-8")
        for line in text.strip().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


# --------------------------------------------------------------------------
# 共用底座
# --------------------------------------------------------------------------


class DayCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-worklog-day-")
        self.tmpdir = self._tmp.name
        self._db_log = logging.getLogger("chatpulse.db")
        self._db_log_level = self._db_log.level
        self._db_log.setLevel(logging.WARNING)
        db.close_connection()
        self._patches = [
            mock.patch.object(cfg, "DB_PATH", os.path.join(self.tmpdir, "test.db")),
            mock.patch.object(cfg, "DATA_DIR", self.tmpdir),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._restore)
        db.init_db()
        row = repo.upsert_viewer("users/000001", "me@corp.com", "我")
        self.vid = int(row["id"])
        self.viewer = {"id": self.vid, "google_user_id": "users/000001", "email": "me@corp.com"}
        other = repo.upsert_viewer("users/000002", "you@corp.com", "你")
        self.other_vid = int(other["id"])
        self.other_viewer = {
            "id": self.other_vid,
            "google_user_id": "users/000002",
            "email": "you@corp.com",
        }
        repo.create_work_repo(
            self.vid,
            name="cs-backend",
            repo_path="/repos/cs-backend",
            author_emails=["me@corp.com"],
        )

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    # -- 資料 --------------------------------------------------------------

    def seed(self, *entries: Dict[str, Any], viewer_id: int = None, meta=None) -> Dict[str, Any]:
        vid = self.vid if viewer_id is None else viewer_id
        draft = repo.upsert_worklog_draft(
            vid, DATE, tz_offset=TZ, total_hours="8.00",
            generation_meta=META if meta is None else meta,
        )
        repo.replace_worklog_entries(vid, draft["id"], list(entries))
        return repo.get_worklog_draft(vid, DATE)

    def stored(self) -> Dict[str, Any]:
        """回頭查 DB。**回應說了什麼不算數，落地了什麼才算。**"""
        return repo.get_worklog_draft(self.vid, DATE)

    def ids(self) -> List[int]:
        return [e["id"] for e in self.stored()["entries"]]

    # -- 端點 --------------------------------------------------------------

    def get(self, work_date: str = DATE, viewer=None) -> Dict[str, Any]:
        return server.get_worklog_day(work_date, self.viewer if viewer is None else viewer)

    def put(self, entries: List[Dict[str, Any]], work_date: str = DATE, viewer=None):
        body = server.WorklogDayUpdateBody(entries=entries)
        return server.update_worklog_day(
            work_date, body, self.viewer if viewer is None else viewer
        )

    def assert_bad_request(self, entries: List[Dict[str, Any]]) -> None:
        """請求格式錯 → 400 INVALID_PARAMETER。

        D-12 說格式檢查放 Pydantic model，那條路在 HTTP 上是
        `RequestValidationError` → `validation_error_handler` → 400；也可能由路由
        函式自己拋 `InvalidParameter` → 400。兩條路 HTTP 上是同一個 400，所以
        兩種都收；但**什麼都不拋**（照單全收）就是失敗。
        """
        try:
            self.put(entries)
        except pydantic.ValidationError:
            return
        except InvalidParameter:
            return
        self.fail(f"這個請求應該被 400 擋下，卻被收下了：{entries!r}")

    def stream(self, *, force: bool = False) -> List[Dict[str, Any]]:
        """跑一次草稿產生（SSE），回傳事件清單。證據與 ZPlanner 都是假的。"""
        evidence = day_evidence(
            commit("a" * 40, "fix: PEI-133 匯出逾時"),
            commit("b" * 40, "整理設定", repo_name="chatpulse"),
        )
        request = server.WorklogDraftRequest(tz_offset=TZ, force=force)
        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", lambda **kw: evidence
        ), mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner()):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            return _parse(asyncio.run(_collect(response.body_iterator)))


# --------------------------------------------------------------------------
# GET
# --------------------------------------------------------------------------


class TestGetDay(DayCase):
    def test_no_draft_is_404(self):
        with self.assertRaises(WorklogDraftNotFound) as ctx:
            self.get()
        self.assertEqual(ctx.exception.code, "WORKLOG_DRAFT_NOT_FOUND")
        self.assertEqual(ctx.exception.http_status, 404)

    def test_other_viewers_draft_is_404(self):
        """ADR-0002 的同一條線：別人的草稿對我而言就是「不存在」。"""
        self.seed(entry(), viewer_id=self.other_vid)
        with self.assertRaises(WorklogDraftNotFound):
            self.get()
        # 正對照：主人讀得到——證明上面不是「一律 404」
        self.assertEqual(len(self.get(viewer=self.other_viewer)["entries"]), 1)

    def test_bad_date_is_400(self):
        with self.assertRaises(InvalidParameter):
            self.get("2026/09/13")

    def test_flat_shape_with_full_entries(self):
        """AC-5：扁平的草稿物件（不包外層），entries 帶完整欄位。"""
        self.seed(entry(**ASSIGN, hours="3.50"), entry(evidence_refs=list(REFS_B)))
        day = self.get()
        for key in (
            "id", "work_date", "status", "total_hours", "tz_offset",
            "generation_meta", "generated_at", "created_at", "updated_at", "entries",
        ):
            self.assertIn(key, day)
        self.assertEqual(day["work_date"], DATE)
        self.assertEqual(day["total_hours"], "8.00")
        self.assertEqual(len(day["entries"]), 2)
        first = day["entries"][0]
        # repository 形狀的每一個鍵都要在（AC-9／10 畫狀態要用）
        for key in repo.get_worklog_draft(self.vid, DATE)["entries"][0]:
            self.assertIn(key, first)
        # Viewer 存過的歸屬與時數要讀得回來
        self.assertEqual(first["issue_id"], 501)
        self.assertEqual(first["issue_key"], "PEI-133")
        self.assertEqual(first["project_id"], 42)
        self.assertEqual(first["hours"], "3.50")

    def test_cluster_fields_are_attached(self):
        """D-8：每筆 entry 補上所屬群的 key／label／reason。"""
        self.seed(entry(evidence_refs=list(REFS_A)), entry(evidence_refs=list(REFS_B)))
        entries = self.get()["entries"]
        self.assertEqual(
            [(e["cluster_key"], e["label"], e["reason"]) for e in entries],
            [("c-a", "匯出逾時", "同一個 issue key"), ("c-b", "整理設定", "同一個 repo")],
        )

    def test_cluster_match_is_by_set_not_order(self):
        """D-9 比的是**集合相等**：同一組 ref 換個順序仍是同一群。"""
        self.seed(entry(evidence_refs=list(reversed(REFS_A))))
        self.assertEqual(self.get()["entries"][0]["cluster_key"], "c-a")

    def test_unmatched_cluster_gives_nulls_not_position(self):
        """D-9：對不到就三個鍵都給 null，**不准**退回用 seq 位置去對。

        這裡第一筆的 ref 是 REFS_A 的子集合——用位置對的話它會被貼上 c-a 的
        reason，而那正是「重產 meta 換新、entries 被拒」時會發生的靜默錯置。
        """
        self.seed(entry(evidence_refs=[REFS_A[0]]), entry(evidence_refs=list(REFS_B)))
        first, second = self.get()["entries"]
        self.assertIsNone(first["cluster_key"])
        self.assertIsNone(first["label"])
        self.assertIsNone(first["reason"])
        # 正對照：同一份草稿裡對得到的那筆照樣有值
        self.assertEqual(second["cluster_key"], "c-b")

    def test_meta_without_clusters_gives_nulls(self):
        self.seed(entry(), meta={})
        e = self.get()["entries"][0]
        self.assertEqual((e["cluster_key"], e["label"], e["reason"]), (None, None, None))

    def test_projection_does_not_leak_into_storage(self):
        """補鍵屬畫面組裝（D-8）：GET 不該回頭改 DB。"""
        self.seed(entry())
        before = self.stored()
        self.get()
        self.assertEqual(self.stored(), before)


# --------------------------------------------------------------------------
# PUT：正對照與逐欄語意
# --------------------------------------------------------------------------


class TestPutUpdates(DayCase):
    def test_updates_hours_assignment_and_note(self):
        """AC-6 正對照：三種可編輯欄位真的改得到，重讀看得到新值。"""
        self.seed(entry(), entry(evidence_refs=list(REFS_B), note="第二筆"))
        first_id, second_id = self.ids()
        result = self.put([{"id": first_id, "hours": "3.50", "note": "改過的備註", **ASSIGN}])

        stored = self.stored()["entries"]
        self.assertEqual(stored[0]["hours"], "3.50")
        self.assertEqual(stored[0]["note"], "改過的備註")
        self.assertEqual(
            {k: stored[0][k] for k in ASSIGN}, ASSIGN, "四個歸屬欄位都要落地"
        )
        # 沒帶到的那一筆原封不動
        self.assertEqual(stored[1]["note"], "第二筆")
        self.assertIsNone(stored[1]["issue_id"])
        # 回應與 GET 同形狀（含 D-8 補的鍵）
        self.assertEqual(result["entries"][0]["cluster_key"], "c-a")
        self.assertEqual(result["entries"][0]["hours"], "3.50")

    def test_put_response_equals_subsequent_get(self):
        """AC-9：PUT 回應畫出來的，與重新整理後 GET 讀回的是同一份。"""
        self.seed(entry(), entry(evidence_refs=list(REFS_B)))
        first_id, _ = self.ids()
        put_result = self.put([{"id": first_id, "hours": "1.50", **ASSIGN}])
        self.assertEqual(put_result["entries"], self.get()["entries"])

    def test_omitted_keys_are_untouched(self):
        """D-2：鍵沒帶＝不改。只改時數時，備註與歸屬不准被順手清掉。"""
        self.seed(entry(**ASSIGN, note="原本的備註"))
        (eid,) = self.ids()
        self.put([{"id": eid, "hours": "4.00"}])
        e = self.stored()["entries"][0]
        self.assertEqual(e["hours"], "4.00")
        self.assertEqual(e["note"], "原本的備註")
        self.assertEqual(e["issue_key"], "PEI-133")

    def test_null_clears(self):
        """帶 null ＝清除，與「沒帶」是兩件事。"""
        self.seed(entry(**ASSIGN, note="原本的備註"))
        (eid,) = self.ids()
        self.put([{"id": eid, "note": None, "hours": None, **UNASSIGN}])
        e = self.stored()["entries"][0]
        self.assertIsNone(e["note"])
        self.assertIsNone(e["hours"])
        for key in ASSIGN:
            self.assertIsNone(e[key])

    def test_does_not_touch_non_viewer_fields(self):
        """AC-8：state／zplanner_worklog_id／is_locked／last_error_*／submitted_at
        不是 Viewer 能改的欄位，PUT 一個都不准碰——整組取代的實作會在這裡露餡。"""
        self.seed(
            entry(
                state="failed",
                is_locked=True,
                last_error_code="ZPLANNER_FORBIDDEN",
                last_error_message="沒有這個專案的填寫權限",
                submitted_at="2026-09-13T10:00:00+00:00",
                zplanner_issue_key="PEI-133",
            )
        )
        before = self.stored()["entries"][0]
        self.put([{"id": before["id"], "hours": "1.00", "note": "改了", **ASSIGN}])
        after = self.stored()["entries"][0]
        for key in (
            "state", "zplanner_worklog_id", "zplanner_issue_key", "is_locked",
            "last_error_code", "last_error_message", "submitted_at",
            "allocated_hours", "evidence_refs", "candidates", "seq", "weight",
        ):
            self.assertEqual(after[key], before[key], f"{key} 被 PUT 動到了")
        # 正對照：同一次 PUT 要改的欄位確實改了，證明上面不是「什麼都沒寫」
        self.assertEqual(after["hours"], "1.00")
        self.assertEqual(after["issue_key"], "PEI-133")

    def test_negative_and_zero_hours_are_accepted(self):
        """D-5：格式以 `require_hours` 為準——負數（沖銷）與 0 都合法，不另卡。"""
        self.seed(entry(), entry(evidence_refs=list(REFS_B)))
        a, b = self.ids()
        self.put([{"id": a, "hours": "-1.50"}, {"id": b, "hours": "0"}])
        self.assertEqual([e["hours"] for e in self.stored()["entries"]], ["-1.50", "0"])

    def test_cannot_add_or_remove_entries(self):
        """D-4：PUT 只更新既有 id，筆數與順序不變。"""
        self.seed(entry(), entry(evidence_refs=list(REFS_B)))
        before = self.ids()
        self.put([{"id": before[1], "note": "只改第二筆"}])
        self.assertEqual(self.ids(), before)


# --------------------------------------------------------------------------
# PUT：請求格式守門（每條都有正對照）
# --------------------------------------------------------------------------


class TestPutValidation(DayCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed(entry(**ASSIGN))
        (self.eid,) = self.ids()
        self.before = self.stored()

    def assert_nothing_changed(self):
        self.assertEqual(self.stored()["entries"], self.before["entries"])

    def test_assignment_fields_must_come_together(self):
        """D-12：四欄任一有帶就四個都要帶——只寫一半，送出閘與重產守門的答案會不同。"""
        self.assert_bad_request([{"id": self.eid, "issue_id": 777}])
        self.assert_bad_request([{"id": self.eid, "issue_id": 777, "issue_key": "PEI-9"}])
        self.assert_bad_request([{"id": self.eid, "project_id": None, "project_key": None}])
        self.assert_nothing_changed()

    def test_issue_id_and_key_must_agree_on_null(self):
        """D-12：`issue_id` 與 `issue_key` 同為 null 或同有值。"""
        self.assert_bad_request(
            [{"id": self.eid, **ASSIGN, "issue_key": None}]
        )
        self.assert_bad_request(
            [{"id": self.eid, **UNASSIGN, "issue_key": "PEI-133"}]
        )
        self.assert_nothing_changed()

    def test_complete_assignment_passes(self):
        """正對照：四欄齊全（有值或全 null）都收下。"""
        self.put([{"id": self.eid, "issue_id": 502, "issue_key": "PEI-140",
                   "project_id": 43, "project_key": "OPS"}])
        self.assertEqual(self.stored()["entries"][0]["issue_key"], "PEI-140")
        self.put([{"id": self.eid, **UNASSIGN}])
        self.assertIsNone(self.stored()["entries"][0]["issue_id"])

    def test_malformed_hours(self):
        """D-5／D-13：`0.333`、四位整數、非數字、非字串都擋。"""
        for bad in ("0.333", "1234", "abc", "1.5h"):
            with self.subTest(hours=bad):
                self.assert_bad_request([{"id": self.eid, "hours": bad}])
        self.assert_bad_request([{"id": self.eid, "hours": 3}])
        self.assert_bad_request([{"id": self.eid, "hours": 1.5}])
        self.assert_nothing_changed()

    def test_wellformed_hours_pass(self):
        """正對照：擋的是格式，不是 0.5 倍數。"""
        for good in ("1.25", "7", "0.10"):
            with self.subTest(hours=good):
                self.put([{"id": self.eid, "hours": good}])
                self.assertEqual(self.stored()["entries"][0]["hours"], good)

    def test_non_contract_key_is_rejected(self):
        """contract §2 追加：單筆帶了契約沒列的鍵 → 400。

        靜默略過的話，前端以為改了 state，DB 其實沒動——那是查不出來的 bug；
        照單全收則等於讓 Viewer 自己把一筆標成 submitted（AC-8 的反面）。
        """
        for key, value in (
            ("state", "submitted"),
            ("zplanner_worklog_id", 9931),
            ("is_locked", True),
            ("allocated_hours", "1.00"),
        ):
            with self.subTest(key=key):
                self.assert_bad_request([{"id": self.eid, "note": "順便改", key: value}])
        self.assert_nothing_changed()

    def test_only_contract_keys_pass(self):
        """正對照：id＋契約列出的六個鍵全帶，照常更新。"""
        self.put([{"id": self.eid, "hours": "1.50", "note": "六個鍵都帶",
                   "issue_id": 502, "issue_key": "PEI-140",
                   "project_id": 43, "project_key": "OPS"}])
        e = self.stored()["entries"][0]
        self.assertEqual(
            (e["hours"], e["note"], e["issue_id"], e["issue_key"], e["project_id"], e["project_key"]),
            ("1.50", "六個鍵都帶", 502, "PEI-140", 43, "OPS"),
        )

    def test_hours_without_digits_is_rejected(self):
        """contract §2 追加：`""`、`"."`、`"-"` 不含任何數字 → 400。

        `""` 若被收下，存進 DB 的是空字串而不是 null：送出閘看 `hours is None`
        判斷「沒有時數」，空字串會漏過那道閘，到 ZPlanner 才變成單筆 failed。
        """
        for bad in ("", ".", "-"):
            with self.subTest(hours=bad):
                self.assert_bad_request([{"id": self.eid, "hours": bad}])
        self.assert_nothing_changed()

    def test_null_hours_clears(self):
        """正對照：要清除時數的正確寫法是 null，這條路必須通。"""
        self.assertEqual(self.stored()["entries"][0]["hours"], "2.00")
        self.put([{"id": self.eid, "hours": None}])
        self.assertIsNone(self.stored()["entries"][0]["hours"])

    def test_empty_entries(self):
        self.assert_bad_request([])

    def test_duplicate_ids(self):
        self.assert_bad_request([{"id": self.eid, "note": "一"}, {"id": self.eid, "note": "二"}])
        self.assert_nothing_changed()

    def test_unknown_entry_id_is_400_with_detail(self):
        """AC-7：不屬於這份草稿的 id → 400，detail 鍵名照 submit。整批不動。"""
        with self.assertRaises(InvalidParameter) as ctx:
            self.put([{"id": self.eid, "note": "合法的這筆"}, {"id": 99999, "note": "x"}])
        detail = ctx.exception.detail
        self.assertEqual(detail["unknown_entry_ids"], [99999])
        self.assertEqual(detail["draft_entry_ids"], [self.eid])
        self.assert_nothing_changed()

    def test_other_viewers_entry_id_counts_as_unknown(self):
        """別人草稿裡的 entry id 帶進來，必須當成「不屬於這份」，不准改到別人的。"""
        other = self.seed(entry(note="別人的"), viewer_id=self.other_vid)
        other_eid = other["entries"][0]["id"]
        with self.assertRaises(InvalidParameter) as ctx:
            self.put([{"id": other_eid, "note": "偷改"}])
        self.assertEqual(ctx.exception.detail["unknown_entry_ids"], [other_eid])
        self.assertEqual(
            repo.get_worklog_draft(self.other_vid, DATE)["entries"][0]["note"], "別人的"
        )

    def test_no_draft_is_404(self):
        with self.assertRaises(WorklogDraftNotFound):
            self.put([{"id": self.eid, "note": "x"}], work_date="2026-09-14")

    def test_other_viewers_draft_is_404(self):
        """拿我的 entry id 去打「別人那一天」：別人那天沒有我的草稿 → 404。"""
        with self.assertRaises(WorklogDraftNotFound):
            self.put([{"id": self.eid, "note": "x"}], viewer=self.other_viewer)
        self.assert_nothing_changed()


# --------------------------------------------------------------------------
# PUT：已送出守門（D-21 逐筆）
# --------------------------------------------------------------------------


class TestPutSubmittedGuard(DayCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed(
            entry(**ASSIGN, state="submitted", zplanner_worklog_id=9931, note="已送出"),
            entry(evidence_refs=list(REFS_B), state="pending", note="還沒送"),
            entry(evidence_refs=[], state="failed", note="送失敗", last_error_code="ZPLANNER_FORBIDDEN"),
        )
        self.sent_id, self.pending_id, self.failed_id = self.ids()

    def test_touching_a_submitted_entry_is_409_and_nothing_moves(self):
        """AC-7：要改的筆中有已送出者 → 409，**連同批合法的那筆也不動**。"""
        db.execute(
            "UPDATE worklog_drafts SET updated_at = ? WHERE viewer_id = ?",
            ("2026-01-01T00:00:00+00:00", self.vid),
        )
        before = self.stored()
        with self.assertRaises(WorklogAlreadySubmitted) as ctx:
            self.put(
                [
                    {"id": self.pending_id, "note": "想一起改"},
                    {"id": self.sent_id, "note": "想改已送出的"},
                ]
            )
        self.assertEqual(ctx.exception.code, "WORKLOG_ALREADY_SUBMITTED")
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.detail["submitted_count"], 1)
        self.assertIs(ctx.exception.detail["force_allowed"], False)
        after = self.stored()
        self.assertEqual(after["entries"], before["entries"])
        self.assertEqual(after["updated_at"], before["updated_at"], "交易沒有整個回捲")

    def test_pending_entry_is_editable_in_a_partly_submitted_draft(self):
        """D-21 正對照：草稿裡有已送出的筆，不影響改**別的**筆。"""
        self.put([{"id": self.pending_id, "note": "改得動", **ASSIGN}])
        entries = self.stored()["entries"]
        self.assertEqual(entries[1]["note"], "改得動")
        # 已送出那筆的 worklog id 一個位元都不能少
        self.assertEqual(entries[0]["zplanner_worklog_id"], 9931)
        self.assertEqual(entries[0]["note"], "已送出")

    def test_failed_entry_is_editable(self):
        """D-21 的存在理由：選錯專案而 failed 的那筆必須改得動，否則只能拿錯的值重送。"""
        self.put([{"id": self.failed_id, "issue_id": 502, "issue_key": "PEI-140",
                   "project_id": 43, "project_key": "OPS"}])
        e = self.stored()["entries"][2]
        self.assertEqual(e["issue_key"], "PEI-140")
        # AC-8：state 與錯誤欄位不因 PUT 而變
        self.assertEqual(e["state"], "failed")
        self.assertEqual(e["last_error_code"], "ZPLANNER_FORBIDDEN")


# --------------------------------------------------------------------------
# PUT：與 submit 共用的鎖（D-14）
# --------------------------------------------------------------------------


class TestPutSubmitLock(DayCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed(entry())
        (self.eid,) = self.ids()

    def test_put_while_submit_holds_the_claim_is_409(self):
        """送出中改值 → ZPlanner 收舊值、本地存新值，無訊號的不一致。所以擋。"""
        with server._claim_worklog_submit(self.vid, DATE):
            with self.assertRaises(WorklogSubmitInProgress) as ctx:
                self.put([{"id": self.eid, "note": "送出途中偷改"}])
        self.assertEqual(ctx.exception.code, "WORKLOG_SUBMIT_IN_PROGRESS")
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.detail["work_date"], DATE)
        self.assertEqual(self.stored()["entries"][0]["note"], "做了匯出效能")

    def test_put_works_once_the_claim_is_released(self):
        """正對照：鎖放掉之後同一個請求照常成功——擋的是「同時」，不是「永遠」。"""
        with server._claim_worklog_submit(self.vid, DATE):
            pass
        self.put([{"id": self.eid, "note": "送完再改"}])
        self.assertEqual(self.stored()["entries"][0]["note"], "送完再改")

    def test_other_day_is_not_blocked(self):
        """鎖的單位是「這個 Viewer 的這一天」。"""
        with server._claim_worklog_submit(self.vid, "2026-09-14"):
            self.put([{"id": self.eid, "note": "別天在送不影響我"}])
        self.assertEqual(self.stored()["entries"][0]["note"], "別天在送不影響我")

    def test_put_holds_the_claim_while_writing(self):
        """PUT 自己也要拿這把鎖：寫入進行中，submit 那一側必須搶不到。"""
        seen: Dict[str, Any] = {}
        real = repo.update_worklog_entries

        def probe(*args, **kwargs):
            try:
                with server._claim_worklog_submit(self.vid, DATE):
                    seen["claimed"] = True
            except WorklogSubmitInProgress:
                seen["claimed"] = False
            return real(*args, **kwargs)

        with mock.patch.object(server.repo, "update_worklog_entries", probe):
            self.put([{"id": self.eid, "note": "寫入中"}])
        self.assertIs(seen.get("claimed"), False, "PUT 寫入時 submit 仍搶得到鎖")

    def test_claim_is_released_after_a_rejected_put(self):
        """被拒絕的 PUT 也要還鎖——否則一次 400 就讓那一天永遠改不了、送不出。"""
        with self.assertRaises(InvalidParameter):
            self.put([{"id": 99999, "note": "x"}])
        with server._claim_worklog_submit(self.vid, DATE):
            pass  # 搶得到就代表有還


# --------------------------------------------------------------------------
# PUT 存了歸屬之後重產（plan.md 風險段第一條，D-6）
# --------------------------------------------------------------------------


class TestRegenerationAfterPut(DayCase):
    def test_untouched_draft_regenerates_without_force(self):
        """正對照：沒人選過歸屬時重產不需要 force——證明下一條的 400 是 PUT 造成的。"""
        self.assertEqual(self.stream()[-1]["type"], "done")
        self.assertEqual(self.stream()[-1]["type"], "done")

    def test_put_assignment_then_regenerate_hits_assigned_count_then_force_clears(self):
        """🔴 實際走一次：產生 → PUT 選歸屬 → 重產被 400（assigned_count）→ force 清成 null。"""
        self.assertEqual(self.stream()[-1]["type"], "done")
        generated = self.stored()["entries"]
        self.assertGreaterEqual(len(generated), 2)
        # AC-3（後端側）：產生出來的一律未指派
        self.assertTrue(all(e["issue_id"] is None and e["issue_key"] is None for e in generated))

        self.put([{"id": generated[0]["id"], **ASSIGN}])
        self.assertEqual(self.stored()["entries"][0]["issue_key"], "PEI-133")

        with self.assertRaises(InvalidParameter) as ctx:
            self.stream()
        detail = ctx.exception.detail
        self.assertEqual(detail["assigned_count"], 1)
        self.assertEqual(detail["assigned_issue_keys"], ["PEI-133"])
        self.assertIs(detail["force_allowed"], True)
        # 擋下來＝真的一筆都沒動
        self.assertEqual(self.stored()["entries"][0]["issue_key"], "PEI-133")

        self.assertEqual(self.stream(force=True)[-1]["type"], "done")
        after = self.stored()["entries"]
        for e in after:
            for key in ASSIGN:
                self.assertIsNone(e[key], f"force 重產後 {key} 應清成 null")

    def test_put_hours_only_does_not_trigger_the_assigned_guard(self):
        """只改時數／備註不算「已指派」：守門看的是 issue_id／issue_key。"""
        self.stream()
        first = self.stored()["entries"][0]
        self.put([{"id": first["id"], "hours": "2.50", "note": "改了備註"}])
        self.assertEqual(self.stream()[-1]["type"], "done")

    def test_get_after_regeneration_reflects_new_clusters(self):
        """D-9：重產後讀回的 cluster 欄位對應的是新 meta，不是位置。"""
        self.stream()
        day = self.get()
        keys = {c["key"] for c in day["generation_meta"]["clusters"]}
        for e in day["entries"]:
            self.assertIn(e["cluster_key"], keys)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
