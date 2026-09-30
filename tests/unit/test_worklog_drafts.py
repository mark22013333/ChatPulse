"""工時草稿的存取層（`worklog_drafts` / `worklog_draft_entries`，ADR-0008）。

守四件事：

  1. **一天一份**。同一天產第二次是覆蓋，不是並存——兩份同時存在的話，
     「這天到底送了哪一份」就沒有答案了。
  2. **entries 是整組取代**（比照 code_projects 的 branches），`seq` 由存取層
     重新編號。
  3. **viewer 隔離**。entries 自己沒有 viewer 欄位，每一條路徑都得透過 draft
     反查——少一次就等於讓任何人帶著猜到的 draft_id 改別人的工時。
  4. **保留策略**：未送出 90 天刪；已送出留 365 天，但 90 天後就地清空證據
     （`zplanner_worklog_id` 還在，修改入口不能斷）。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_drafts*.py'
"""

import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from core.errors import InvalidParameter, WorklogAlreadySubmitted  # noqa: E402


def days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


class TempDbCase(unittest.TestCase):
    """每個測試一個全新的暫存 SQLite 檔（做法同 test_work_repos.py）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-worklog-drafts-")
        self.tmpdir = self._tmp.name
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self._db_log = logging.getLogger("chatpulse.db")
        self._db_log_level = self._db_log.level
        self._db_log.setLevel(logging.WARNING)
        db.close_connection()
        self._patches = [
            mock.patch.object(cfg, "DB_PATH", self.db_path),
            mock.patch.object(cfg, "DATA_DIR", self.tmpdir),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._restore)
        db.init_db()
        self.vid = self.new_viewer("1")
        self.other = self.new_viewer("2")

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    def new_viewer(self, suffix: str) -> int:
        row = repo.upsert_viewer(
            f"users/{suffix.rjust(6, '0')}", f"v{suffix}@example.com", f"Viewer {suffix}"
        )
        return int(row["id"])


ENTRY = {
    "issue_id": None,
    "issue_key": None,
    "hours": "3.50",
    "allocated_hours": "3.50",
    "note": "修正匯出逾時",
    "weight": 1.25,
    "rounded_up": True,
    "evidence_refs": ["commit:" + "a" * 40, "chat:spaces/AAA/messages/m1"],
    "candidates": [{"issue_id": 9, "issue_key": "PEI-133", "confidence": "strong"}],
    "state": "pending",
}


class TestDraftUpsert(TempDbCase):
    def test_create_and_read_back(self):
        draft = repo.upsert_worklog_draft(
            self.vid,
            "2026-09-13",
            tz_offset="+08:00",
            total_hours="8.00",
            generation_meta={"work_date": "2026-09-13", "commits": []},
            generated_at="2026-09-13T04:00:00+00:00",
        )
        self.assertEqual(draft["work_date"], "2026-09-13")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["total_hours"], "8.00")
        self.assertEqual(draft["tz_offset"], "+08:00")
        # JSON 欄位解開後是真的 dict，不是字串
        self.assertEqual(draft["generation_meta"]["work_date"], "2026-09-13")
        self.assertEqual(draft["entries"], [])

    def test_one_per_day(self):
        first = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        second = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        self.assertEqual(first["id"], second["id"], "同一天產第二次是覆蓋，不是並存")
        rows = db.query_all("SELECT id FROM worklog_drafts WHERE viewer_id = ?", (self.vid,))
        self.assertEqual(len(rows), 1)

    def test_unset_keeps_untouched_fields(self):
        repo.upsert_worklog_draft(
            self.vid, "2026-09-13", tz_offset="+08:00", total_hours="8.00",
            generation_meta={"a": 1},
        )
        kept = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        self.assertEqual(kept["total_hours"], "8.00")
        self.assertEqual(kept["generation_meta"], {"a": 1})

    def test_none_clears_total_hours(self):
        """`None` ＝ 還沒決定總時數，那是使用者會停留的狀態，必須存得下去。"""
        repo.upsert_worklog_draft(
            self.vid, "2026-09-13", tz_offset="+08:00", total_hours="8.00"
        )
        cleared = repo.upsert_worklog_draft(
            self.vid, "2026-09-13", tz_offset="+08:00", total_hours=None
        )
        self.assertIsNone(cleared["total_hours"])

    def test_created_at_survives_update(self):
        with mock.patch.object(repo, "_now", return_value="2026-01-01T00:00:00+00:00"):
            first = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        second = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertNotEqual(second["updated_at"], first["updated_at"])

    def test_tz_offset_is_overwritten_on_regeneration(self):
        """換時區重產，日界的依據必須跟著換——否則事後重建不出這份草稿。"""
        repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        again = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+09:00")
        self.assertEqual(again["tz_offset"], "+09:00")

    def test_two_viewers_can_have_the_same_day(self):
        a = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        b = repo.upsert_worklog_draft(self.other, "2026-09-13", tz_offset="+08:00")
        self.assertNotEqual(a["id"], b["id"])

    def test_viewer_isolation(self):
        draft = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        self.assertIsNone(repo.get_worklog_draft(self.other, "2026-09-13"))
        self.assertIsNone(
            repo.get_worklog_draft_by_id(self.other, draft["id"]), "id 猜對了也讀不到"
        )

    def test_missing_day_is_none_not_error(self):
        """沒有草稿是**正常狀態**，不是錯誤（比照 DRAFT_NOT_FOUND 的判準）。"""
        self.assertIsNone(repo.get_worklog_draft(self.vid, "2026-09-13"))

    def test_delete(self):
        repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        self.assertTrue(repo.delete_worklog_draft(self.vid, "2026-09-13"))
        self.assertFalse(repo.delete_worklog_draft(self.vid, "2026-09-13"))

    def test_broken_json_does_not_break_reading(self):
        """存壞的 meta 該讓證據少一塊，不該讓整份草稿讀不回來。"""
        draft = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")
        db.execute(
            "UPDATE worklog_drafts SET generation_meta_json = '{壞掉' WHERE id = ?",
            (draft["id"],),
        )
        self.assertEqual(repo.get_worklog_draft(self.vid, "2026-09-13")["generation_meta"], {})


class TestEntries(TempDbCase):
    def setUp(self) -> None:
        super().setUp()
        self.draft = repo.upsert_worklog_draft(self.vid, "2026-09-13", tz_offset="+08:00")

    def test_replace_writes_and_renumbers(self):
        saved = repo.replace_worklog_entries(
            self.vid, self.draft["id"], [dict(ENTRY), dict(ENTRY, note="第二筆")]
        )
        self.assertEqual([e["seq"] for e in saved["entries"]], [1, 2])
        first = saved["entries"][0]
        self.assertEqual(first["hours"], "3.50")
        self.assertEqual(first["allocated_hours"], "3.50")
        self.assertEqual(first["weight"], 1.25)
        self.assertIs(first["rounded_up"], True)
        self.assertIs(first["is_locked"], False)
        self.assertEqual(first["evidence_refs"], ENTRY["evidence_refs"])
        self.assertEqual(first["candidates"][0]["issue_key"], "PEI-133")
        self.assertEqual(first["state"], "pending")

    def test_replace_is_whole_replacement(self):
        repo.replace_worklog_entries(
            self.vid, self.draft["id"], [dict(ENTRY), dict(ENTRY), dict(ENTRY)]
        )
        saved = repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        self.assertEqual(len(saved["entries"]), 1, "整組取代，不是逐筆 merge")
        self.assertEqual(saved["entries"][0]["seq"], 1)

    def test_replace_with_empty_list_clears(self):
        repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        saved = repo.replace_worklog_entries(self.vid, self.draft["id"], [])
        self.assertEqual(saved["entries"], [])

    def test_replace_rejects_other_viewers_draft(self):
        self.assertIsNone(
            repo.replace_worklog_entries(self.other, self.draft["id"], [dict(ENTRY)])
        )
        self.assertEqual(repo.get_worklog_draft(self.vid, "2026-09-13")["entries"], [])

    def test_replace_touches_draft_updated_at(self):
        db.execute(
            "UPDATE worklog_drafts SET updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", self.draft["id"]),
        )
        before = repo.get_worklog_draft(self.vid, "2026-09-13")
        after = repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        self.assertNotEqual(after["updated_at"], before["updated_at"])

    def test_refuse_submitted_rolls_the_whole_thing_back(self):
        """🔴 守門與寫入必須在**同一個交易**裡，中間不能有縫。

        呼叫端在外面先查一次是不夠的：草稿產生要掃 git、要打 ZPlanner，守門與
        真正寫入之間隔得掉數十秒；那段時間內另一條路徑把工時送出去，這裡的
        整組 DELETE 就會把剛拿到的 `zplanner_worklog_id` 靜默抹掉。
        """
        repo.replace_worklog_entries(
            self.vid,
            self.draft["id"],
            [dict(ENTRY, note="已送出", zplanner_worklog_id=9931, state="submitted")],
        )
        before = repo.get_worklog_draft(self.vid, "2026-09-13")

        with self.assertRaises(WorklogAlreadySubmitted) as ctx:
            repo.replace_worklog_entries(
                self.vid,
                self.draft["id"],
                [dict(ENTRY, note="重產出來的")],
                refuse_submitted=True,
            )
        self.assertEqual(ctx.exception.detail["submitted_count"], 1)

        after = repo.get_worklog_draft(self.vid, "2026-09-13")
        self.assertEqual([e["note"] for e in after["entries"]], ["已送出"])
        self.assertEqual([e["zplanner_worklog_id"] for e in after["entries"]], [9931])
        # 交易整個回捲：連 updated_at 都不該動
        self.assertEqual(after["updated_at"], before["updated_at"])

    def test_refuse_submitted_lets_an_untouched_draft_through(self):
        """正對照：沒有任何一筆送出時，這道守門不准擋路。"""
        repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        saved = repo.replace_worklog_entries(
            self.vid,
            self.draft["id"],
            [dict(ENTRY, note="重產出來的")],
            refuse_submitted=True,
        )
        self.assertEqual([e["note"] for e in saved["entries"]], ["重產出來的"])

    def test_refuse_submitted_defaults_to_off(self):
        """預設不擋——是否要擋是呼叫端的政策，不是這一層的（模組慣例）。"""
        repo.replace_worklog_entries(
            self.vid, self.draft["id"], [dict(ENTRY, zplanner_worklog_id=9932)]
        )
        saved = repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        self.assertEqual([e["zplanner_worklog_id"] for e in saved["entries"]], [None])

    def test_hours_may_be_null(self):
        """沒決定總時數時 `hours` 留 null——不是 0，0 的意思是「不送出」。"""
        saved = repo.replace_worklog_entries(
            self.vid, self.draft["id"], [dict(ENTRY, hours=None, allocated_hours=None)]
        )
        self.assertIsNone(saved["entries"][0]["hours"])
        self.assertIsNone(saved["entries"][0]["allocated_hours"])

    def test_entries_cascade_when_draft_is_deleted(self):
        repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY)])
        repo.delete_worklog_draft(self.vid, "2026-09-13")
        rows = db.query_all(
            "SELECT id FROM worklog_draft_entries WHERE draft_id = ?", (self.draft["id"],)
        )
        self.assertEqual(rows, [])

    def test_remote_worklog_id_is_globally_unique(self):
        """同一筆 ZPlanner 工時不可以被兩筆草稿認領（partial unique index）。"""
        other_draft = repo.upsert_worklog_draft(self.vid, "2026-09-14", tz_offset="+08:00")
        repo.replace_worklog_entries(
            self.vid, self.draft["id"], [dict(ENTRY, zplanner_worklog_id=9931)]
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_worklog_entries(
                self.vid, other_draft["id"], [dict(ENTRY, zplanner_worklog_id=9931)]
            )

    def test_null_remote_ids_do_not_collide(self):
        other_draft = repo.upsert_worklog_draft(self.vid, "2026-09-14", tz_offset="+08:00")
        repo.replace_worklog_entries(self.vid, self.draft["id"], [dict(ENTRY), dict(ENTRY)])
        repo.replace_worklog_entries(self.vid, other_draft["id"], [dict(ENTRY)])
        self.assertEqual(len(repo.get_worklog_draft(self.vid, "2026-09-14")["entries"]), 1)


class TestUpdateEntries(TempDbCase):
    """`update_worklog_entries`：PUT 的存取層（D-11）。

    與 `replace_worklog_entries` 語意相反：**逐筆只改帶到的欄位**，不刪不增。
    守的三件事：整批同一交易（全有或全無）、viewer 隔離、`UNSET` 與 `None`
    是兩件事。
    """

    DATE = "2026-09-13"

    def setUp(self) -> None:
        super().setUp()
        self.draft = repo.upsert_worklog_draft(self.vid, self.DATE, tz_offset="+08:00")
        repo.replace_worklog_entries(
            self.vid,
            self.draft["id"],
            [
                dict(ENTRY, note="第一筆", issue_id=9, issue_key="PEI-133",
                     project_id=42, project_key="PEI"),
                dict(ENTRY, note="第二筆"),
            ],
        )
        self.first_id, self.second_id = [
            e["id"] for e in repo.get_worklog_draft(self.vid, self.DATE)["entries"]
        ]
        # 固定一個舊的 updated_at 當基準：被拒絕後它沒變，才證明交易真的回捲
        db.execute(
            "UPDATE worklog_drafts SET updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", self.draft["id"]),
        )

    def update(self, updates, viewer_id=None):
        return repo.update_worklog_entries(
            self.vid if viewer_id is None else viewer_id, self.DATE, updates
        )

    def stored(self):
        return repo.get_worklog_draft(self.vid, self.DATE)

    def test_updates_only_the_given_fields(self):
        saved = self.update([{"id": self.second_id, "hours": "1.50"}])
        second = saved["entries"][1]
        self.assertEqual(second["hours"], "1.50")
        self.assertEqual(second["note"], "第二筆", "沒帶 note 就不准動 note")
        # 回傳與 get_worklog_draft 同形狀、同內容
        self.assertEqual(saved, self.stored())

    def test_unset_versus_none(self):
        """鍵不在 dict 裡＝不改；值是 None＝清除。兩者分不清就會一邊存一邊丟。"""
        self.update([{"id": self.first_id, "note": None}])
        first = self.stored()["entries"][0]
        self.assertIsNone(first["note"])
        self.assertEqual(first["issue_key"], "PEI-133", "沒帶的歸屬欄位被當成 None 清掉了")
        self.assertEqual(first["hours"], ENTRY["hours"])

    def test_non_editable_key_is_a_programming_error(self):
        """server 之外的呼叫端帶了 state 之類的鍵：拋 ValueError，一行都不動。"""
        before = self.stored()
        with self.assertRaises(ValueError):
            self.update([{"id": self.first_id, "note": "順便改", "state": "submitted"}])
        self.assertEqual(self.stored(), before)

    def test_does_not_add_or_remove_rows(self):
        self.update([{"id": self.first_id, "note": "改"}])
        self.assertEqual(
            [e["id"] for e in self.stored()["entries"]], [self.first_id, self.second_id]
        )

    def test_touches_draft_updated_at(self):
        """正對照：成功的更新會動 updated_at——否則下面「沒動」的斷言什麼都證明不了。"""
        saved = self.update([{"id": self.first_id, "note": "改"}])
        self.assertNotEqual(saved["updated_at"], "2026-01-01T00:00:00+00:00")

    def test_other_viewer_gets_none_and_nothing_moves(self):
        before = self.stored()
        self.assertIsNone(self.update([{"id": self.first_id, "note": "偷改"}], viewer_id=self.other))
        self.assertEqual(self.stored(), before)

    def test_missing_day_is_none(self):
        self.assertIsNone(
            repo.update_worklog_entries(self.vid, "2026-09-14", [{"id": self.first_id, "note": "x"}])
        )

    def test_unknown_id_rolls_back_the_whole_batch(self):
        """合法的那筆排在前面也不准寫進去（D-11 全有或全無）。"""
        before = self.stored()
        with self.assertRaises(InvalidParameter) as ctx:
            self.update([{"id": self.first_id, "note": "前面這筆"}, {"id": 99999, "note": "x"}])
        self.assertEqual(ctx.exception.detail["unknown_entry_ids"], [99999])
        self.assertEqual(
            sorted(ctx.exception.detail["draft_entry_ids"]), sorted([self.first_id, self.second_id])
        )
        self.assertEqual(self.stored(), before)

    def test_submitted_entry_rolls_back_the_whole_batch(self):
        """D-21：要改的筆中有已送出者 → 拋，同批未送出的那筆也不動。"""
        db.execute(
            "UPDATE worklog_draft_entries SET zplanner_worklog_id = 9931, state = 'submitted' "
            "WHERE id = ?",
            (self.first_id,),
        )
        before = self.stored()
        with self.assertRaises(WorklogAlreadySubmitted) as ctx:
            self.update([{"id": self.second_id, "note": "未送出"}, {"id": self.first_id, "note": "已送出"}])
        self.assertEqual(ctx.exception.detail["submitted_count"], 1)
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.stored()["updated_at"], "2026-01-01T00:00:00+00:00")

    def test_submitted_sibling_does_not_block_other_entries(self):
        """D-21 正對照：已送出的筆沒被點名，就不准擋。"""
        db.execute(
            "UPDATE worklog_draft_entries SET zplanner_worklog_id = 9931, state = 'submitted' "
            "WHERE id = ?",
            (self.first_id,),
        )
        saved = self.update([{"id": self.second_id, "note": "改得動"}])
        self.assertEqual(saved["entries"][1]["note"], "改得動")
        self.assertEqual(saved["entries"][0]["zplanner_worklog_id"], 9931)

    def test_failure_midway_rolls_back_earlier_rows(self):
        """🔴 守門都過了、寫到第二筆才失敗：第一筆也不准留下。

        用一個只在測試 DB 裡存在的 trigger 讓第二筆的 UPDATE 失敗——這是
        「逐筆 UPDATE 各自 commit」的實作唯一會露餡的地方，前面的守門測試
        全部在寫入之前就拋了，驗不到這條。
        """
        db.execute(
            "CREATE TRIGGER boom BEFORE UPDATE ON worklog_draft_entries "
            "WHEN NEW.note = '炸' BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        before = self.stored()
        with self.assertRaises(sqlite3.DatabaseError):
            self.update([{"id": self.first_id, "note": "先寫的"}, {"id": self.second_id, "note": "炸"}])
        self.assertEqual(self.stored(), before)

    def test_does_not_touch_submission_bookkeeping(self):
        """AC-8：只 SET 可編輯欄位。"""
        db.execute(
            "UPDATE worklog_draft_entries SET state = 'failed', is_locked = 1, "
            "last_error_code = 'X', last_error_message = 'm', submitted_at = 't' WHERE id = ?",
            (self.second_id,),
        )
        before = self.stored()["entries"][1]
        self.update([{"id": self.second_id, "hours": "0.50", "note": "改",
                      "issue_id": 1, "issue_key": "A-1", "project_id": 2, "project_key": "A"}])
        after = self.stored()["entries"][1]
        for key in ("state", "is_locked", "last_error_code", "last_error_message",
                    "submitted_at", "zplanner_worklog_id", "allocated_hours", "evidence_refs"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["issue_key"], "A-1")


class TestWeekRange(TempDbCase):
    def _day(self, date: str, entries):
        draft = repo.upsert_worklog_draft(
            self.vid, date, tz_offset="+08:00", total_hours="8.00"
        )
        repo.replace_worklog_entries(self.vid, draft["id"], entries)
        return draft

    def test_range_is_inclusive_and_ordered(self):
        for date in ("2026-09-07", "2026-09-08", "2026-09-11", "2026-09-14", "2026-09-15"):
            repo.upsert_worklog_draft(self.vid, date, tz_offset="+08:00")
        rows = repo.list_worklog_drafts_in_range(self.vid, "2026-09-08", "2026-09-14")
        self.assertEqual(
            [r["work_date"] for r in rows], ["2026-09-08", "2026-09-11", "2026-09-14"]
        )

    def test_aggregates(self):
        self._day(
            "2026-09-11",
            [
                dict(ENTRY, hours="3.50", issue_id=9, state="submitted"),
                dict(ENTRY, hours="4.50", issue_id=None, state="pending"),
                dict(ENTRY, hours=None, issue_id=None, state="failed"),
            ],
        )
        row = repo.list_worklog_drafts_in_range(self.vid, "2026-09-08", "2026-09-14")[0]
        self.assertEqual(row["entry_count"], 3)
        # 真正會送出的是各筆之和，與 total_hours 分開算
        self.assertEqual(row["hours"], "8.00")
        self.assertEqual(row["total_hours"], "8.00")
        self.assertEqual(row["submitted_count"], 1)
        self.assertEqual(row["failed_count"], 1)
        self.assertEqual(row["unassigned_count"], 2, "還沒選專案的筆數要看得出來")

    def test_week_rows_do_not_carry_entries(self):
        """週檢視只畫狀態與時數；整包 entries 帶過去只是讓回應大一個量級。"""
        self._day("2026-09-11", [dict(ENTRY)])
        row = repo.list_worklog_drafts_in_range(self.vid, "2026-09-08", "2026-09-14")[0]
        self.assertNotIn("entries", row)

    def test_range_does_not_cross_viewers(self):
        repo.upsert_worklog_draft(self.other, "2026-09-11", tz_offset="+08:00")
        self.assertEqual(
            repo.list_worklog_drafts_in_range(self.vid, "2026-09-08", "2026-09-14"), []
        )

    def test_hours_sum_ignores_garbage(self):
        self._day("2026-09-11", [dict(ENTRY, hours="3.50"), dict(ENTRY, hours="壞掉")])
        row = repo.list_worklog_drafts_in_range(self.vid, "2026-09-08", "2026-09-14")[0]
        self.assertEqual(row["hours"], "3.50", "算不動的值跳過，但不讓整個加總失敗")


class TestRetention(TempDbCase):
    """保留策略：未送出 90 天、已送出 365 天，證據 90 天。"""

    def _aged_draft(self, date: str, *, status: str, age_days: int, entries=None):
        with mock.patch.object(repo, "_now", return_value=days_ago(age_days)):
            draft = repo.upsert_worklog_draft(
                self.vid, date, tz_offset="+08:00", status=status
            )
            if entries:
                repo.replace_worklog_entries(self.vid, draft["id"], entries)
        return draft

    def test_unsent_draft_expires_after_90_days(self):
        self._aged_draft("2026-01-01", status="draft", age_days=91)
        self._aged_draft("2026-02-01", status="draft", age_days=89)
        result = repo.purge_expired()
        self.assertEqual(result["worklog_drafts_deleted"], 1)
        self.assertIsNone(repo.get_worklog_draft(self.vid, "2026-01-01"))
        self.assertIsNotNone(repo.get_worklog_draft(self.vid, "2026-02-01"))

    def test_submitted_draft_survives_to_365_days(self):
        """留著是為了 `zplanner_worklog_id`——少了它就沒有修改與刪除的入口。"""
        self._aged_draft(
            "2026-01-01",
            status="submitted",
            age_days=200,
            entries=[dict(ENTRY, zplanner_worklog_id=9931, state="submitted")],
        )
        repo.purge_expired()
        kept = repo.get_worklog_draft(self.vid, "2026-01-01")
        self.assertIsNotNone(kept)
        self.assertEqual(kept["entries"][0]["zplanner_worklog_id"], 9931)

    def test_submitted_draft_expires_after_365_days(self):
        self._aged_draft("2025-01-01", status="submitted", age_days=366)
        repo.purge_expired()
        self.assertIsNone(repo.get_worklog_draft(self.vid, "2025-01-01"))

    def test_evidence_is_scrubbed_in_place_after_90_days(self):
        self._aged_draft(
            "2026-01-01",
            status="submitted",
            age_days=120,
            entries=[dict(ENTRY, zplanner_worklog_id=9931, state="submitted")],
        )
        result = repo.purge_expired()
        self.assertEqual(result["worklog_evidence_scrubbed"], 1)
        entry = repo.get_worklog_draft(self.vid, "2026-01-01")["entries"][0]
        self.assertEqual(entry["evidence_refs"], [])
        self.assertEqual(entry["candidates"], [])
        # 索引與 note 留著：沒有 worklog id 就沒有修改入口
        self.assertEqual(entry["zplanner_worklog_id"], 9931)
        self.assertEqual(entry["note"], ENTRY["note"])

    def test_scrub_does_not_touch_recent_submitted_drafts(self):
        self._aged_draft(
            "2026-09-01",
            status="submitted",
            age_days=10,
            entries=[dict(ENTRY, zplanner_worklog_id=9932, state="submitted")],
        )
        result = repo.purge_expired()
        self.assertEqual(result["worklog_evidence_scrubbed"], 0)
        entry = repo.get_worklog_draft(self.vid, "2026-09-01")["entries"][0]
        self.assertEqual(entry["evidence_refs"], ENTRY["evidence_refs"])

    def test_scrub_is_idempotent(self):
        """跑第二次不該再數一遍——否則每天的維運報表都會看到同一批。"""
        self._aged_draft(
            "2026-01-01",
            status="submitted",
            age_days=120,
            entries=[dict(ENTRY, zplanner_worklog_id=9931, state="submitted")],
        )
        repo.purge_expired()
        self.assertEqual(repo.purge_expired()["worklog_evidence_scrubbed"], 0)

    def test_existing_purge_keys_are_untouched(self):
        """既有呼叫端（lifespan 的維運工作）讀的是這兩個 key。"""
        result = repo.purge_expired()
        for key in ("summaries_deleted", "mentions_deleted"):
            self.assertIn(key, result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
