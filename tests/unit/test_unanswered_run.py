"""「要回哪幾則」的判準：從我上次發言到現在，對方講了什麼我還沒回。

**這一套是為了一個實測到的失敗寫的。** 2026-09-07：對方 11:33 問白名單、
12:32 問 LINE 推播，相隔 **59 分鐘**。原本的規則是「同一人 ＋ 間隔 < 5 分鐘
才算同一串連發」，於是只有 12:32 被標成「要回的」，11:33 掉進背景脈絡——
草稿把 LINE 推播答得很完整，白名單那題只寫了「我另外看，確認完再回你」。

間隔多久跟「這則我回了沒」根本沒有關係。新判準只看發話者：
同一個人講的、中間我沒插過話，就都是我欠的。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import draft_context as dc  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_draft_context import (  # noqa: E402
    ME,
    OTHER,
    PEER,
    FakeClient,
    msg,
    names,
    resolve,
)


def build(history, anchor, **kw):
    params = dict(
        space_id="spaces/S",
        space_type="DIRECT_MESSAGE",
        threading_state="THREADED_MESSAGES",
        anchor_msg=anchor,
        resolve=resolve,
        self_user_id=ME,
    )
    params.update(kw)
    return dc.build(FakeClient(recent=history, thread=history), **params)


class TestTheReportedCase(unittest.TestCase):
    """重現使用者回報的那一段，逐項驗它現在被怎麼處理。"""

    def _history(self):
        # 時間取自實測：09-04 05:44 我最後發言 → 對方 05:46 圖 → 11:33 → 12:32
        return [
            msg(1, sender=PEER, text="台北通上安卓會出現錯誤", minutes=-(59 + 3226 + 2 + 7)),
            msg(2, sender=ME, text="這個路徑錯了", minutes=-(59 + 3226 + 2)),
            msg(3, sender=ME, text="後面應該是/tpcd", minutes=-(59 + 3226 + 2)),
            msg(4, sender=PEER, text="（截圖）", minutes=-(59 + 3226)),
            msg(5, sender=PEER, text="mark 我在彙整台北市白名單，粉底的部分請協助確認", minutes=-59),
            msg(6, sender=PEER, text="line推播 我剛測試還是沒收到", minutes=0),
        ]

    def test_both_questions_are_marked_even_though_they_are_59_minutes_apart(self):
        h = self._history()
        ctx = build(h, h[-1])
        marked = [m["text"] for m in ctx.anchor_run]
        self.assertIn("mark 我在彙整台北市白名單，粉底的部分請協助確認", marked)
        self.assertIn("line推播 我剛測試還是沒收到", marked)

    def test_it_counts_as_two_separate_questions(self):
        """59 分鐘分屬兩群，所以 prompt 要切到「N 件事」模式。"""
        h = self._history()
        self.assertEqual(build(h, h[-1]).anchor_count, 2)

    def test_the_old_gap_rule_alone_would_have_missed_it(self):
        """正對照：不給 self_user_id 就退回舊行為，只標到最後那一則。"""
        h = self._history()
        ctx = build(h, h[-1], self_user_id=None)
        self.assertEqual(len(ctx.anchor_run), 1)
        self.assertEqual(ctx.anchor_count, 1)

    def test_it_stops_at_my_own_last_message(self):
        """我 09-04 已經回過的那些不該被翻出來重回一次。"""
        h = self._history()
        marked = names(build(h, h[-1]).anchor_run)
        self.assertNotIn(h[0]["name"], marked)  # 我回過了
        self.assertNotIn(h[1]["name"], marked)  # 我自己說的
        self.assertNotIn(h[2]["name"], marked)

    def test_messages_beyond_48h_are_not_marked(self):
        """09-04 那張圖離錨點兩天多，硬要一起回反而奇怪——但它仍在脈絡裡看得到。"""
        h = self._history()
        ctx = build(h, h[-1])
        self.assertNotIn(h[3]["name"], names(ctx.anchor_run))
        self.assertIn(h[3]["name"], names(ctx.blocks[0].messages))


class TestClustering(unittest.TestCase):
    """間隔現在只用來分群（幾件事），不用來決定要回哪幾則。"""

    def test_one_question_split_into_three_is_still_one(self):
        h = [
            msg(1, sender=ME, text="上一輪", minutes=-600),
            msg(2, sender=PEER, text="你好", minutes=-3),
            msg(3, sender=PEER, text="想問一下匯出報表為什麼會 500", minutes=-2),
            msg(4, sender=PEER, text="方便的話今天回我", minutes=-1),
        ]
        ctx = build(h, h[-1])
        self.assertEqual(len(ctx.anchor_run), 3)
        self.assertEqual(ctx.anchor_count, 1)  # 一件事，不要切成「3 件事」

    def test_two_clusters_far_apart(self):
        h = [
            msg(1, sender=ME, text="上一輪", minutes=-600),
            msg(2, sender=PEER, text="A", minutes=-120),
            msg(3, sender=PEER, text="A2", minutes=-119),
            msg(4, sender=PEER, text="B", minutes=-1),
        ]
        ctx = build(h, h[-1])
        self.assertEqual(len(ctx.anchor_run), 3)
        self.assertEqual(ctx.anchor_count, 2)

    def test_cluster_helper_is_pure(self):
        msgs = [
            msg(1, sender=PEER, minutes=-10),
            msg(2, sender=PEER, minutes=-9),
            msg(3, sender=PEER, minutes=-1),
        ]
        groups = dc.cluster_messages(msgs)
        self.assertEqual([len(g) for g in groups], [2, 1])

    def test_different_senders_never_share_a_cluster(self):
        msgs = [msg(1, sender=PEER, minutes=-2), msg(2, sender=OTHER, minutes=-2)]
        self.assertEqual(len(dc.cluster_messages(msgs)), 2)


class TestBoundaries(unittest.TestCase):
    def test_stops_at_a_third_person_in_a_group(self):
        """群組裡別人講的話不是我欠的——收攏只往同一個發話者延伸。"""
        thread = [
            msg(1, sender=PEER, text="A 問的", minutes=-30, thread="tA"),
            msg(2, sender=OTHER, text="路人插話", minutes=-20, thread="tA"),
            msg(3, sender=PEER, text="A 又問", minutes=-10, thread="tA"),
        ]
        ctx = dc.build(
            FakeClient(thread=thread),
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=thread[-1],
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
            self_user_id=ME,
        )
        self.assertEqual(names(ctx.anchor_run), [thread[2]["name"]])

    def test_thread_path_also_counts_questions_not_messages(self):
        """討論串那一側的 anchor_count 也要算「幾件事」，不是「幾則」。

        （這條是補的：原本只有 flat_window 那側有測，討論串側改成 len(anchor_run)
        也不會有任何測試變紅。）
        """
        thread = [
            msg(1, sender=ME, text="上一輪", minutes=-600, thread="tA"),
            msg(2, sender=PEER, text="你好", minutes=-3, thread="tA"),
            msg(3, sender=PEER, text="想問一下 X", minutes=-2, thread="tA"),
            msg(4, sender=PEER, text="今天回我", minutes=-1, thread="tA"),
        ]
        ctx = dc.build(
            FakeClient(thread=thread),
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=thread[-1],
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
            self_user_id=ME,
        )
        self.assertEqual(len(ctx.anchor_run), 3)
        self.assertEqual(ctx.anchor_count, 1)

    def test_extends_forward_too(self):
        """真的 Mention 之後對方又補了幾句，那些也是我欠的。"""
        thread = [
            msg(1, sender=PEER, text="@我 幫我看一下", minutes=-30, thread="tA"),
            msg(2, sender=PEER, text="補充：只有安卓會", minutes=-20, thread="tA"),
            msg(3, sender=PEER, text="另外還有第二件", minutes=-10, thread="tA"),
        ]
        ctx = dc.build(
            FakeClient(thread=thread),
            space_id="spaces/S",
            space_type="SPACE",
            threading_state="THREADED_MESSAGES",
            anchor_msg=thread[0],  # 錨點是最早那則 @
            thread_name="spaces/S/threads/tA",
            resolve=resolve,
            self_user_id=ME,
        )
        self.assertEqual(len(ctx.anchor_run), 3)

    def test_caps_at_max_clusters(self):
        """一次最多標 N 件；更舊的仍看得到，只是不標成「要回的」。"""
        h = [msg(0, sender=ME, text="上一輪", minutes=-1000)]
        h += [msg(i, sender=PEER, text=f"問題{i}", minutes=-(30 - i) * 10) for i in range(1, 8)]
        ctx = build(h, h[-1])
        self.assertEqual(ctx.anchor_count, cfg.DRAFT_ANCHOR_MAX_CLUSTERS)
        # 被擋掉的那幾則沒有消失，只是沒被標
        self.assertIn(h[1]["name"], names(ctx.blocks[0].messages))
        self.assertNotIn(h[1]["name"], names(ctx.anchor_run))

    def test_keeps_the_newest_clusters_not_the_oldest(self):
        h = [msg(0, sender=ME, text="上一輪", minutes=-1000)]
        h += [msg(i, sender=PEER, text=f"問題{i}", minutes=-(30 - i) * 10) for i in range(1, 8)]
        marked = [m["text"] for m in build(h, h[-1]).anchor_run]
        self.assertIn("問題7", marked)
        self.assertNotIn("問題1", marked)

    def test_single_unanswered_message_still_reads_as_one(self):
        h = [
            msg(1, sender=ME, text="上一輪", minutes=-600),
            msg(2, sender=PEER, text="只有一件事", minutes=-5),
        ]
        ctx = build(h, h[-1])
        self.assertEqual(ctx.anchor_count, 1)
        self.assertEqual(len(ctx.anchor_run), 1)

    def test_images_cover_every_unanswered_message(self):
        """兩題各帶一張圖時兩張都要送——只送一張等於漏看一題。"""
        from test_draft_context import IMG_ATT  # noqa: PLC0415

        h = [
            msg(1, sender=ME, text="上一輪", minutes=-600),
            msg(2, sender=PEER, text="白名單", minutes=-59, att=IMG_ATT),
            msg(3, sender=PEER, text="LINE 推播", minutes=0, att=IMG_ATT),
        ]
        img_names = names(build(h, h[-1]).image_messages)
        self.assertIn(h[1]["name"], img_names)
        self.assertIn(h[2]["name"], img_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
