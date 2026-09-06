"""core/draft_context.py 的單元測試（不打任何 API）。

**為什麼這一套要先寫。** `tests/` 底下原本只有 `e2e/`，而且沒有任何檔案引用
`draft_reply_prompt` 或 `list_thread_messages`——也就是說改 prompt 簽章不會弄壞
既有測試，但**也沒有任何東西會攔住你改錯**。這種靜默錯誤（脈絡撈錯、圖片母體
被放大、群組路徑被順手改掉）在 e2e 上表現為「草稿讀起來怪怪的」，查不出來。

執行：
    .venv/bin/python -m unittest discover -s tests/unit -v
或
    .venv/bin/python tests/unit/test_draft_context.py
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import draft_context as dc  # noqa: E402
from core.chat_client import format_conversation  # noqa: E402

BASE = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
ME = "users/me"
PEER = "users/peer"
OTHER = "users/other"

IMG_ATT = [
    {
        "contentName": "shot.png",
        "contentType": "image/png",
        "attachmentDataRef": {"resourceName": "res/shot"},
    }
]


def msg(idx, *, sender=PEER, text=None, minutes=0, thread="t-default", att=None):
    """造一則長得像 Google Chat Message 的字典。

    `minutes` 是相對 BASE 的分鐘偏移，負數代表更早。
    """
    return {
        "name": f"spaces/S/messages/{idx}",
        "sender": {"name": sender},
        "text": text if text is not None else f"訊息{idx}",
        "createTime": (BASE + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "thread": {"name": f"spaces/S/threads/{thread}"},
        "attachment": att or [],
    }


class FakeClient:
    """只實作 draft_context 需要的三個方法，並記錄呼叫次數。

    記錄呼叫是刻意的：設計文件對「私訊不該再呼叫 list_thread_messages」
    有明確主張（那次往返必然只回 1 則，純浪費），沒有斷言就守不住。
    """

    def __init__(self, recent=None, thread=None, since=None):
        self._recent = list(recent or [])
        self._thread = list(thread or [])
        self._since = list(since or [])
        self.calls = []

    def fetch_recent_messages(self, space_id, limit=50):
        self.calls.append(("fetch_recent_messages", space_id, limit))
        return list(self._recent[-limit:])

    def list_thread_messages(self, space_id, thread_name, limit=1000):
        self.calls.append(("list_thread_messages", space_id, thread_name, limit))
        return list(self._thread[:limit])

    def list_messages_since(self, space_id, since, page_size=100, *, max_messages=None):
        self.calls.append(("list_messages_since", space_id, since, max_messages))
        out = [m for m in self._since if m["createTime"] > since.isoformat().replace("+00:00", "Z")]
        return out[:max_messages] if max_messages else out

    def kinds(self):
        return [c[0] for c in self.calls]


def resolve(uid):
    return {ME: "我", PEER: "對方", OTHER: "路人"}.get(uid, "未知成員")


def names(messages):
    return [m["name"] for m in messages]


# ===========================================================================
# 判準本身
# ===========================================================================


class TestFlatCriterion(unittest.TestCase):
    """判準必須用 Space 的結構語意，而且是兩個訊號的聯集。

    2026-09-06 實測本帳號 436 個 Space：私訊回報的 spaceThreadingState 是
    THREADED_MESSAGES（不是官方文件寫的 UNTHREADED_MESSAGES）。只用
    threadingState 判斷的話，私訊一則都修不到——那正是這次要修的 bug。
    """

    def test_direct_message_is_flat_even_when_threading_state_says_threaded(self):
        self.assertTrue(dc.is_flat_space("DIRECT_MESSAGE", "THREADED_MESSAGES"))

    def test_unthreaded_space_is_flat(self):
        self.assertTrue(dc.is_flat_space("SPACE", "UNTHREADED_MESSAGES"))

    def test_threaded_space_is_not_flat(self):
        self.assertFalse(dc.is_flat_space("SPACE", "THREADED_MESSAGES"))

    def test_group_chat_is_not_flat(self):
        # 抽樣實測 GROUP_CHAT 有真的討論串（30 則 / 22 thread，最長一串 8 則），
        # 沒有可靠結構訊號時走討論串路徑——把別串當同一段對話比少給脈絡貴。
        self.assertFalse(dc.is_flat_space("GROUP_CHAT", "THREADED_MESSAGES"))

    def test_missing_fields_default_to_threaded(self):
        self.assertFalse(dc.is_flat_space(None, None))

    def test_labels(self):
        self.assertEqual(dc.space_type_label("DIRECT_MESSAGE", "THREADED_MESSAGES"), "一對一私訊")
        self.assertEqual(dc.space_type_label("SPACE", "UNTHREADED_MESSAGES"), "不分討論串的聊天室")
        self.assertEqual(dc.space_type_label("GROUP_CHAT", "THREADED_MESSAGES"), "多人群組")


# ===========================================================================
# 私訊／不分串群組：錨點前後窗
# ===========================================================================


class TestFlatWindow(unittest.TestCase):
    """私訊：每則訊息各自成一個 thread，扁平序列本身才是對話。"""

    def _dm_history(self, n=40):
        # 每則各自一個 thread（私訊的實際形態），每則間隔 1 分鐘
        return [
            msg(i, sender=PEER if i % 2 else ME, minutes=-(n - i), thread=f"t{i}")
            for i in range(n)
        ]

    def test_anchor_in_middle_gets_before_and_after(self):
        history = self._dm_history(40)
        anchor = history[25]
        client = FakeClient(recent=history)
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name=anchor["thread"]["name"],
            resolve=resolve,
        )
        self.assertEqual(ctx.mode, "flat_window")
        self.assertEqual(ctx.coverage, "full")
        self.assertEqual(len(ctx.blocks), 1)
        block = ctx.blocks[0]
        self.assertEqual(block.kind, "flat_window")
        # 前 15 後 10 ＋ 錨點本身
        self.assertEqual(block.count, cfg.DRAFT_CTX_BEFORE + 1 + cfg.DRAFT_CTX_AFTER)
        self.assertIn(anchor["name"], names(block.messages))
        # 錨點之後的訊息確實有進來——「最近 N 則」框架給不了這個
        self.assertIn(history[26]["name"], names(block.messages))

    def test_does_not_call_list_thread_messages(self):
        """私訊的 thread 恆為 1 則，那次 API 往返純粹是浪費。"""
        history = self._dm_history(40)
        client = FakeClient(recent=history, thread=[history[25]])
        dc.build(
            client,
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=history[25],
            thread_name=history[25]["thread"]["name"],
            resolve=resolve,
        )
        self.assertNotIn("list_thread_messages", client.kinds())
        self.assertEqual(client.kinds().count("fetch_recent_messages"), 1)

    def test_anchor_marked_in_text(self):
        history = self._dm_history(40)
        anchor = history[25]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        lines = ctx.blocks[0].text.split("\n")
        marked = [ln for ln in lines if ln.startswith(dc.ANCHOR_MARK)]
        self.assertEqual(len(marked), 1)
        self.assertIn("訊息25", marked[0])

    def test_time_bound_cuts_old_chatter_but_keeps_floor(self):
        """冷清私訊：48h 之外的訊息會被切掉，但保底 6 則不能歸零。

        只用時間窗的話冷清的私訊會一則都不剩，等於沒修。
        """
        old = [msg(i, minutes=-(60 * 24 * 30) - i, thread=f"t{i}") for i in range(20)]
        old.sort(key=lambda m: m["createTime"])
        anchor = msg(99, minutes=0, thread="t99")
        history = old + [anchor]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        before_count = ctx.blocks[0].count - 1  # 扣掉錨點，沒有 after
        self.assertEqual(before_count, cfg.DRAFT_CTX_MIN_BEFORE)

    def test_time_bound_applies_when_history_is_busy(self):
        """熱絡對話：48h 內就超過 15 則，時間上界不該砍掉任何一則。"""
        history = [msg(i, minutes=-(40 - i) * 10, thread=f"t{i}") for i in range(40)]
        anchor = history[30]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        # 10 分鐘一則，前 15 則 = 150 分鐘，遠在 48h 內
        self.assertEqual(ctx.blocks[0].count, cfg.DRAFT_CTX_BEFORE + 1 + 9)

    def test_anchor_run_collapses_consecutive_messages_from_same_sender(self):
        """一個問題拆三則發時，錨點應該是整串連發，不是最後那句客套話。"""
        history = [
            msg(0, sender=ME, minutes=-60, thread="t0"),
            msg(1, sender=PEER, text="你好", minutes=-3, thread="t1"),
            msg(2, sender=PEER, text="想問一下匯出報表為什麼會 500", minutes=-2, thread="t2"),
            msg(3, sender=PEER, text="方便的話今天回我", minutes=-1, thread="t3"),
        ]
        anchor = history[3]  # create_draft_target 挑的是「最後一則不是自己發的」
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        self.assertEqual(names(ctx.anchor_run), [history[1]["name"], history[2]["name"], anchor["name"]])
        # 真正的問題有進到【被 @ 的訊息】區塊
        self.assertIn("匯出報表為什麼會 500", ctx.anchor_text)
        self.assertIn("匯出報表為什麼會 500", ctx.anchor_plain_text)
        # anchor_plain_text 不帶發話者與時間戳（那些會污染 code_search 抽詞）
        self.assertNotIn("對方:", ctx.anchor_plain_text)

    def test_anchor_run_stops_at_different_sender(self):
        history = [
            msg(1, sender=PEER, minutes=-3, thread="t1"),
            msg(2, sender=ME, minutes=-2, thread="t2"),
            msg(3, sender=PEER, minutes=-1, thread="t3"),
        ]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=history[2],
            resolve=resolve,
        )
        self.assertEqual(names(ctx.anchor_run), [history[2]["name"]])

    def test_anchor_run_stops_at_long_gap(self):
        history = [
            msg(1, sender=PEER, minutes=-120, thread="t1"),
            msg(2, sender=PEER, minutes=-1, thread="t2"),
        ]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=history[1],
            resolve=resolve,
        )
        self.assertEqual(names(ctx.anchor_run), [history[1]["name"]])

    def test_anchor_older_than_window_falls_back_to_time_anchor(self):
        """收件匣積壓：錨點比最近 60 則還舊。"""
        recent = [msg(1000 + i, minutes=i, thread=f"r{i}") for i in range(60)]
        anchor = msg(1, minutes=-600, thread="t1")
        neighbours = [msg(i, minutes=-600 + (i - 1) * 2, thread=f"t{i}") for i in range(1, 12)]
        client = FakeClient(recent=recent, since=neighbours)
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        self.assertIn("list_messages_since", client.kinds())
        self.assertEqual(ctx.coverage, "full")
        self.assertIn(anchor["name"], names(ctx.blocks[0].messages))

    def test_anchor_unreachable_marks_partial(self):
        """連時間錨定都撈不到錨點時要誠實標 partial，不能假裝脈絡完整。"""
        recent = [msg(1000 + i, minutes=i, thread=f"r{i}") for i in range(60)]
        anchor = msg(1, minutes=-6000, thread="t1")
        client = FakeClient(recent=recent, since=[])
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        self.assertEqual(ctx.coverage, "partial")
        self.assertIn(anchor["name"], names(ctx.blocks[0].messages))


# ===========================================================================
# 群組長串：這條路徑是最常走的，任何改動都是純風險
# ===========================================================================


class TestThreadUnchanged(unittest.TestCase):
    def _thread(self, n=6):
        return [msg(i, sender=PEER if i % 2 else OTHER, minutes=-(n - i), thread="tA") for i in range(n)]

    def test_mode_and_messages_identical_to_before(self):
        thread = self._thread(6)
        anchor = thread[-1]
        client = FakeClient(thread=thread)
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertEqual(ctx.mode, "thread")
        self.assertEqual(len(ctx.blocks), 1)
        # 撈回來的訊息與改動前完全一致
        self.assertEqual(names(ctx.blocks[0].messages), names(thread))
        # 沒有多打任何一次 API
        self.assertEqual(client.kinds(), ["list_thread_messages"])
        # 文字內容也與改動前一致（差別只有錨點那行多了 ▶ 標記）
        stripped = ctx.blocks[0].text.replace(dc.ANCHOR_MARK, "")
        self.assertEqual(stripped, format_conversation(thread, resolve))

    def test_thread_limit_is_no_longer_limit_max(self):
        """C-8.1：原本傳 LIMIT_MAX=1000，一個超長討論串會把 1000 則灌進 prompt。"""
        thread = self._thread(6)
        client = FakeClient(thread=thread)
        dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=thread[-1],
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertEqual(client.calls[0][3], cfg.DRAFT_THREAD_LIMIT)
        self.assertLess(cfg.DRAFT_THREAD_LIMIT, cfg.LIMIT_MAX)

    def test_image_messages_equal_thread_messages(self):
        thread = self._thread(6)
        ctx = dc.build(
            FakeClient(thread=thread),
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=thread[-1],
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertEqual(names(ctx.image_messages), names(thread))


# ===========================================================================
# 群組薄串：獨立區塊 + 警語，不能跟私訊走同一條路徑
# ===========================================================================


class TestThreadThin(unittest.TestCase):
    def _setup(self, cross_enabled=True):
        anchor = msg(50, sender=OTHER, text="@我 這個怎麼辦", minutes=0, thread="tA")
        others = [msg(i, sender=PEER, minutes=-(30 - i), thread=f"tB{i}") for i in range(30)]
        client = FakeClient(recent=others + [anchor], thread=[anchor])
        return anchor, client

    def test_cross_thread_block_is_separate_and_warned(self):
        anchor, client = self._setup()
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertEqual(ctx.mode, "thread_thin")
        kinds = [b.kind for b in ctx.blocks]
        self.assertEqual(kinds, ["thread", "cross_thread"])
        cross = ctx.blocks[1]
        self.assertEqual(cross.count, cfg.DRAFT_CROSS_BEFORE)
        # 警語必須在區塊「之前」，而且要講清楚這些不屬於本串
        self.assertIn("不屬於", cross.note)
        self.assertIn("不可", cross.note)

    def test_cross_thread_excluded_from_images(self):
        """C-5：放大文字不能靜默放大圖片母體。"""
        anchor = msg(50, sender=OTHER, minutes=0, thread="tA", att=IMG_ATT)
        others = [msg(i, sender=PEER, minutes=-(30 - i), thread=f"tB{i}", att=IMG_ATT) for i in range(30)]
        client = FakeClient(recent=others + [anchor], thread=[anchor])
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertEqual(names(ctx.image_messages), [anchor["name"]])
        cross_names = set(names(ctx.blocks[1].messages))
        self.assertFalse(cross_names & set(names(ctx.image_messages)))

    def test_cross_thread_excluded_from_search_text(self):
        anchor, client = self._setup()
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        self.assertNotIn("訊息29", ctx.search_text)
        self.assertIn("這個怎麼辦", ctx.search_text)

    def test_cross_thread_can_be_switched_off(self):
        anchor, client = self._setup()
        original = cfg.DRAFT_CROSS_THREAD_ENABLED
        cfg.DRAFT_CROSS_THREAD_ENABLED = False
        try:
            ctx = dc.build(
                client,
                space_id="spaces/S",
                space_type="SPACE",
                threading_state="THREADED_MESSAGES",
                anchor_msg=anchor,
                thread_name="spaces/S/threads/tA",
                resolve=resolve,
            )
        finally:
            cfg.DRAFT_CROSS_THREAD_ENABLED = original
        self.assertEqual([b.kind for b in ctx.blocks], ["thread"])
        self.assertNotIn("fetch_recent_messages", client.kinds())

    def test_cross_thread_only_looks_backwards(self):
        anchor = msg(50, sender=OTHER, minutes=0, thread="tA")
        before = [msg(i, sender=PEER, minutes=-(10 - i), thread=f"tB{i}") for i in range(10)]
        after = [msg(100 + i, sender=PEER, minutes=i + 1, thread=f"tC{i}") for i in range(5)]
        client = FakeClient(recent=before + [anchor] + after, thread=[anchor])
        ctx = dc.build(
            client,
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
        )
        cross_names = set(names(ctx.blocks[1].messages))
        self.assertFalse(cross_names & set(names(after)))


# ===========================================================================
# 不分串群組（27 個）：語意與私訊相同
# ===========================================================================


class TestUnthreadedSpace(unittest.TestCase):
    def test_gets_flat_window_not_cross_thread_warning(self):
        """T-2：這種群組的周圍訊息**就是**同一段對話，不該被警語壓低可信度。"""
        history = [msg(i, sender=PEER if i % 2 else OTHER, minutes=-(20 - i), thread=f"t{i}") for i in range(20)]
        anchor = history[15]
        ctx = dc.build(
            FakeClient(recent=history, thread=[anchor]),
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="UNTHREADED_MESSAGES",
            anchor_msg=anchor,
            thread_name=anchor["thread"]["name"],
            resolve=resolve,
        )
        self.assertEqual(ctx.mode, "flat_window")
        self.assertEqual([b.kind for b in ctx.blocks], ["flat_window"])
        self.assertNotIn("不可", ctx.blocks[0].note)
        self.assertEqual(ctx.space_type_label, "不分討論串的聊天室")


# ===========================================================================
# 圖片母體（C-5 的核心）
# ===========================================================================


class TestImageMessages(unittest.TestCase):
    def test_flat_window_excludes_after_and_caps_before(self):
        history = [msg(i, sender=PEER, minutes=-(40 - i), thread=f"t{i}", att=IMG_ATT) for i in range(40)]
        anchor = history[25]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=anchor,
            resolve=resolve,
        )
        img_names = set(names(ctx.image_messages))
        after_names = {m["name"] for m in history[26:36]}
        self.assertFalse(img_names & after_names, "錨點之後的圖是別人回答時貼的，不該送")
        # anchor_run（同一發話者連發，這裡整段都是 PEER 所以會收攏）+ 最多 6 則 before
        before_in_images = [n for n in img_names if n not in set(names(ctx.anchor_run))]
        self.assertLessEqual(len(before_in_images), cfg.DRAFT_IMAGE_BEFORE)

    def test_image_messages_sorted_oldest_first(self):
        """attachments.find_candidates 用清單索引算「越新越優先」，順序不能亂。"""
        history = [msg(i, sender=PEER, minutes=-(40 - i), thread=f"t{i}", att=IMG_ATT) for i in range(40)]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=history[25],
            resolve=resolve,
        )
        stamps = [m["createTime"] for m in ctx.image_messages]
        self.assertEqual(stamps, sorted(stamps))


# ===========================================================================
# meta：使用者判斷這份草稿可不可信的唯一依據
# ===========================================================================


class TestMeta(unittest.TestCase):
    def test_meta_shape(self):
        history = [msg(i, sender=PEER, minutes=-(40 - i), thread=f"t{i}") for i in range(40)]
        ctx = dc.build(
            FakeClient(recent=history),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state=None,
            anchor_msg=history[25],
            resolve=resolve,
        )
        meta = ctx.to_meta()
        self.assertEqual(meta["mode"], "flat_window")
        self.assertEqual(meta["coverage"], "full")
        self.assertEqual(meta["message_count"], ctx.blocks[0].count)
        self.assertTrue(meta["time_range"]["start"])
        self.assertTrue(meta["time_range"]["end"])
        self.assertLessEqual(meta["time_range"]["start"], meta["time_range"]["end"])
        self.assertEqual(meta["blocks"][0]["kind"], "flat_window")

    def test_message_count_is_bigger_than_one_for_dm(self):
        """整個修復的核心宣稱：私訊草稿的脈絡不再是 1 則。"""
        history = [msg(i, sender=PEER if i % 2 else ME, minutes=-(20 - i), thread=f"t{i}") for i in range(20)]
        ctx = dc.build(
            FakeClient(recent=history, thread=[history[15]]),
            space_id="spaces/S",
            space_type="DIRECT_MESSAGE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=history[15],
            thread_name=history[15]["thread"]["name"],
            resolve=resolve,
        )
        self.assertGreater(ctx.message_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
