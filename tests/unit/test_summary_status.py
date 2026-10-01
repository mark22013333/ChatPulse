"""Space 表格的「Summary」欄的資料來源（`repo.latest_summary_times` ＋ `GET /spaces`）。

守三件事：

  1. **ADR-0002**：別的 Viewer 對同一個 Space 產過 Summary，不影響這位 Viewer
     看到的結果——否則畫面會說「已是最新」，而那份 Summary 他根本讀不到。
  2. **一次查完**：不論幾個 Space，DB 只查一次（GROUP BY），不是每個 Space 一次。
  3. 沒產生過的 Space 回 null，不是省略欄位——前端靠 null 判定「從未產生」。

三種狀態本身怎麼判（含時區表示法不同、lastActiveTime 缺少）是前端的純函式，
測試在 `dashboard/frontend/src/lib/spaceTable.test.ts`。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`；也不打任何 Google API
（`list_spaces_cached` 一律換成假資料）。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_summary_status*.py'
"""

import logging
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from dashboard.api import server  # noqa: E402


class TempDbCase(unittest.TestCase):
    """每個測試一個全新的暫存 SQLite 檔（做法同 test_worklog_drafts.py）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-summary-status-")
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

    def summarize(self, viewer_id: int, space_id: str, at: str) -> None:
        with mock.patch.object(repo, "_now", return_value=at):
            repo.create_summary(viewer_id, space_id, "測試群", "general", 10, "內容")


class TestLatestSummaryTimes(TempDbCase):
    def test_picks_latest_per_space(self):
        self.summarize(self.vid, "spaces/A", "2026-09-01T00:00:00+00:00")
        self.summarize(self.vid, "spaces/A", "2026-09-03T00:00:00+00:00")
        self.summarize(self.vid, "spaces/A", "2026-09-02T00:00:00+00:00")
        self.summarize(self.vid, "spaces/B", "2026-08-01T00:00:00+00:00")
        self.assertEqual(
            repo.latest_summary_times(self.vid),
            {"spaces/A": "2026-09-03T00:00:00+00:00", "spaces/B": "2026-08-01T00:00:00+00:00"},
        )

    def test_never_summarized_space_is_absent(self):
        self.summarize(self.vid, "spaces/A", "2026-09-01T00:00:00+00:00")
        self.assertNotIn("spaces/B", repo.latest_summary_times(self.vid))

    def test_other_viewers_summaries_do_not_count(self):
        """ADR-0002：別人產的、而且比較新的 Summary 不能蓋過（或憑空變出）我的。"""
        self.summarize(self.vid, "spaces/A", "2026-09-01T00:00:00+00:00")
        self.summarize(self.other, "spaces/A", "2026-09-30T00:00:00+00:00")
        self.summarize(self.other, "spaces/B", "2026-09-30T00:00:00+00:00")
        self.assertEqual(
            repo.latest_summary_times(self.vid), {"spaces/A": "2026-09-01T00:00:00+00:00"}
        )
        # 正對照：同一批資料換成另一位 Viewer 查，確實查得到他自己的
        self.assertEqual(
            repo.latest_summary_times(self.other),
            {"spaces/A": "2026-09-30T00:00:00+00:00", "spaces/B": "2026-09-30T00:00:00+00:00"},
        )

    def test_real_now_format_sorts_as_time(self):
        """MAX(created_at) 是字串比較；用真正的 `_now()` 寫入，確認它挑到的是最晚那筆。"""
        repo.create_summary(self.vid, "spaces/A", None, "general", 1, "舊")
        time.sleep(1.1)  # _now() 精度到秒
        repo.create_summary(self.vid, "spaces/A", None, "general", 1, "新")
        newest = repo.list_summaries(self.vid, limit=1)[0]["created_at"]
        self.assertEqual(repo.latest_summary_times(self.vid)["spaces/A"], newest)


SPACES = [
    {"id": "spaces/A", "displayName": "甲", "type": "SPACE", "lastActiveTime": "2026-09-02T00:00:00Z"},
    {"id": "spaces/B", "displayName": "乙", "type": "SPACE", "lastActiveTime": "2026-09-02T00:00:00Z"},
    {"id": "spaces/C", "displayName": "丙", "type": "SPACE", "lastActiveTime": None},
]


class TestSpacesEndpoint(TempDbCase):
    def call(self, viewer_id: int, **kw):
        entry = {"data": [dict(s) for s in SPACES], "timestamp": 1_790_000_000, "was_cached": True}
        with mock.patch.object(server, "list_spaces_cached", lambda vid, refresh=False: entry):
            params = {"search": None, "refresh": False, "viewer": {"id": viewer_id}}
            params.update(kw)
            return server.get_spaces(**params)

    def test_each_space_carries_last_summary_at_or_null(self):
        self.summarize(self.vid, "spaces/A", "2026-09-01T00:00:00+00:00")
        out = {s["id"]: s for s in self.call(self.vid)["spaces"]}
        self.assertEqual(out["spaces/A"]["lastSummaryAt"], "2026-09-01T00:00:00+00:00")
        # 沒產生過：欄位在、值是 null（省略欄位的話前端分不出「舊後端」與「從未產生」）
        self.assertIn("lastSummaryAt", out["spaces/B"])
        self.assertIsNone(out["spaces/B"]["lastSummaryAt"])
        self.assertIsNone(out["spaces/C"]["lastSummaryAt"])

    def test_other_viewer_summary_not_visible(self):
        self.summarize(self.other, "spaces/A", "2026-09-05T00:00:00+00:00")
        out = {s["id"]: s for s in self.call(self.vid)["spaces"]}
        self.assertIsNone(out["spaces/A"]["lastSummaryAt"])

    def test_summaries_are_queried_once_not_per_space(self):
        """N+1 守門：三個 Space，summaries 只查一次。"""
        for sid in ("spaces/A", "spaces/B"):
            self.summarize(self.vid, sid, "2026-09-01T00:00:00+00:00")
        summary_queries = []

        def counting(real):
            def wrapper(sql, params=()):
                if "summaries" in sql:
                    summary_queries.append(sql)
                return real(sql, params)
            return wrapper

        # 三個入口都數：逐筆查可能走 query_one 或 execute，只包 query_all 會漏看
        with mock.patch.object(db, "query_all", counting(db.query_all)), \
             mock.patch.object(db, "query_one", counting(db.query_one)), \
             mock.patch.object(db, "execute", counting(db.execute)):
            out = self.call(self.vid)
        self.assertEqual(len(out["spaces"]), 3)
        self.assertEqual(len(summary_queries), 1)

    def test_search_filter_still_applies(self):
        self.summarize(self.vid, "spaces/B", "2026-09-01T00:00:00+00:00")
        out = self.call(self.vid, search="乙")
        self.assertEqual([s["id"] for s in out["spaces"]], ["spaces/B"])
        self.assertEqual(out["spaces"][0]["lastSummaryAt"], "2026-09-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
