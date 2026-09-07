"""core/polishers/base.py 的錨點抽取與完整性驗證（純函式，不打任何 API）。

**為什麼這一套要存在。** 潤稿是「讓一個模型改寫另一個模型寫的事實陳述」，
而完整性檢查是唯一擋得住它改壞事實的東西。這條防線失效是**雙重靜默**的：

  * 潤稿改掉數字不會有例外——「timeout 是 30 秒」變成「大約半分鐘」
    讀起來更自然，而且潤稿後的版本**比原版更可信**；
  * 檢查失效也不會有例外——它只是回 `ok=True`，草稿照樣送出去。

所以這裡驗的不是「函式跑得動」，而是三種各自獨立的失敗形態：

  1. **抽不到錨點**（漏抽）→ 檢查形同虛設。八個類別逐一驗。
  2. **重複抽到**（遮蔽順序壞了）→ `TimeoutConfig.java:88` 的 `88` 若同時
     變成獨立的 `number`，刪掉整個 file:line 會同時報兩類遺失，
     錯誤訊息從「檔案行號不見了」變成無法判讀的一串；`2026-09-01` 若同時被
     `date` 與 `number` 抽到也一樣。這種壞法**不會讓測試變紅**，
     只會讓 `fallback_reason` 慢慢變得沒人看得懂。
  3. **鬆緊調錯**→ 嚴格類別（連結／SHA／檔案行號／日期／數字）放行等於
     防線消失；寬鬆類別（路徑／識別字／時間）收緊則是正常潤稿被大量誤退，
     使用者會認定「這個功能壞了」而永遠關掉它。

另外守兩個容易被「優化」掉的設計：集合語意（刪重複是合理潤稿，不是改事實）
與問號檢查（把「問回去」潤掉會讓對方不知道球在誰手上）。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import polishers  # noqa: E402
from core.errors import InvalidParameter  # noqa: E402
from core.polishers import base  # noqa: E402

#: 一段塞滿各類事實錨點的回話，形態照抄真實草稿（見 test_draft_polish_pipeline）。
ANCHORED = (
    "我看 production branch，目前 timeout 是 30 秒"
    "（TimeoutConfig.java:88，commit a3f91c2）。"
    "這個值是 2026-09-01 在 14:33 改的，細節見 https://wiki.example.com/timeout，"
    "程式在 core/polishers/base.py，入口是 `resolve()`。你要問的是這個嗎？"
)


def kinds(text):
    """只回類別名稱的集合，斷言「抽到／沒抽到哪幾類」時比整包 dict 好讀。"""
    return set(base.extract_anchors(text))


class TestEveryAnchorKindIsActuallyExtracted(unittest.TestCase):
    """漏抽一類 = 那一類的事實從此可以被自由改寫，而且完全沒有訊號。"""

    def test_urls_are_extracted(self):
        self.assertEqual(
            base.extract_anchors("詳見 https://example.com/docs/a?b=1 這頁")["url"],
            ["https://example.com/docs/a?b=1"],
        )

    def test_commit_shas_are_extracted(self):
        self.assertEqual(base.extract_anchors("見 commit a3f91c2")["commit_sha"], ["a3f91c2"])

    def test_file_line_references_are_extracted(self):
        found = base.extract_anchors("TimeoutConfig.java:88 與 app/main.py:10-20")["file_line"]
        self.assertEqual(found, ["TimeoutConfig.java:88", "app/main.py:10-20"])

    def test_dates_are_extracted_in_all_four_written_forms(self):
        found = base.extract_anchors("2026-09-01、2026/09/02、2026年9月3日、9/15")["date"]
        self.assertEqual(found, ["2026-09-01", "2026/09/02", "2026年9月3日", "9/15"])

    def test_times_are_extracted(self):
        self.assertEqual(
            base.extract_anchors("14:33 與 09:00:15")["time"], ["09:00:15", "14:33"]
        )

    def test_numbers_including_decimals_and_thousands_separators(self):
        found = base.extract_anchors("timeout 是 30 秒，共 1,234 筆，比率 0.75")["number"]
        self.assertEqual(found, ["0.75", "1,234", "30"])

    def test_paths_are_extracted(self):
        found = base.extract_anchors("見 core/polishers/base.py 與 README.md")["path"]
        self.assertEqual(found, ["README.md", "core/polishers/base.py"])

    def test_code_symbols_come_from_backticks_without_the_backticks(self):
        found = base.extract_anchors("呼叫 `retry()` 之後看 `TimeoutConfig`")["code_symbol"]
        self.assertEqual(found, ["TimeoutConfig", "retry()"])

    def test_a_realistic_reply_yields_every_kind(self):
        self.assertEqual(kinds(ANCHORED), set(base._ANCHOR_KINDS))

    def test_extraction_is_deduplicated_and_sorted(self):
        """集合語意的前提。回計次的話「刪掉重複那句」就會變成假失敗。"""
        self.assertEqual(base.extract_anchors("30 秒，再說一次 30 秒")["number"], ["30"])

    def test_empty_text_yields_nothing_instead_of_raising(self):
        self.assertEqual(base.extract_anchors(""), {})
        self.assertEqual(base.extract_anchors(None), {})


class TestMaskingOrderPreventsDoubleCounting(unittest.TestCase):
    """先抽結構完整的、抽到就遮蔽，再抽零散的。順序反了訊息就沒人看得懂。"""

    def test_the_line_number_does_not_also_become_a_number(self):
        found = base.extract_anchors("問題在 TimeoutConfig.java:88")
        self.assertEqual(found["file_line"], ["TimeoutConfig.java:88"])
        self.assertNotIn("number", found, "88 不可以同時是獨立的數字錨點")

    def test_the_line_number_does_not_also_become_a_time(self):
        self.assertNotIn("time", base.extract_anchors("問題在 TimeoutConfig.java:88"))

    def test_a_date_is_not_also_three_numbers(self):
        found = base.extract_anchors("2026-09-01 改的")
        self.assertEqual(found["date"], ["2026-09-01"])
        self.assertNotIn("number", found, "日期的年月日不可以再被當成數字")

    def test_a_time_is_not_also_two_numbers(self):
        found = base.extract_anchors("14:33 改的")
        self.assertEqual(found["time"], ["14:33"])
        self.assertNotIn("number", found)

    def test_digits_inside_a_url_do_not_become_numbers(self):
        found = base.extract_anchors("見 https://example.com/issues/1234")
        self.assertIn("url", found)
        self.assertNotIn("number", found)

    def test_a_file_line_is_not_also_a_bare_path(self):
        """否則刪掉 `:88` 會同時報「檔案行號遺失」與「路徑新增」兩件事。"""
        found = base.extract_anchors("問題在 core/app/TimeoutConfig.java:88")
        self.assertEqual(found["file_line"], ["core/app/TimeoutConfig.java:88"])
        self.assertNotIn("path", found)

    def test_the_realistic_reply_has_exactly_one_number(self):
        """`30`。日期、時間、行號、commit、URL 裡的數字都已經被遮蔽掉了。"""
        self.assertEqual(base.extract_anchors(ANCHORED)["number"], ["30"])


class TestCommitShaIsNotJustAnyHexLookingWord(unittest.TestCase):
    """英文單字剛好都由 a–f 組成時會被誤判成 SHA，然後正常潤稿被誤退。"""

    def test_a_pure_letter_hex_word_is_not_a_sha(self):
        self.assertNotIn("commit_sha", base.extract_anchors("這段程式碼是 feedface 寫的"))

    def test_another_pure_letter_hex_word_is_not_a_sha(self):
        self.assertNotIn("commit_sha", base.extract_anchors("測試值 deadbeef 只是佔位"))

    def test_a_hex_word_containing_a_digit_is_a_sha(self):
        self.assertEqual(
            base.extract_anchors("commit deadbe3f")["commit_sha"], ["deadbe3f"]
        )

    def test_a_hex_run_glued_to_other_letters_is_not_a_sha(self):
        self.assertNotIn("commit_sha", base.extract_anchors("變數名 xa3f91c2y"))

    def test_a_six_character_hex_is_too_short(self):
        self.assertNotIn("commit_sha", base.extract_anchors("值 a3f91c 太短"))


class TestVerifyIntegrityStrictKinds(unittest.TestCase):
    """嚴格類別遺失或被發明，一律判定潤稿失敗——這是這一層存在的理由。"""

    def test_identical_text_passes(self):
        self.assertTrue(base.verify_integrity(ANCHORED, ANCHORED).ok)

    def test_a_pure_wording_change_passes(self):
        """只改語氣、錨點全留，才是潤稿「成功」的樣子。"""
        report = base.verify_integrity(
            "我看 production，目前 timeout 是 30 秒（commit a3f91c2）。你要問的是這個嗎？",
            "production 現在的 timeout 是 30 秒（commit a3f91c2）。你問的是這個嗎？",
        )
        self.assertTrue(report.ok, report.reason())

    def test_a_lost_number_fails(self):
        report = base.verify_integrity("timeout 是 30 秒", "timeout 大約半分鐘")
        self.assertFalse(report.ok)
        self.assertIn("number", report.lost)

    def test_a_lost_url_fails(self):
        report = base.verify_integrity("見 https://a.example/x", "見文件")
        self.assertFalse(report.ok)
        self.assertIn("url", report.lost)

    def test_a_lost_commit_sha_fails(self):
        report = base.verify_integrity("commit a3f91c2 改的", "最近一次 commit 改的")
        self.assertFalse(report.ok)
        self.assertIn("commit_sha", report.lost)

    def test_a_lost_file_line_fails(self):
        report = base.verify_integrity("見 TimeoutConfig.java:88", "見 TimeoutConfig.java")
        self.assertFalse(report.ok)
        self.assertIn("file_line", report.lost)

    def test_a_lost_date_fails(self):
        report = base.verify_integrity("2026-09-01 改的", "前陣子改的")
        self.assertFalse(report.ok)
        self.assertIn("date", report.lost)

    def test_an_invented_number_fails(self):
        """發明原文沒有的具體資訊比遺失更危險——讀者無從察覺。"""
        report = base.verify_integrity("timeout 是 30 秒", "timeout 是 30 秒，預設值本來是 120 秒")
        self.assertFalse(report.ok)
        self.assertIn("number", report.invented)
        self.assertIn("120", report.invented["number"])

    def test_an_invented_date_fails(self):
        report = base.verify_integrity("timeout 是 30 秒", "timeout 是 30 秒，2026-01-01 改的")
        self.assertFalse(report.ok)
        self.assertIn("date", report.invented)

    def test_an_invented_url_fails(self):
        report = base.verify_integrity("見文件", "見 https://a.example/x")
        self.assertFalse(report.ok)
        self.assertIn("url", report.invented)

    def test_a_changed_number_is_reported_as_both_lost_and_invented(self):
        report = base.verify_integrity("timeout 是 30 秒", "timeout 是 60 秒")
        self.assertFalse(report.ok)
        self.assertEqual(report.lost["number"], ["30"])
        self.assertEqual(report.invented["number"], ["60"])


class TestSetSemanticsToleratesDeduplication(unittest.TestCase):
    """刪掉重複提到的同一個數字是合理的潤稿，不是改掉事實。

    改成計次比對的話，「timeout 是 30 秒…這個 30 秒是上週改的」被潤成
    只講一次 30 秒，就會被判失敗——而那正是我們**希望**潤稿做的事。
    """

    def test_a_repeated_number_kept_once_still_passes(self):
        report = base.verify_integrity("timeout 是 30 秒，這個 30 秒是上週改的", "timeout 是 30 秒")
        self.assertTrue(report.ok, report.reason())
        self.assertEqual(report.lost, {})

    def test_a_repeated_file_line_kept_once_still_passes(self):
        report = base.verify_integrity(
            "見 TimeoutConfig.java:88，也就是 TimeoutConfig.java:88 那一行",
            "見 TimeoutConfig.java:88",
        )
        self.assertTrue(report.ok, report.reason())

    def test_saying_the_same_number_twice_is_not_an_invention(self):
        report = base.verify_integrity("timeout 是 30 秒", "timeout 是 30 秒，30 秒是上限")
        self.assertTrue(report.ok, report.reason())


class TestSoftKindsAreRecordedNotEnforced(unittest.TestCase):
    """路徑／識別字／時間的邊界本來就模糊，收緊會讓正常潤稿被大量誤退。"""

    def test_the_soft_set_is_exactly_these_three(self):
        self.assertEqual(set(base._SOFT_KINDS), {"path", "code_symbol", "time"})

    def test_the_strict_set_is_exactly_these_five(self):
        self.assertEqual(
            set(base._STRICT_KINDS), {"url", "commit_sha", "file_line", "date", "number"}
        )

    def test_a_changed_path_passes_but_is_recorded(self):
        report = base.verify_integrity("見 core/a.py", "見 core/b.py")
        self.assertTrue(report.ok)
        self.assertIn("path", report.soft_diff)
        self.assertIn("-core/a.py", report.soft_diff["path"])
        self.assertIn("+core/b.py", report.soft_diff["path"])

    def test_dropping_backticks_passes_but_is_recorded(self):
        """格式變了、事實沒變——這是潤稿常做的事，不該退回。"""
        report = base.verify_integrity("呼叫 `retry()`", "呼叫 retry()")
        self.assertTrue(report.ok)
        self.assertIn("code_symbol", report.soft_diff)

    def test_a_changed_time_passes_but_is_recorded(self):
        report = base.verify_integrity("14:33 改的", "15:00 改的")
        self.assertTrue(report.ok)
        self.assertIn("time", report.soft_diff)

    def test_soft_differences_leave_the_reason_empty(self):
        """`ok=True` 卻有 `fallback_reason` 會讓前端標示「潤稿被退回」。"""
        report = base.verify_integrity("見 core/a.py", "見 core/b.py")
        self.assertEqual(report.reason(), "")


class TestTheQuestionMustSurvive(unittest.TestCase):
    """潤稿的天性是刪東西，最容易刪掉的就是結尾那句提問。"""

    def test_dropping_the_only_question_fails(self):
        report = base.verify_integrity("你要問的是這個嗎？", "就是這樣。")
        self.assertFalse(report.ok)
        self.assertTrue(report.lost_question)

    def test_a_half_width_question_mark_counts_too(self):
        report = base.verify_integrity("Is that what you meant?", "That is it.")
        self.assertFalse(report.ok)
        self.assertTrue(report.lost_question)

    def test_rewording_the_question_is_fine(self):
        report = base.verify_integrity("你要問的是這個嗎？", "你問的是這個嗎？")
        self.assertTrue(report.ok)
        self.assertFalse(report.lost_question)

    def test_text_without_a_question_is_not_penalised(self):
        """原文本來就沒問號時，潤稿後沒問號當然不算遺失。"""
        report = base.verify_integrity("timeout 是 30 秒。", "目前 timeout 設定為 30 秒。")
        self.assertTrue(report.ok)
        self.assertFalse(report.lost_question)

    def test_adding_a_question_is_not_a_failure(self):
        """多問一句不會讓對方拿到錯的事實，不需要為此退回。"""
        report = base.verify_integrity("timeout 是 30 秒。", "timeout 是 30 秒。這樣可以嗎？")
        self.assertTrue(report.ok)


class TestReasonIsReadableByAHuman(unittest.TestCase):
    """訊息只寫「驗證失敗」的話，沒人判得出是模型改壞了還是我們誤判。"""

    def test_a_passing_report_has_no_reason(self):
        self.assertEqual(base.verify_integrity("abc", "abc").reason(), "")

    def test_a_lost_number_names_the_kind_and_the_value(self):
        reason = base.verify_integrity("timeout 是 30 秒", "timeout 大約半分鐘").reason()
        self.assertIn("數字", reason)
        self.assertIn("30", reason)

    def test_a_lost_file_line_uses_the_chinese_kind_name(self):
        reason = base.verify_integrity("見 TimeoutConfig.java:88", "見 TimeoutConfig.java").reason()
        self.assertIn("檔案行號", reason)
        self.assertIn("TimeoutConfig.java:88", reason)

    def test_a_lost_commit_sha_uses_the_conventional_english_name(self):
        reason = base.verify_integrity("commit a3f91c2", "最近一次 commit").reason()
        self.assertIn("commit SHA", reason)

    def test_an_invention_is_worded_differently_from_a_loss(self):
        reason = base.verify_integrity("timeout 是 30 秒", "timeout 是 30 秒，預設 120 秒").reason()
        self.assertIn("新增了原文沒有的", reason)
        self.assertNotIn("遺失", reason)

    def test_a_lost_question_is_spelled_out(self):
        reason = base.verify_integrity("這樣可以嗎？", "就這樣。").reason()
        self.assertIn("問號", reason)

    def test_several_problems_are_joined_into_one_sentence(self):
        reason = base.verify_integrity(
            "timeout 是 30 秒（TimeoutConfig.java:88）。你要問的是這個嗎？", "設定改過了。"
        ).reason()
        self.assertIn("數字", reason)
        self.assertIn("檔案行號", reason)
        self.assertIn("問號", reason)


class TestNoopPolisher(unittest.TestCase):
    """關閉潤稿與開啟潤稿走**同一條**程式路徑，少一個分支就少一種失敗形態。"""

    def test_it_is_always_available(self):
        self.assertEqual(base.NoopPolisher().available(), (True, ""))

    def test_it_returns_the_text_untouched(self):
        result = base.NoopPolisher().polish(base.PolishRequest(reply=ANCHORED))
        self.assertEqual(result.text, ANCHORED)

    def test_it_reports_that_nothing_was_polished(self):
        """`polished=True` 會讓前端說「已用 Sepia 潤稿」，那是謊話。"""
        result = base.NoopPolisher().polish(base.PolishRequest(reply="原文"))
        self.assertFalse(result.polished)
        self.assertEqual(result.polisher, "noop")

    def test_it_has_no_fallback_reason(self):
        """沒潤稿與「潤了但退回」是兩件事，混在一起使用者無法判斷。"""
        result = base.NoopPolisher().polish(base.PolishRequest(reply="原文"))
        self.assertIsNone(result.fallback_reason)

    def test_its_meta_shape_matches_what_the_sse_layer_stores(self):
        result = base.NoopPolisher().polish(base.PolishRequest(reply="原文"))
        self.assertEqual(result.to_meta(), {"polisher": "noop", "polished": False})


class TestResolve(unittest.TestCase):
    """`resolve()` 是設定值進入系統的唯一入口，放行錯的值等於下游 AttributeError。"""

    def test_the_four_ways_of_saying_off_all_give_noop(self):
        for name in ("noop", None, "", "off", "none"):
            with self.subTest(name=name):
                self.assertIsInstance(polishers.resolve(name), base.NoopPolisher)

    def test_the_name_is_case_insensitive_and_trimmed(self):
        self.assertEqual(polishers.resolve("  NOOP  ").name, "noop")
        self.assertEqual(polishers.resolve("Sepia").name, "sepia")

    def test_sepia_resolves_to_the_sepia_polisher(self):
        from core.polishers.sepia import SepiaPolisher  # noqa: PLC0415

        self.assertIsInstance(polishers.resolve("sepia"), SepiaPolisher)

    def test_an_unknown_polisher_is_rejected(self):
        with self.assertRaises(InvalidParameter):
            polishers.resolve("grammarly")

    def test_the_error_message_lists_the_legal_values(self):
        with self.assertRaises(InvalidParameter) as caught:
            polishers.resolve("grammarly")
        self.assertIn("noop", caught.exception.message)
        self.assertIn("sepia", caught.exception.message)
        self.assertIn("grammarly", caught.exception.message)

    def test_everything_resolve_returns_implements_the_interface(self):
        for name in ("noop", "sepia"):
            with self.subTest(name=name):
                self.assertIsInstance(polishers.resolve(name), base.ResponsePolisher)


class TestDescribeAll(unittest.TestCase):
    """前端靠這份清單決定「Sepia 這個開關能不能點」與「不能點的話寫什麼」。"""

    def test_it_lists_both_polishers_in_a_stable_order(self):
        self.assertEqual([e["name"] for e in polishers.describe_all()], ["noop", "sepia"])

    def test_every_entry_has_the_four_fields(self):
        for entry in polishers.describe_all():
            with self.subTest(name=entry["name"]):
                self.assertEqual(set(entry), {"name", "label", "available", "reason"})

    def test_labels_are_traditional_chinese_and_non_empty(self):
        for entry in polishers.describe_all():
            with self.subTest(name=entry["name"]):
                self.assertTrue(entry["label"].strip())

    def test_availability_is_a_real_boolean(self):
        """回 truthy 的字串會讓前端的 `if (available)` 永遠成立。"""
        for entry in polishers.describe_all():
            with self.subTest(name=entry["name"]):
                self.assertIsInstance(entry["available"], bool)

    def test_noop_is_always_available(self):
        entry = next(e for e in polishers.describe_all() if e["name"] == "noop")
        self.assertTrue(entry["available"])
        self.assertEqual(entry["reason"], "")

    def test_an_unavailable_polisher_says_why(self):
        """不可用卻沒有原因，使用者只看得到一個點不下去的開關。"""
        for entry in polishers.describe_all():
            with self.subTest(name=entry["name"]):
                if not entry["available"]:
                    self.assertTrue(entry["reason"].strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
