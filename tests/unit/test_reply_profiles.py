"""core/reply_profiles.py 的單元測試（純資料模組，不打任何 API）。

**為什麼這一套要存在。** 這個模組看起來只是一張 dict 表，而「一張表」正是
最容易靜默壞掉的東西——它壞掉的時候不會拋錯，只會讓產出悄悄變成另一種樣子：

  1. **`instruction` 外洩到 `tone_options()`。** 那是送進 prompt 的片段。
     哪天有人為了讓前端顯示「這個語氣會怎麼指示模型」而順手把它加進選項，
     整段 prompt 內容就會出現在瀏覽器 devtools 裡，而且沒有任何測試會變紅。
  2. **複製貼上忘了改 `instruction`。** 新增一個 tone 最自然的做法是複製
     上一個再改字。改漏的話 UI 上有 9 個選項、實際只有 8 種語氣，
     使用者選了「委婉柔和」卻拿到「堅定明確」的產出——這種錯誤只有
     逐字比對兩份輸出才看得出來。
  3. **`REPLY_TONES` 與 `_TONES` 漂移。** 前者是 API 與設定檔的驗證依據，
     後者是實際查表的來源。兩邊不一致時，`validate_tone()` 會放行一個
     `tone_instruction()` 查不到的值，結果是產草稿到一半 KeyError（500）。
  4. **`is_empty()` 判錯。** 它是「要不要在 prompt 加【回話風格】區塊」的
     唯一開關。判成 False 會讓沒設定任何偏好的使用者也拿到一個空區塊，
     打破「不選就與這個功能存在之前逐字相同」的向後相容承諾。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import reply_profiles  # noqa: E402
from core.errors import InvalidParameter  # noqa: E402

#: 這一版所有合法的 tone id。**刻意寫死在測試裡**，不從 `REPLY_TONES` 讀——
#: 從被測模組讀等於用它自己驗自己，刪掉一個 tone 測試照樣全綠。
EXPECTED_TONES = (
    "natural",
    "professional",
    "concise",
    "friendly",
    "engineer",
    "soft",
    "assertive",
    "custom",
)

#: `tone_options()` 每一筆該有的 key，**恰好**這四個。
EXPECTED_OPTION_KEYS = {"id", "label", "description", "example"}


class TestValidateTone(unittest.TestCase):
    """`validate_tone()` 是 tone 進入系統的唯一入口，放行錯的值等於下游 KeyError。"""

    def test_every_shipped_tone_passes(self):
        for tone in EXPECTED_TONES:
            with self.subTest(tone=tone):
                self.assertEqual(reply_profiles.validate_tone(tone), tone)

    def test_none_falls_back_to_the_default(self):
        """`None` 是「沒有偏好」，不是錯誤——擋下來會讓沒設定的人產不了草稿。"""
        self.assertEqual(reply_profiles.validate_tone(None), reply_profiles.REPLY_TONE_DEFAULT)

    def test_empty_string_is_treated_as_no_preference(self):
        """DB 的偏好欄位可能存空字串（不是 NULL），那要與 `None` 同樣處理。"""
        self.assertEqual(reply_profiles.validate_tone(""), reply_profiles.REPLY_TONE_DEFAULT)

    def test_the_default_is_natural(self):
        """預設值換掉是行為變更，要有測試逼人正面面對，不能順手改。"""
        self.assertEqual(reply_profiles.REPLY_TONE_DEFAULT, "natural")

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(reply_profiles.validate_tone("  engineer  "), "engineer")

    def test_an_unknown_tone_is_rejected(self):
        with self.assertRaises(InvalidParameter):
            reply_profiles.validate_tone("sarcastic")

    def test_the_error_message_lists_every_legal_value(self):
        """錯誤訊息只寫「不合法」的話，呼叫端要翻原始碼才知道能填什麼。"""
        with self.assertRaises(InvalidParameter) as caught:
            reply_profiles.validate_tone("sarcastic")
        for tone in EXPECTED_TONES:
            self.assertIn(tone, caught.exception.message)

    def test_the_error_message_echoes_what_was_received(self):
        with self.assertRaises(InvalidParameter) as caught:
            reply_profiles.validate_tone("sarcastic")
        self.assertIn("sarcastic", caught.exception.message)


class TestToneOptionsDoNotLeakThePrompt(unittest.TestCase):
    """`tone_options()` 會整包送到瀏覽器，多送一個欄位就是多洩漏一份 prompt。"""

    def test_every_tone_is_offered(self):
        ids = [item["id"] for item in reply_profiles.tone_options()]
        self.assertEqual(ids, list(EXPECTED_TONES))

    def test_each_option_has_exactly_the_four_public_keys(self):
        for item in reply_profiles.tone_options():
            with self.subTest(tone=item["id"]):
                self.assertEqual(set(item), EXPECTED_OPTION_KEYS)

    def test_the_prompt_fragment_never_reaches_the_frontend(self):
        """這是刻意的設計，不是漏寫——沒有測試守住，下一個人會「順手補上」。"""
        for item in reply_profiles.tone_options():
            with self.subTest(tone=item["id"]):
                self.assertNotIn("instruction", item)

    def test_labels_and_examples_are_non_empty(self):
        """空字串的選項在 UI 上是一列看不見的空白，使用者只會覺得選單壞了。"""
        for item in reply_profiles.tone_options():
            with self.subTest(tone=item["id"]):
                self.assertTrue(item["label"].strip())
                self.assertTrue(item["description"].strip())
                self.assertTrue(item["example"].strip())


class TestToneLabel(unittest.TestCase):
    """顯示路徑上拋錯 = 一筆歷史草稿的舊 tone 值讓整頁掛掉。"""

    def test_known_ids_map_to_chinese_labels(self):
        self.assertEqual(reply_profiles.tone_label("engineer"), "工程師協作")
        self.assertEqual(reply_profiles.tone_label("professional"), "專業正式")

    def test_an_unknown_id_is_echoed_back_instead_of_raising(self):
        """舊草稿可能存著已被移除的 tone id，顯示原字串比爆掉好。"""
        self.assertEqual(reply_profiles.tone_label("retired_tone"), "retired_tone")

    def test_none_becomes_an_empty_string(self):
        self.assertEqual(reply_profiles.tone_label(None), "")
        self.assertEqual(reply_profiles.tone_label(""), "")


class TestToneInstructionsAreAllDifferent(unittest.TestCase):
    """複製貼上忘了改 instruction，UI 有 8 個選項、實際只有 7 種語氣。"""

    def test_every_tone_has_a_non_empty_instruction(self):
        for tone in EXPECTED_TONES:
            with self.subTest(tone=tone):
                self.assertTrue(reply_profiles.tone_instruction(tone).strip())

    def test_no_two_tones_share_the_same_instruction(self):
        seen = {}
        for tone in EXPECTED_TONES:
            text = reply_profiles.tone_instruction(tone)
            self.assertNotIn(
                text, seen, f"{tone} 與 {seen.get(text)} 的 instruction 完全相同"
            )
            seen[text] = tone

    def test_no_two_tones_share_the_same_label(self):
        """標籤重複時使用者在下拉選單看到兩個一樣的字，根本無從選起。"""
        labels = [item["label"] for item in reply_profiles.tone_options()]
        self.assertEqual(len(labels), len(set(labels)))


class TestSingleSourceOfTruth(unittest.TestCase):
    """`REPLY_TONES` 驗證、`_TONES` 查表——兩邊漂移就是產草稿到一半 KeyError。"""

    def test_the_public_tuple_matches_the_internal_table(self):
        self.assertEqual(tuple(reply_profiles.REPLY_TONES), tuple(reply_profiles._TONES))

    def test_the_public_tuple_matches_what_this_version_ships(self):
        self.assertEqual(tuple(reply_profiles.REPLY_TONES), EXPECTED_TONES)

    def test_everything_validate_tone_accepts_has_an_instruction(self):
        """`validate_tone` 放行的每一個值，`tone_instruction` 都查得到。"""
        for tone in reply_profiles.REPLY_TONES:
            with self.subTest(tone=tone):
                self.assertTrue(reply_profiles.tone_instruction(reply_profiles.validate_tone(tone)))

    def test_the_default_tone_is_itself_a_legal_tone(self):
        self.assertIn(reply_profiles.REPLY_TONE_DEFAULT, reply_profiles.REPLY_TONES)


def options(**kwargs):
    """建一個 `ReplyGenerationOptions`，預設是「什麼都沒選」。"""
    base = dict(tone=None, custom_prompt=None, persona=None)
    base.update(kwargs)
    return reply_profiles.ReplyGenerationOptions(**base)


class FakePersona:
    """`PersonaProfileLike` 的最小替身。

    刻意不 import 真的 `PersonaProfile`：`reply_profiles` 對 persona 的唯一
    要求就是 `name` 與 `to_prompt_dict()`，測試若綁定真型別，
    等於把「這兩個模組互不依賴」這個刻意的設計偷偷破壞掉。
    """

    name = "測試人物"

    def to_prompt_dict(self):
        return {"name": self.name}


class TestIsEmptyGuardsBackwardCompatibility(unittest.TestCase):
    """`is_empty()` 是「prompt 要不要加風格區塊」的唯一開關。"""

    def test_nothing_selected_is_empty(self):
        self.assertTrue(options().is_empty())

    def test_the_bare_constructor_is_empty(self):
        """`ReplyGenerationOptions()` 就是「這個功能存在之前」的狀態。"""
        self.assertTrue(reply_profiles.ReplyGenerationOptions().is_empty())

    def test_a_tone_alone_is_enough(self):
        self.assertFalse(options(tone="engineer").is_empty())

    def test_a_persona_alone_is_enough(self):
        self.assertFalse(options(persona=FakePersona()).is_empty())

    def test_a_custom_prompt_alone_is_enough(self):
        self.assertFalse(options(custom_prompt="請寫短一點").is_empty())

    def test_a_whitespace_only_custom_prompt_counts_as_nothing(self):
        """使用者在輸入框按了幾下空白鍵，不該因此觸發整個風格區塊。"""
        self.assertTrue(options(custom_prompt="   ").is_empty())
        self.assertTrue(options(custom_prompt="\n\t ").is_empty())
        self.assertTrue(options(custom_prompt="").is_empty())

    def test_all_three_together_is_not_empty(self):
        self.assertFalse(
            options(
                tone="soft", custom_prompt="請寫短一點", persona=FakePersona()
            ).is_empty()
        )

    def test_the_options_object_is_immutable(self):
        """frozen dataclass：解析完就定案，組裝階段不該還能改設定。"""
        with self.assertRaises(Exception):
            options().tone = "engineer"


if __name__ == "__main__":
    unittest.main(verbosity=2)
