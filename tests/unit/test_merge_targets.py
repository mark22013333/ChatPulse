"""`server.resolve_merge_targets()` 的單元測試（不打 API、不碰資料庫）。

**這是合併回覆的正確性邊界。** 三種擋下來的情況，放行的代價各不相同，
其中「跨討論串」最危險：回話會**送出成功**，但只進得了其中一串，
另一串的提問者永遠看不到——而系統會把兩則都標成已處理。
沒有任何錯誤訊息，使用者要等到對方再問一次才會知道。

前端也有一份同樣規則（`src/lib/merge.ts`）。那份是為了好用（不能勾的先變灰），
這份是為了正確——前端規則永遠可能被繞過，兩份都要有測試。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import InvalidParameter, MentionNotFound  # noqa: E402
from dashboard.api import server  # noqa: E402

DM = "spaces/DM"
GROUP = "spaces/G"


def row(mid, *, space=DM, thread="threads/a", state="pending", created="2026-09-06T10:00:00Z"):
    return {
        "id": mid,
        "space_id": space,
        "thread_name": f"{space}/{thread}",
        "state": state,
        "create_time": created,
        "message_name": f"{space}/messages/m{mid}",
        "sender_display": "對方",
    }


class MergeTargetsCase(unittest.TestCase):
    def resolve(self, primary, others, *, flat=True, shape=None):
        """跑 resolve_merge_targets，把 repo 與 space_shape 換成假的。"""
        table = {r["id"]: r for r in others}
        shape = shape or (("DIRECT_MESSAGE", "THREADED_MESSAGES") if flat else ("SPACE", "THREADED_MESSAGES"))
        with mock.patch.object(server.repo, "get_mention", lambda vid, mid: table.get(mid)), \
             mock.patch.object(server, "space_shape", lambda vid, sid: shape):
            return server.resolve_merge_targets(1, primary, [r["id"] for r in others])


class TestHappyPath(MergeTargetsCase):
    def test_empty_merge_ids_returns_only_primary(self):
        p = row(1)
        with mock.patch.object(server, "space_shape", lambda vid, sid: ("DIRECT_MESSAGE", None)):
            out = server.resolve_merge_targets(1, p, [])
        self.assertEqual([r["id"] for r in out], [1])

    def test_dm_merges_across_different_threads(self):
        """私訊裡每則訊息各自成一個 thread，用 thread 擋就等於永遠不能合併。"""
        p = row(1, thread="threads/a")
        out = self.resolve(p, [row(2, thread="threads/b")], flat=True)
        self.assertEqual([r["id"] for r in out], [1, 2])

    def test_result_is_sorted_by_time(self):
        """順序決定 prompt 裡的呈現順序，倒著讀會讓模型誤判因果。"""
        p = row(1, created="2026-09-06T12:00:00Z")
        out = self.resolve(p, [row(2, created="2026-09-06T09:00:00Z")], flat=True)
        self.assertEqual([r["id"] for r in out], [2, 1])

    def test_group_merges_within_the_same_thread(self):
        p = row(1, space=GROUP, thread="threads/a")
        out = self.resolve(p, [row(2, space=GROUP, thread="threads/a")], flat=False)
        self.assertEqual([r["id"] for r in out], [1, 2])

    def test_primary_id_in_merge_list_is_ignored(self):
        p = row(1)
        out = self.resolve(p, [row(1)], flat=True)
        self.assertEqual([r["id"] for r in out], [1])


class TestRejections(MergeTargetsCase):
    def test_other_space_is_rejected(self):
        p = row(1, space=DM)
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve(p, [row(2, space=GROUP)], flat=True)
        self.assertIn("同一個聊天室", str(cm.exception))

    def test_group_cross_thread_is_rejected(self):
        """放行的話回話只會進其中一串，另一串的人看不到，且兩則都被標已處理。"""
        p = row(1, space=GROUP, thread="threads/a")
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve(p, [row(2, space=GROUP, thread="threads/b")], flat=False)
        self.assertIn("討論串", str(cm.exception))

    def test_unthreaded_space_may_cross_threads(self):
        """不分串的群組（實測 27 個）語意同私訊，不該被 thread 規則擋住。"""
        p = row(1, space=GROUP, thread="threads/a")
        out = self.resolve(
            p,
            [row(2, space=GROUP, thread="threads/b")],
            shape=("SPACE", "UNTHREADED_MESSAGES"),
        )
        self.assertEqual([r["id"] for r in out], [1, 2])

    def test_already_resolved_is_rejected(self):
        p = row(1)
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve(p, [row(2, state="resolved")], flat=True)
        self.assertIn("已經處理過", str(cm.exception))

    def test_missing_mention_raises_not_found(self):
        p = row(1)
        with mock.patch.object(server.repo, "get_mention", lambda vid, mid: None), \
             mock.patch.object(server, "space_shape", lambda vid, sid: ("DIRECT_MESSAGE", None)):
            with self.assertRaises(MentionNotFound):
                server.resolve_merge_targets(1, p, [999])

    def test_too_many_is_rejected(self):
        p = row(1)
        extras = [row(i) for i in range(2, 2 + server.MERGE_MAX)]
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve(p, extras, flat=True)
        self.assertIn(str(server.MERGE_MAX), str(cm.exception))

    def test_exactly_at_the_limit_is_allowed(self):
        p = row(1)
        extras = [row(i) for i in range(2, 1 + server.MERGE_MAX)]
        out = self.resolve(p, extras, flat=True)
        self.assertEqual(len(out), server.MERGE_MAX)


if __name__ == "__main__":
    unittest.main(verbosity=2)
