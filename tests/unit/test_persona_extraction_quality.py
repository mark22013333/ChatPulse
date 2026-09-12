"""抽取**品質**的迴歸守衛：拿真實的第三方 persona 檔案跑 `normalize_persona()`。

## 這一套跟 `test_persona_normalize.py` 守的不是同一件事

那一套守**安全**（指令不可以漏進 prompt）與**契約**（欄位形狀、上限、
`to_prompt_dict()` 不含 boundaries），用的是手寫的最小樣本。它全綠不代表抽出來
的東西有用——2026-09-12 之前它就是全綠的，而當時 14 份真實人物 skill 抽出來的
`thinking_style` 有三分之一是調研佐證，其中一份的六條全部來自同一個心智模型。

**「過得了淨化」與「抽得到東西」是兩件事，這個檔案守後者。**

## 三條驗收條件

每一條都對應 2026-09-12 找到的一個具體缺陷，而且都是機械可驗的——不是
「人看了覺得像樣」：

1. **`thinking_style` 至少涵蓋 4 棵不同的子樹。**
   缺陷：`_MAX_ITEMS_PER_SECTION` 算的單位是章節，而一個心智模型底下有五六個
   子章節，`模型1` 一個人就吃滿整個欄位。實測 feynman 的 `explain_extraction()`
   回報裡，模型 2 到模型 5 每一段都是 `kept_items: 0`。
2. **沒有任何條目是區塊引言或出處署名。**
   缺陷：`_clean_item()` 只擋 `#` 開頭，於是 `> "You can know the name of that
   bird…"` 與 `> —— 费曼复述父亲的教导` 排在 `thinking_style` 的第一、二條。
3. **沒有任何條目被截斷在句中。**
   缺陷：佐證章節裡的逐字稿長度遠超 `_MAX_ITEM_CHARS`，截斷後留下
   `WWDC 1997: "People think focus means saying yes to the thing you've got to
   focus on. But that's not what it means at all`——半句話。

第 3 條用「長度剛好等於上限」當判準。這不完美（正常句子也可能剛好 120 字），
但方向是對的：這批語料裡長度等於上限的條目，實測**全部**是被截斷的佐證。
真的出現一條 120 字的正常風格描述時，該做的是把它加進下面的白名單並寫明理由，
而不是把這條測試拿掉。

語料的出處、授權與釘版見 `fixtures/personas/SOURCES.md`。
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import personas  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "personas"

#: 三份語料各自代表一種形態，理由見 SOURCES.md。
SAMPLES = ("feynman-skill", "mrbeast-skill", "zhangxuefeng-skill")

#: 進 prompt 的三個清單型欄位（`boundaries` 不進 prompt，見 `to_prompt_dict()`）。
PROMPT_FIELDS = ("thinking_style", "communication_style", "avoid")


def load(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def subtrees_of(raw: str, field: str) -> set:
    """這個欄位的條目來自幾棵不同的子樹。

    走 `explain_extraction()` 而不是自己數，因為它與 `normalize_persona()`
    共用同一個 `_collect()`——回報的數字與 profile 裡的條目保證一致。
    """
    info = personas.explain_extraction(raw)
    return {
        # `used_sections` 記的路徑是「祖先 / … / 葉」，`_subtree_key()` 要的是
        # 反過來的鏈（最深優先）。
        personas._subtree_key(used["section"].split(" / ")[::-1])
        for used in info["used_sections"]
        if used["field"] == field and used["kept_items"]
    }


class SampleFilesExistTest(unittest.TestCase):
    """正對照：語料不見了要當場失敗，不可以讓下面每一條都「零條目、通過」。"""

    def test_every_sample_is_present_and_substantial(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                path = FIXTURES / f"{name}.md"
                self.assertTrue(path.exists(), f"缺少語料 {path}")
                # 字元數，不是位元組數——這批是中文，一字 3 bytes。
                # 最小的一份（zhangxuefeng）約 10,000 字。
                self.assertGreater(len(load(name)), 8_000)

    def test_every_sample_still_produces_a_usable_profile(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                profile = personas.normalize_persona(load(name), name_hint=name)
                self.assertTrue(profile.is_usable())


class ThinkingStyleCoversMultipleModelsTest(unittest.TestCase):
    """驗收條件 1：不可以只抽到第一個心智模型。"""

    #: 四棵子樹＝至少四個心智模型有代表。這批語料每份都有 5–6 個模型，
    #: 門檻設在 4 留一點餘裕給結構不同的來源。
    MIN_SUBTREES = 4

    def test_thinking_style_draws_from_at_least_four_subtrees(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                found = subtrees_of(load(name), "thinking_style")
                self.assertGreaterEqual(
                    len(found),
                    self.MIN_SUBTREES,
                    f"{name} 的 thinking_style 只來自 {len(found)} 棵子樹：{sorted(found)}",
                )

    def test_the_per_subtree_quota_is_what_makes_that_possible(self):
        """漂移守衛：有人把子樹配額拿掉時，上面那條要說得出原因。"""
        self.assertEqual(personas._MAX_ITEMS_PER_SUBTREE, 2)

    def test_two_subtrees_with_the_same_title_do_not_share_a_quota(self):
        """子樹的識別要靠整條路徑，不能只靠標題。

        只用標題當 key 的話，同一個欄位底下兩棵不同的子樹只要恰好同名就會
        共用配額，第二棵一條都抽不到——而那正是這個配額要解決的問題本身。
        `## 核心心智模型` 與 `## 決策啟發式` 底下都叫 `### 模型一` 很常見。
        """
        doc = (
            "# 人格\n\n"
            "## 核心心智模型\n\n### 模型一\n"
            "- 心智模型這棵子樹的第一條描述，長度足夠\n"
            "- 心智模型這棵子樹的第二條描述，長度足夠\n\n"
            "## 決策啟發式\n\n### 模型一\n"
            "- 決策啟發式這棵子樹的第一條描述，長度足夠\n"
            "- 決策啟發式這棵子樹的第二條描述，長度足夠\n"
        )
        profile = personas.normalize_persona(doc, name_hint="t")
        self.assertEqual(len(profile.thinking_style), 4)
        self.assertTrue(
            any("決策啟發式" in item for item in profile.thinking_style),
            "第二棵同名子樹一條都沒抽到，配額被第一棵吃光了",
        )

    def test_the_same_holds_when_the_shared_title_is_the_allowlist_hit(self):
        """另一種塌陷形態：同名的那一層**自己**就是命中 allowlist 的那層。

        上一條是「命中層在祖先、同名的是它的子節點」；這一條是三個不同的
        模型底下各有一個 `#### 思考框架`，而 `思考框架` 自己命中 allowlist。
        兩種形態的 key 算法不同（`index > 0` 與否），要分開守。
        """
        doc = "# 人格\n\n" + "".join(
            f"## 模型{c}\n\n### 思考框架\n"
            f"- 模型{c}的第一條描述，長度足夠不會被丟掉\n"
            f"- 模型{c}的第二條描述，長度足夠不會被丟掉\n\n"
            for c in "ABC"
        )
        profile = personas.normalize_persona(doc, name_hint="t")
        self.assertEqual(len(profile.thinking_style), 6)


class NoQuotationsSurviveTest(unittest.TestCase):
    """驗收條件 2：引文與出處署名不是風格描述。"""

    def test_no_item_is_a_blockquote_or_an_attribution(self):
        for name in SAMPLES:
            profile = personas.normalize_persona(load(name), name_hint=name)
            for field in PROMPT_FIELDS:
                for item in getattr(profile, field):
                    with self.subTest(sample=name, field=field, item=item[:24]):
                        self.assertFalse(item.startswith(">"), "區塊引言殘留")
                        self.assertFalse(item.startswith("——"), "出處署名殘留")

    def test_the_sample_really_does_contain_blockquotes(self):
        """正對照。沒有這一條，上面那條在「語料裡本來就沒有引言」時也會綠。"""
        raw = load("feynman-skill")
        self.assertTrue(
            any(line.lstrip().startswith(">") for line in raw.splitlines()),
            "feynman 語料裡應該有區塊引言，否則上面那條測試沒有在測東西",
        )


class NoSectionPreambleSurvivesTest(unittest.TestCase):
    """章節引言句（「…遵循以下风格规则：」）不是風格描述。

    這類句子原本排在 `communication_style` 的第一條，擠掉一個真正的條目——
    它是使用者最先看到、也最先被模型讀到的那一條。
    """

    def test_no_item_announces_what_follows(self):
        for name in SAMPLES:
            profile = personas.normalize_persona(load(name), name_hint=name)
            for field in PROMPT_FIELDS:
                for item in getattr(profile, field):
                    with self.subTest(sample=name, field=field, item=item[:24]):
                        self.assertNotIn("遵循以下", item)
                        self.assertFalse(item.endswith(("：", ":")))

    def test_the_sample_really_does_contain_such_a_preamble(self):
        """正對照。沒有這一條，上面那條在「語料裡本來就沒有引言」時也會綠。"""
        self.assertIn("遵循以下风格规则：", load("feynman-skill"))


class NoItemIsCutMidSentenceTest(unittest.TestCase):
    """驗收條件 3：條目不可以停在半句話。"""

    def test_no_item_sits_exactly_on_the_truncation_limit(self):
        for name in SAMPLES:
            profile = personas.normalize_persona(load(name), name_hint=name)
            for field in PROMPT_FIELDS:
                for item in getattr(profile, field):
                    with self.subTest(sample=name, field=field, item=item[:24]):
                        self.assertNotEqual(
                            len(item),
                            personas._MAX_ITEM_CHARS,
                            "長度剛好等於上限，幾乎一定是被截斷的佐證",
                        )


class EveryRealSampleGetsADescriptionTest(unittest.TestCase):
    """簡介是使用者在設定頁挑 persona 時唯一的辨識線索，不可以是空的。

    `mrbeast-skill` 正是實測中簡介被清空的那一類：整段 frontmatter 命中
    `impersonation`，而它乾淨的第一句跟著一起被丟掉。
    """

    def test_no_sample_ends_up_without_a_description(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                profile = personas.normalize_persona(load(name), name_hint=name)
                self.assertTrue(profile.description.strip(), "簡介是空的")

    def test_the_mrbeast_frontmatter_really_is_the_hostile_shape(self):
        """正對照。整段命中指令特徵、但第一句乾淨——這才是這條測試要守的形態。"""
        fields, _body = personas._parse_frontmatter(load("mrbeast-skill"))
        raw_description = fields.get("description", "")
        self.assertIsNotNone(personas._is_instruction_like(raw_description))
        self.assertTrue(len(raw_description) > 200)


class AvoidIsNotSilentlyEmptyTest(unittest.TestCase):
    """`avoid` 是唯一直接約束輸出的欄位——它空著不會報錯，只會悄悄失效。

    三份語料都有 `### 我拒绝的` 或 `### 拒绝` 這種章節，所以三份都該抽得到。
    """

    def test_every_sample_produces_at_least_one_avoid_item(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                profile = personas.normalize_persona(load(name), name_hint=name)
                self.assertTrue(profile.avoid, "avoid 是空的")

    def test_pursuit_sections_never_leak_into_avoid(self):
        """反向守衛：`拒绝` 的兄弟章節是「要追求的東西」，不可以一起被收進來。"""
        for name in SAMPLES:
            profile = personas.normalize_persona(load(name), name_hint=name)
            for item in profile.avoid:
                with self.subTest(sample=name, item=item[:24]):
                    # 這批語料的 `追求` 章節都以價值觀名詞起頭（诚实／好奇心／
                    # 独立／简洁），避開的東西不會長這樣。
                    self.assertFalse(item.startswith(("诚实", "好奇心", "独立", "简洁")))


class FieldBudgetsHoldOnRealFilesTest(unittest.TestCase):
    """條數放寬到 8 之後，**總表達量**在真實檔案上仍然沒有變大。"""

    def test_no_field_exceeds_its_character_budget(self):
        for name in SAMPLES:
            profile = personas.normalize_persona(load(name), name_hint=name)
            for field in PROMPT_FIELDS + ("boundaries",):
                with self.subTest(sample=name, field=field):
                    total = sum(len(item) for item in getattr(profile, field))
                    self.assertLessEqual(total, personas._MAX_FIELD_CHARS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
