"""`GET /api/v1/messages` 的格式化邏輯（不打 API、不碰資料庫）。

**這裡守的是一個很容易再犯的錯**：這個端點原本寫 `if not text: continue`，
於是「只有一張截圖、沒有文字」的訊息整則消失。表現不是報錯，是「我要 20 則
怎麼只有 17 則」——而且看不出少的是哪幾則。同樣的坑 `format_conversation()`
2026-09-05 就踩過一次並修好了，這個端點漏掉。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import InvalidParameter  # noqa: E402
from dashboard.api import server  # noqa: E402

SPACE = "spaces/S"
VIEWER = {"id": 1, "google_user_id": "users/me"}

IMG = {
    "contentName": "shot.png",
    "contentType": "image/png",
    "attachmentDataRef": {"resourceName": "res/shot"},
}
DRIVE_IMG = {"contentName": "plan.png", "contentType": "image/png", "driveDataRef": {"id": "d1"}}


def msg(mid, text=None, att=None, thread="tA", created="2026-09-06T10:00:00.000Z"):
    return {
        "name": f"{SPACE}/messages/{mid}",
        "sender": {"name": "users/peer"},
        "text": text,
        "createTime": created,
        "thread": {"name": f"{SPACE}/threads/{thread}"},
        "attachment": att or [],
    }


class FakeClient:
    def __init__(self, recent=None, thread=None):
        self._recent = recent or []
        self._thread = thread or []
        self.calls = []

    def fetch_recent_messages(self, space_id, limit=50):
        self.calls.append(("recent", space_id, limit))
        return list(self._recent)

    def list_thread_messages(self, space_id, thread_name, limit=1000):
        self.calls.append(("thread", space_id, thread_name, limit))
        return list(self._thread)


class MessagesCase(unittest.TestCase):
    def call(self, client, **kw):
        with mock.patch.object(server, "get_client", lambda vid: client), \
             mock.patch.object(server, "learn_names", lambda msgs: None), \
             mock.patch.object(server, "learn_dm_peer", lambda v, s, m: None), \
             mock.patch.object(server, "name_resolver_for", lambda v: (lambda uid: "對方")), \
             mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"):
            params = {"space_id": SPACE, "limit": 20, "thread_name": None, "viewer": VIEWER}
            params.update(kw)
            return server.get_messages(**params)


class TestAttachmentOnlyMessages(MessagesCase):
    def test_image_only_message_is_kept(self):
        """只有圖沒有文字的訊息不可以被丟掉——那是工作群組最常見的形態之一。"""
        client = FakeClient(recent=[msg("1", "有文字"), msg("2", None, [IMG])])
        out = self.call(client)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["messages"][1]["text"], "")
        self.assertIn("shot.png", out["messages"][1]["attachment_note"])

    def test_whitespace_only_text_with_image_is_kept(self):
        client = FakeClient(recent=[msg("1", "   ", [IMG])])
        out = self.call(client)
        self.assertEqual(out["count"], 1)

    def test_drive_image_is_kept_and_says_it_cannot_be_read(self):
        """Drive 來源的圖下載不到（沒有那個 scope），但**不能靜默略過**。"""
        client = FakeClient(recent=[msg("1", None, [DRIVE_IMG])])
        out = self.call(client)
        self.assertEqual(out["count"], 1)
        self.assertIn("無權讀取", out["messages"][0]["attachment_note"])

    def test_truly_empty_message_is_still_dropped(self):
        """既沒文字也沒附件的（系統事件之類）沒有東西可顯示，照樣不要。"""
        client = FakeClient(recent=[msg("1", None), msg("2", "有文字")])
        out = self.call(client)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["messages"][0]["text"], "有文字")

    def test_plain_text_message_has_empty_attachment_note(self):
        client = FakeClient(recent=[msg("1", "只有文字")])
        self.assertEqual(self.call(client)["messages"][0]["attachment_note"], "")


class TestThreadInfo(MessagesCase):
    def test_every_message_carries_its_thread_name(self):
        """前端要靠這欄才分得出哪幾則是同一串——沒有它就沒有『包含討論串』可言。"""
        client = FakeClient(recent=[msg("1", "a", thread="tA"), msg("2", "b", thread="tB")])
        out = self.call(client)
        self.assertEqual(out["messages"][0]["thread_name"], f"{SPACE}/threads/tA")
        self.assertEqual(out["messages"][1]["thread_name"], f"{SPACE}/threads/tB")

    def test_thread_name_param_returns_the_whole_thread(self):
        client = FakeClient(
            recent=[msg("1", "a")],
            thread=[msg("1", "a"), msg("2", "b"), msg("3", "c")],
        )
        out = self.call(client, thread_name=f"{SPACE}/threads/tA")
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["thread_name"], f"{SPACE}/threads/tA")
        self.assertEqual(client.calls[0][0], "thread")

    def test_thread_from_another_space_is_rejected(self):
        """不驗的話可以用 A 群的 space_id 去讀 B 群的討論串。"""
        client = FakeClient()
        with self.assertRaises(InvalidParameter):
            self.call(client, thread_name="spaces/OTHER/threads/tA")

    def test_no_thread_name_uses_recent_messages(self):
        client = FakeClient(recent=[msg("1", "a")])
        self.call(client)
        self.assertEqual(client.calls[0][0], "recent")
        self.assertIsNone(self.call(client)["thread_name"])


class TestValidation(MessagesCase):
    def test_bad_space_id_is_rejected(self):
        with self.assertRaises(InvalidParameter):
            self.call(FakeClient(), space_id="AAAAxLxqJxY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
