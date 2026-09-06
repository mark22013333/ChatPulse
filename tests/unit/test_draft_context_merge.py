"""多錨點合併回覆的單元測試（`draft_context.build(extra_anchor_msgs=...)`）。

**要守什麼**：同一個人在同一個對話裡連問兩件事，分兩次產草稿會得到兩份各自
正確、但要分兩次送出的回話。合併之後模型必須「看得到兩則、都標成要回的、
用一則回話回完」——其中任何一項掉了都是靜默失敗：草稿讀起來完全正常，
使用者要逐則比對才會發現有一題沒被回到。

單錨點的行為必須**完全不變**：那是絕大多數的路徑（見 test_draft_context.py）。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import draft_context as dc  # noqa: E402
from core import prompts  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_draft_context import (  # noqa: E402
    IMG_ATT,
    ME,
    OTHER,
    PEER,
    FakeClient,
    msg,
    names,
    resolve,
)


def build(client, anchor, extra=(), **kw):
    params = dict(
        space_id="spaces/S",
        space_type="DIRECT_MESSAGE",
        threading_state="THREADED_MESSAGES",
        anchor_msg=anchor,
        extra_anchor_msgs=list(extra),
        resolve=resolve,
    )
    params.update(kw)
    return dc.build(client, **params)


class TestFlatMerge(unittest.TestCase):
    """私訊：兩則相隔一小時的提問，中間夾著自己的回覆。"""

    def _history(self):
        return [
            msg(0, sender=PEER, text="背景訊息", minutes=-300, thread="t0"),
            msg(1, sender=ME, text="我知道了", minutes=-290, thread="t1"),
            msg(2, sender=PEER, text="白名單那份資料想請你確認", minutes=-120, thread="t2"),
            msg(3, sender=ME, text="好我看一下", minutes=-100, thread="t3"),
            msg(4, sender=PEER, text="另外 line 推播我剛測還是沒收到", minutes=-60, thread="t4"),
            msg(5, sender=PEER, text="謝謝", minutes=-30, thread="t5"),
        ]

    def test_both_anchors_are_marked(self):
        h = self._history()
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertEqual(ctx.anchor_count, 2)
        self.assertEqual(names(ctx.anchor_run), [h[2]["name"], h[4]["name"]])
        marked = [ln for ln in ctx.blocks[0].text.split("\n") if ln.startswith(dc.ANCHOR_MARK)]
        self.assertEqual(len(marked), 2)
        self.assertIn("白名單", marked[0])
        self.assertIn("line 推播", marked[1])

    def test_messages_between_anchors_are_kept(self):
        """兩個錨點之間夾著的自己的回覆，是這兩題之間的來龍去脈，不能被切掉。"""
        h = self._history()
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertIn(h[3]["name"], names(ctx.blocks[0].messages))

    def test_anchor_order_does_not_matter(self):
        """使用者在收件匣先勾新的再勾舊的，結果要一樣（內部按時間排序）。"""
        h = self._history()
        a = build(FakeClient(recent=h), h[2], [h[4]])
        b = build(FakeClient(recent=h), h[4], [h[2]])
        self.assertEqual(names(a.anchor_run), names(b.anchor_run))
        self.assertEqual(names(a.blocks[0].messages), names(b.blocks[0].messages))

    def test_after_window_starts_from_the_latest_anchor(self):
        """after 是用來偵測「已經有人回答了」，要從最後一個錨點往後看。"""
        h = self._history()
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertIn(h[5]["name"], names(ctx.blocks[0].messages))

    def test_anchor_text_lists_every_anchor(self):
        h = self._history()
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertIn("白名單", ctx.anchor_text)
        self.assertIn("line 推播", ctx.anchor_text)
        self.assertIn("白名單", ctx.anchor_plain_text)
        self.assertIn("line 推播", ctx.anchor_plain_text)

    def test_images_cover_both_anchors(self):
        """兩則各自帶圖時，兩張都要進圖片母體——只送一張就等於漏看一題。"""
        h = self._history()
        h[2] = msg(2, sender=PEER, text="白名單", minutes=-120, thread="t2", att=IMG_ATT)
        h[4] = msg(4, sender=PEER, text="line 推播", minutes=-60, thread="t4", att=IMG_ATT)
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        img_names = set(names(ctx.image_messages))
        self.assertIn(h[2]["name"], img_names)
        self.assertIn(h[4]["name"], img_names)

    def test_images_between_anchors_are_kept(self):
        """夾在兩個錨點之間的圖也要送。

        典型形態：對方問 Q1 → 我貼一張截圖回 → 對方接著問 Q2。那張截圖是這兩題
        之間的關鍵，但它**不是錨點**，只在 core 裡。
        （這條是補的：原本的 test_images_cover_both_anchors 兩個錨點自己都帶圖，
        所以 image_messages 用 core 或用 anchor_run 都會通過——測不出差別。）
        """
        h = self._history()
        h[3] = msg(3, sender=ME, text="好我看一下", minutes=-100, thread="t3", att=IMG_ATT)
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertNotIn(h[3]["name"], names(ctx.anchor_run))
        self.assertIn(h[3]["name"], names(ctx.image_messages))

    def test_images_still_exclude_after(self):
        """C-5 的界線在合併之後仍然要守住。"""
        h = self._history()
        h[5] = msg(5, sender=PEER, text="謝謝", minutes=-30, thread="t5", att=IMG_ATT)
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertNotIn(h[5]["name"], names(ctx.image_messages))

    def test_meta_reports_anchor_count(self):
        h = self._history()
        ctx = build(FakeClient(recent=h), h[2], [h[4]])
        self.assertEqual(ctx.to_meta()["anchor_count"], 2)

    def test_single_anchor_still_reports_one(self):
        h = self._history()
        ctx = build(FakeClient(recent=h), h[4])
        self.assertEqual(ctx.anchor_count, 1)
        self.assertEqual(ctx.to_meta()["anchor_count"], 1)

    def test_extra_anchor_outside_the_fetch_window_is_pulled_in(self):
        """比最近 N 則還舊的那一則也要被標成錨點，不能靜默掉成一般脈絡。"""
        recent = [msg(100 + i, sender=PEER, minutes=-(60 - i), thread=f"r{i}") for i in range(60)]
        old = msg(1, sender=PEER, text="很久以前那題", minutes=-9000, thread="t1")
        ctx = build(FakeClient(recent=recent), recent[-1], [old])
        self.assertEqual(ctx.anchor_count, 2)
        self.assertIn(old["name"], names(ctx.anchor_run))
        self.assertIn("很久以前那題", ctx.anchor_text)


class TestThreadMerge(unittest.TestCase):
    """群組：兩則 @ 都在同一個討論串。"""

    def _thread(self):
        return [
            msg(0, sender=OTHER, text="這串的開頭", minutes=-100, thread="tA"),
            msg(1, sender=PEER, text="@我 第一件事", minutes=-80, thread="tA"),
            msg(2, sender=ME, text="收到", minutes=-70, thread="tA"),
            msg(3, sender=PEER, text="@我 還有第二件事", minutes=-60, thread="tA"),
        ]

    def test_both_anchors_marked_and_thread_unchanged(self):
        t = self._thread()
        client = FakeClient(thread=t)
        ctx = build(
            client,
            t[1],
            [t[3]],
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            thread_name="spaces/S/threads/tA",
        )
        self.assertEqual(ctx.mode, "thread")
        self.assertEqual(ctx.anchor_count, 2)
        # 撈回來的訊息與單錨點時完全一樣——合併不該改變「撈什麼」
        self.assertEqual(names(ctx.blocks[0].messages), names(t))
        self.assertEqual(client.kinds(), ["list_thread_messages"])
        marked = [ln for ln in ctx.blocks[0].text.split("\n") if ln.startswith(dc.ANCHOR_MARK)]
        self.assertEqual(len(marked), 2)


class TestPromptMultiAnchor(unittest.TestCase):
    """prompt 必須明說「這是 N 則各自獨立的問題，要一次回完」。"""

    def _render(self, anchor_count):
        return prompts.draft_reply_prompt(
            anchor_text="[a] 對方: Q1\n[b] 對方: Q2",
            mention_sender="對方",
            space_name="王小明",
            space_type_label="一對一私訊",
            context_blocks=[],
            anchor_count=anchor_count,
            reference_blocks=[],
        )

    def test_multi_anchor_header_states_the_count(self):
        out = self._render(2)
        self.assertIn("共 2 則", out)
        self.assertIn("一則回話全部回完", out)

    def test_multi_anchor_warns_they_are_independent(self):
        out = self._render(2)
        self.assertIn("各自獨立", out)
        self.assertIn("一則都不能漏", out)

    def test_multi_anchor_adds_a_per_item_checklist_field(self):
        """〈逐則確認〉是唯一能讓「漏回一題」被看見的欄位。"""
        out = self._render(2)
        self.assertIn("- **逐則確認**", out)
        # 欄位要各自成行，不能和下一個欄位黏在一起
        lines = [ln for ln in out.split("\n") if ln.startswith("- **")]
        self.assertTrue(any(ln.startswith("- **逐則確認**") for ln in lines))
        self.assertTrue(any(ln.startswith("- **關鍵決策**") for ln in lines))

    def test_single_anchor_has_none_of_it(self):
        out = self._render(1)
        self.assertIn("【被 @ 的訊息】", out)
        self.assertNotIn("各自獨立", out)
        self.assertNotIn("逐則確認", out)


class TestMergeDoesNotBreakSingleAnchor(unittest.TestCase):
    """合併功能不可以改變單錨點的任何輸出——那是最常走的路徑。"""

    def test_flat_single_anchor_identical_with_and_without_empty_extras(self):
        h = [msg(i, sender=PEER, minutes=-(20 - i) * 5, thread=f"t{i}") for i in range(20)]
        a = build(FakeClient(recent=h), h[15])
        b = build(FakeClient(recent=h), h[15], [])
        self.assertEqual(a.mode, b.mode)
        self.assertEqual(names(a.blocks[0].messages), names(b.blocks[0].messages))
        self.assertEqual(a.blocks[0].text, b.blocks[0].text)
        self.assertEqual(names(a.image_messages), names(b.image_messages))
        self.assertEqual(a.anchor_count, 1)

    def test_duplicate_anchor_is_deduped(self):
        """把同一則勾兩次（或前端把主要那則也塞進 merge 清單）不該變成 2 則。"""
        h = [msg(i, sender=PEER, minutes=-(20 - i) * 5, thread=f"t{i}") for i in range(20)]
        ctx = build(FakeClient(recent=h), h[15], [h[15]])
        self.assertEqual(ctx.anchor_count, 1)


class TestMergeLimit(unittest.TestCase):
    def test_config_max_is_below_the_context_window(self):
        """合併上限不能大到把錨點窗塞滿——那樣脈絡就沒有空間了。"""
        from dashboard.api import server  # noqa: PLC0415 - 只有這條測試需要

        self.assertLessEqual(server.MERGE_MAX, cfg.DRAFT_CTX_BEFORE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
