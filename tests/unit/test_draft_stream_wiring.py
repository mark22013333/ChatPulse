"""`draft_stream` 有沒有把該傳的東西傳給 `draft_context.build()`。

**為什麼需要這一套。** 2026-09-07 做正對照時發現：把 server 那行
`self_user_id=viewer.get("google_user_id")` 改成 `self_user_id=None`，
整條「從我上次發言到現在，對方講了什麼我還沒回」的判準就等於沒開——
而**所有測試照樣全綠**。core 的測試全都直接呼叫 `dc.build()`，
沒有任何東西守著「server 到底有沒有把參數接上去」。

接線錯誤的表現是靜默的：草稿讀起來完全正常，只是又只回了一半。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import draft_context as dc  # noqa: E402
from dashboard.api import server  # noqa: E402

VIEWER = {"id": 1, "google_user_id": "users/me", "display_name": "我"}
SPACE = "spaces/S"
MENTION = {
    "id": 7,
    "space_id": SPACE,
    "space_name": "測試群",
    "message_name": f"{SPACE}/messages/m1",
    "thread_name": f"{SPACE}/threads/t1",
    "state": "pending",
    "create_time": "2026-09-06T12:00:00Z",
    "sender_display": "對方",
}
ANCHOR = {
    "name": MENTION["message_name"],
    "sender": {"name": "users/peer"},
    "text": "想問一下",
    "createTime": "2026-09-06T12:00:00Z",
    "thread": {"name": MENTION["thread_name"]},
    "attachment": [],
}


class FakeProvider:
    model = "fake"
    supports_vision = False

    def stream_text(self, prompt, operation=None, images=None):
        self.prompt = prompt
        yield "### ✍️ 建議回話\n好"


class FakeClient:
    def get_message(self, name):
        return dict(ANCHOR)

    def fetch_recent_messages(self, space_id, limit=50):
        return [dict(ANCHOR)]

    def list_thread_messages(self, space_id, thread_name, limit=1000):
        return [dict(ANCHOR)]

    def list_messages_since(self, space_id, since, page_size=100, *, max_messages=None):
        return []


def run_stream(req=None, captured=None):
    """跑一次 draft_stream，把 draft_context.build 的呼叫參數攔下來。"""
    calls = captured if captured is not None else []
    real_build = dc.build

    def spy(client, **kwargs):
        calls.append(kwargs)
        return real_build(client, **kwargs)

    provider = FakeProvider()
    request = req or server.DraftRequest()
    with mock.patch.object(server.repo, "get_mention", lambda vid, mid: dict(MENTION)), \
         mock.patch.object(server.repo, "create_draft", lambda mid, text: 1), \
         mock.patch.object(server, "get_client", lambda vid: FakeClient()), \
         mock.patch.object(server, "get_provider", lambda vid, p: provider), \
         mock.patch.object(server, "space_shape", lambda vid, sid: ("SPACE", "THREADED_MESSAGES")), \
         mock.patch.object(server, "learn_names", lambda msgs: None), \
         mock.patch.object(server, "name_resolver_for", lambda v: (lambda uid: "對方")), \
         mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"), \
         mock.patch.object(server.draft_context, "build", spy), \
         mock.patch.object(server.providers, "resolve_name", lambda p=None: "fake"):
        resp = server.draft_stream(7, request, VIEWER)
        # StreamingResponse 會把同步 generator 包成 async iterator，
        # 要跑完它才會真的呼叫到 draft_context.build
        events = asyncio.run(_drain(resp.body_iterator))
    return calls, events, provider


async def _drain(it):
    return [chunk async for chunk in it]


class TestSelfUserIdIsWired(unittest.TestCase):
    def test_build_receives_the_viewers_own_user_id(self):
        """沒接上去的話，「我還沒回」判不出來，草稿又會只回一半。"""
        calls, _, _ = run_stream()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["self_user_id"], VIEWER["google_user_id"])

    def test_build_receives_space_shape(self):
        """脈絡形狀（私訊 vs 討論串）靠這兩個欄位決定。"""
        calls, _, _ = run_stream()
        self.assertEqual(calls[0]["space_type"], "SPACE")
        self.assertEqual(calls[0]["threading_state"], "THREADED_MESSAGES")

    def test_build_receives_the_thread_name(self):
        calls, _, _ = run_stream()
        self.assertEqual(calls[0]["thread_name"], MENTION["thread_name"])

    def test_build_is_called_once_per_draft(self):
        """呼叫兩次代表多打了一輪 Google API（曾經為了學名字這樣做過）。"""
        calls, _, _ = run_stream()
        self.assertEqual(len(calls), 1)

    def test_meta_event_carries_the_context_shape(self):
        import json  # noqa: PLC0415

        _, events, _ = run_stream()
        meta = json.loads(events[0].split("data: ", 1)[1])
        self.assertEqual(meta["type"], "meta")
        self.assertIn("context", meta)
        self.assertIn("answering", meta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
