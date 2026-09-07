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


#: 偏好的替身。`draft_stream` 會讀偏好來解析回覆設定（ADR-0007），
#: 不 patch 的話單元測試會連到真的資料庫——那既違反「tests/unit 不碰 DB」
#: 的紀律，也會讓測試結果隨開發者自己的偏好設定而變。
#: 全部給「沒有偏好」，這樣這一套測試驗的仍然是原本的行為。
PREFERENCES = {
    "pinned_space_ids": [],
    "default_limit": 50,
    "default_style": "general",
    "default_provider": None,
    "default_code_project_id": None,
    "default_code_environment": None,
    "default_reply_tone": None,
    "default_persona_id": None,
    "default_reply_prompt_id": None,
    "default_sepia_enabled": None,
    "updated_at": "2026-09-07T00:00:00+00:00",
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
         mock.patch.object(server.repo, "create_draft", lambda mid, text, config=None: 1), \
         mock.patch.object(server.repo, "get_preferences", lambda vid: dict(PREFERENCES)), \
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


class TestStreamActuallySucceeds(unittest.TestCase):
    """這一串必須跑到 done，不可以在中途變成 error 事件。

    **為什麼這一套要存在。** 2026-09-07 為 Draft Reply 加回覆設定時，
    `repo.create_draft` 多了第三個參數，而這個檔案的替身還是
    `lambda mid, text`。於是每一次串流都在存檔那一步 TypeError、
    被 `except Exception` 接住、變成 `INTERNAL_ERROR` 事件——
    **而上面五個測試全部照樣綠燈**，因為它們只檢查
    `draft_context.build` 收到什麼，沒有人檢查這串到底有沒有成功。

    這條測試守的就是那個缺口：任何讓串流中途失敗的改動，
    在這裡會直接紅燈，而不是等到有人手動打開畫面才發現。
    """

    def _events(self):
        import json  # noqa: PLC0415

        _, events, _ = run_stream()
        return [json.loads(e.split("data: ", 1)[1]) for e in events]

    def test_no_error_event_is_emitted(self):
        errors = [e for e in self._events() if e["type"] == "error"]
        self.assertEqual(
            errors, [], f"串流中途失敗了：{errors[0]['message'] if errors else ''}"
        )

    def test_stream_reaches_done_with_a_draft_id(self):
        events = self._events()
        self.assertEqual(events[-1]["type"], "done", f"最後一個事件是 {events[-1]}")
        self.assertEqual(events[-1]["draft_id"], 1)

    def test_meta_reports_the_reply_settings_it_applied(self):
        """meta 要說出「系統以為你選了什麼」，理由同 code_refs。"""
        meta = self._events()[0]
        self.assertIn("reply", meta)
        # 沒有任何偏好、request 也沒帶 → 只有 sepia 一個鍵，且是關閉
        self.assertEqual(meta["reply"], {"sepia": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
