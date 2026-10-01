"""`GET /api/v1/mentions` 的清單與計數必須來自同一個快照。

**這裡守的是一個 2026-10-01 實際發生過的錯**：端點原本先 `list_mentions`、
接著逐則打 Chat API 補內容（最多 60 則、8 個並行，要好幾秒），最後才
`count_mentions`。背景採集器每 45 秒一輪，剛好在這段時間 INSERT 一則新的
pending Mention，於是同一個回應裡清單是 90 則已處理、counts 卻是
pending 1／resolved 90。前端的表現是：徽章與 tab 說有 1 則待處理，清單卻說
「沒有待處理的 Mention」，還跳出假的「只載入最新 90 筆，共 91 筆」截斷提示
——畫面說有，Viewer 卻看不到，正是本專案最貴的「靜默少給資料」。

競態用「補內容那一步裡，從另一條執行緒（另一條連線，跟真的採集器一樣）
插入一則 pending」來重現。不能在同一條執行緒插：那會走同一條 thread-local
連線，模擬不出跨連線的快照差異。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`；也不打任何 Google API。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_mentions_snapshot*.py'
"""

import logging
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from dashboard.api import server  # noqa: E402

SPACE = "spaces/S"


def mention(n: int) -> dict:
    return {
        "space_id": SPACE,
        "space_name": "測試群",
        "message_name": f"{SPACE}/messages/m{n}",
        "thread_name": f"{SPACE}/threads/t{n}",
        "sender_name": "users/peer",
        "sender_display": "對方",
        "create_time": f"2026-09-30T10:00:{n:02d}.000Z",
    }


def insert_from_other_thread(viewer_id: int, n: int) -> None:
    """模擬採集器：在另一條執行緒（因此是另一條 SQLite 連線）寫入新 Mention。"""

    def work():
        try:
            repo.upsert_mention(viewer_id, mention(n))
        finally:
            db.close_connection()

    t = threading.Thread(target=work)
    t.start()
    t.join()


class RacingClient:
    """`get_message` 第一次被呼叫時，讓採集器插進一則新的 pending。"""

    def __init__(self, viewer_id: int):
        self.viewer_id = viewer_id
        self._lock = threading.Lock()
        self._fired = False

    def get_message(self, message_name):
        with self._lock:
            if not self._fired:
                self._fired = True
                insert_from_other_thread(self.viewer_id, 99)
        return {"name": message_name, "text": "內容"}


class TempDbCase(unittest.TestCase):
    """每個測試一個全新的暫存 SQLite 檔（做法同 test_summary_status.py）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-mentions-snapshot-")
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
        self.vid = int(repo.upsert_viewer("users/000001", "v1@example.com", "Viewer 1")["id"])
        self.viewer = {"id": self.vid, "google_user_id": "users/000001"}

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    def seed_resolved(self, count: int) -> None:
        for n in range(count):
            repo.upsert_mention(self.vid, mention(n))
            row = repo.list_mentions(self.vid, limit=1)[0]
            repo.set_mention_state(self.vid, row["id"], "resolved")

    def call(self, client, **kw):
        with mock.patch.object(server, "get_client", lambda vid: client), \
             mock.patch.object(server, "learn_names", lambda msgs: None), \
             mock.patch.object(server, "name_resolver_for", lambda v: (lambda uid: "對方")), \
             mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"):
            params = {
                "state": None,
                "limit": 200,
                "with_content": True,
                "viewer": self.viewer,
            }
            params.update(kw)
            return server.get_mentions(**params)


class TestListAndCountsShareOneSnapshot(TempDbCase):
    def assert_consistent(self, out) -> None:
        states = [m["state"] for m in out["mentions"]]
        listed = {
            "pending": sum(s in ("pending", repo.MANUAL_STATE) for s in states),
            "resolved": states.count("resolved"),
        }
        # limit 遠大於筆數，所以清單沒有被截斷：兩邊必須逐狀態相等
        self.assertEqual(out["counts"], listed)

    def test_collector_insert_during_hydrate_does_not_split_list_and_counts(self):
        """補內容途中採集器寫入新 pending：回應的 counts 與清單必須一致。"""
        self.seed_resolved(3)
        out = self.call(RacingClient(self.vid))
        self.assert_consistent(out)
        # 那則新的確實寫進去了（證明競態真的有發生，不是場景造得太乾淨）
        self.assertEqual(repo.count_mentions(self.vid), {"pending": 1, "resolved": 3})

    def test_pending_tab_also_consistent(self):
        """pending tab：清單列出幾則，counts.pending 就必須是幾則。

        要先有一則 pending——清單是空的時候端點不會去補內容，競態根本不會發生，
        測試會在修正前就綠，等於沒測（2026-10-01 第一版就是這樣假綠的）。
        """
        self.seed_resolved(2)
        repo.upsert_mention(self.vid, mention(50))
        out = self.call(RacingClient(self.vid), state="pending")
        self.assertEqual(out["count"], len(out["mentions"]))
        self.assertEqual(out["counts"]["pending"], len(out["mentions"]))

    def test_hydrate_still_fills_content(self):
        """修正不可以改變補內容的行為。"""
        self.seed_resolved(2)
        out = self.call(RacingClient(self.vid))
        self.assertTrue(all(m["text"] == "內容" for m in out["mentions"]))
        self.assertTrue(all(m["content_error"] is None for m in out["mentions"]))

    def test_snapshot_read_leaves_no_open_transaction(self):
        """讀完必須把交易關掉——留著不關，WAL 會被這條連線的讀取快照卡住，
        而且下一個 db.execute 的 `with conn` 會把別人的變更混進同一筆交易。"""
        self.seed_resolved(1)
        self.call(RacingClient(self.vid))
        self.assertFalse(db.get_connection().in_transaction)


if __name__ == "__main__":
    unittest.main()
