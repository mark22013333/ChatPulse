"""core/prompts.draft_reply_prompt() 的單元測試（不打任何 API）。

**為什麼這一套要存在。** 這次把簽章從 `thread_text: str` 換成
`context_blocks: List[Dict]`，動機正是「只改撈什麼、不改 prompt，等於對模型說謊」：
把該 Space 最近 20 則（分屬 6 個 thread）塞進標著「該討論串的完整對話」的區塊，
模型會相信這些是同一串的連續發言，於是把別串的結論寫成本串的既定事實。
那種錯誤比「明顯的資訊不足」更貴——它看起來很有脈絡。

所以這裡守的是**字串層級**的承諾：假標題不能回來、警語必須在區塊之前、
涵蓋範圍必須可稽核。這些都是 e2e 看不出來的東西。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import prompts  # noqa: E402

THREAD_BLOCK = {
    "kind": "thread",
    "label": "該討論串的完整對話（共 4 則）",
    "note": "",
    "count": 4,
    "time_range": ("2026-09-06T10:00:00Z", "2026-09-06T11:30:00Z"),
    "text": "[2026-09-06 10:00] 甲: 開頭\n▶ [2026-09-06 11:30] 乙: @我 這個怎麼辦",
}

FLAT_BLOCK = {
    "kind": "flat_window",
    "label": "這個一對一私訊在該則前後的連續對話（前 15 則、後 10 則）",
    "note": "這個一對一私訊沒有討論串結構，**這個時間序列本身就是對話**。",
    "count": 26,
    "time_range": ("2026-09-04T10:12:00Z", "2026-09-06T14:33:00Z"),
    "text": "▶ [2026-09-06 14:33] 對方: 想問一下匯出報表",
}

CROSS_BLOCK = {
    "kind": "cross_thread",
    "label": "⚠️ 同一聊天室其他討論串的近期訊息（共 8 則）",
    "note": "這些訊息**不屬於**你要回覆的那一串，只是時間相近的其他討論。"
    "僅供理解背景，**不可**當成本串已經談定的結論，也不要在回話中引用。",
    "count": 8,
    "time_range": ("2026-09-06T09:00:00Z", "2026-09-06T11:00:00Z"),
    "text": "[2026-09-06 09:00] 丙: 別串的討論",
}


def render(**kwargs):
    base = dict(
        anchor_text="[2026-09-06 14:33] 對方: 想問一下匯出報表",
        mention_sender="對方",
        space_name="測試聊天室",
        space_type_label="一對一私訊",
        context_blocks=[FLAT_BLOCK],
        coverage="full",
        reference_blocks=[],
        code_blocks=None,
    )
    base.update(kwargs)
    return prompts.draft_reply_prompt(**base)


class TestFakePromiseIsGone(unittest.TestCase):
    """「【該討論串的完整對話】」不可以再是寫死的標題。"""

    def test_flat_window_does_not_claim_to_be_a_thread(self):
        out = render()
        self.assertNotIn("【該討論串的完整對話】", out)
        self.assertIn("這個時間序列本身就是對話", out)

    def test_thread_block_keeps_its_own_label(self):
        out = render(context_blocks=[THREAD_BLOCK], space_type_label="多人群組")
        self.assertIn("該討論串的完整對話（共 4 則）", out)

    def test_coverage_is_auditable_not_a_promise_of_completeness(self):
        out = render()
        self.assertIn("共 26 則", out)
        self.assertIn("2026-09-04 10:12 ~ 2026-09-06 14:33", out)
        self.assertIn("這是系統能取到的全部", out)


class TestAnchor(unittest.TestCase):
    def test_anchor_marker_is_explained(self):
        out = render()
        self.assertIn("▶", out)
        self.assertIn("要回覆的那一則在下方以 ▶ 標記", out)

    def test_multi_message_anchor_run_is_printed_whole(self):
        run = "[2026-09-06 14:31] 對方: 你好\n[2026-09-06 14:32] 對方: 想問一下匯出報表為什麼會 500"
        out = render(anchor_text=run)
        self.assertIn("匯出報表為什麼會 500", out)
        self.assertIn("你好", out)

    def test_space_type_label_reaches_the_model(self):
        out = render(space_type_label="不分討論串的聊天室")
        self.assertIn("測試聊天室（不分討論串的聊天室）", out)


class TestCrossThreadWarning(unittest.TestCase):
    def test_warning_appears_before_the_block_content(self):
        """就近的指令服從度較高——警語放在區塊之後等於沒放。"""
        out = render(context_blocks=[THREAD_BLOCK, CROSS_BLOCK], space_type_label="多人群組")
        warn = out.index("這些訊息**不屬於**")
        body = out.index("別串的討論")
        label = out.index("⚠️ 同一聊天室其他討論串")
        self.assertLess(label, warn)
        self.assertLess(warn, body)

    def test_blocks_stay_separate(self):
        out = render(context_blocks=[THREAD_BLOCK, CROSS_BLOCK], space_type_label="多人群組")
        self.assertIn("該討論串的完整對話（共 4 則）", out)
        self.assertIn("⚠️ 同一聊天室其他討論串的近期訊息（共 8 則）", out)
        # 總則數是兩個區塊相加，不是把跨串內容藏進本串的計數裡
        self.assertIn("共 12 則", out)


class TestCoverage(unittest.TestCase):
    def test_partial_coverage_is_declared(self):
        out = render(coverage="partial")
        self.assertIn("不保證連續", out)

    def test_full_coverage_has_no_warning(self):
        self.assertNotIn("不保證連續", render(coverage="full"))

    def test_empty_context_says_so_instead_of_pretending(self):
        out = render(context_blocks=[])
        self.assertIn("沒有取到任何脈絡訊息", out)
        self.assertIn("不要", out)


class TestOutputContract(unittest.TestCase):
    """兩個章節與新增欄位——e2e 的驗收條件直接讀這些字串。"""

    def test_two_sections(self):
        out = render()
        self.assertIn("### 🧭 脈絡分析", out)
        self.assertIn("### ✍️ 建議回話", out)

    def test_context_coverage_field_is_mandatory(self):
        out = render()
        self.assertIn("**脈絡涵蓋**", out)
        self.assertIn("不可省略", out)

    def test_already_answered_still_requires_a_reply(self):
        """T-3：after 窗帶來的新失敗模式——模型可能改寫成「XXX 已經回覆了」。"""
        out = render()
        self.assertIn("仍然要寫出你自己的回話", out)

    def test_reference_blocks_still_work(self):
        out = render(
            reference_blocks=[
                {
                    "space_name": "1.BU2-PG",
                    "message_count": 50,
                    "conversation_text": "[2026-09-06 09:00] 丁: 參考內容",
                }
            ]
        )
        self.assertIn("【參考聊天室的脈絡】", out)
        self.assertIn("參考內容", out)

    def test_no_reference_blocks_says_so(self):
        self.assertIn("Viewer 未指定任何參考聊天室", render())

    def test_code_section_untouched(self):
        out = render(
            code_blocks=[
                {
                    "project_name": "demo",
                    "environment": "production",
                    "environment_label": "正式環境",
                    "branch": "main",
                    "commit_sha": "a3f91c2",
                    "terms": ["export"],
                    "notes": [],
                    "hits": [],
                }
            ]
        )
        self.assertIn("【參考專案原始碼】", out)
        self.assertIn("沒有找到", out)


class TestTimeFormatting(unittest.TestCase):
    def test_matches_format_conversation_slicing(self):
        """〔涵蓋範圍〕的時間格式必須與對話每一行的時間戳對得上。

        兩邊各自格式化就會出現「範圍寫本地時間、內文寫 UTC」這種對不上的情況，
        而模型會照著算日期。
        """
        self.assertEqual(prompts._short_time("2026-09-06T14:33:07.123Z"), "2026-09-06 14:33")
        self.assertEqual(prompts._short_time(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
