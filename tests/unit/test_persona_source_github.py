"""`core/persona_sources/github.py` 的「找不到 persona 時要指路」單元測試。

**這一套守的是一個 discoverability 缺口，不是一個崩潰。**

Repository 模式試五條候選路徑，全部 404 時原本只回「試過的路徑：…」。那對
「slug 打錯」有用，對真正最常見的情況卻完全沒用：**生態裡的多數 repo 是
「一個 repo 一個 persona、SKILL.md 放在根目錄」**（2026-09-11 實測
`jangviktor-web/zeng-shiqiang`、`superj0107/kaishengwang-perspective`、
`zhuoshu-perspective`、`chenboling-perspective`、`shugui-perspective` 五個
都是），而那種形態在 Repository 模式下**永遠**找不到——根目錄不算 persona
是刻意的（見 `_LISTING_RE` 上方的註解）。使用者盯著五條路徑看不出「該換模式」。

所以失敗時多花一次 trees 呼叫把話講完。下面守四件事：

  1. 有 slug 就列出來，而且措辭**不可以**是「可以匯入」——`_LISTING_RE` 只
     證明路徑形狀符合。實測 `anthropics/skills` 有 19 個
     `skills/<名稱>/SKILL.md`，全部都不是 persona。說「可以匯入」會把人
     送去撞 409。
  2. 只有根目錄檔案時要說「改用網址模式」，而且**建議的網址用 commit SHA**。
     實測 zeng-shiqiang 的預設分支叫 `auto-optimize/20260825-…`，**含斜線**，
     而網址模式按 `/` 切段解析，含斜線的 ref 結構上表達不出來、填了必然 404。
  3. 樹被截斷時要說清單可能不完整（原本的程式碼有個 `pass` 與一句「照實說」
     的註解，但實際上什麼都沒說）。
  4. 列舉失敗（rate limit、格式怪）**不可以蓋掉原本的錯誤**。

全部用 mock，不打網路。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import PersonaSourceError  # noqa: E402
from core.persona_sources.github import GitHubPersonaSource  # noqa: E402

OWNER = "someone"
REPO = "some-repo"
SHA = "60f381a9e6d8c77443c74b8672d1e766be3924e2"
SLUG = "zengshiqiang"
ATTEMPTED = [
    "personas/zengshiqiang/SKILL.md",
    "personas/zengshiqiang/PERSONA.md",
    "personas/zengshiqiang.md",
    "skills/zengshiqiang/SKILL.md",
    "zengshiqiang/SKILL.md",
]


def blob(path: str) -> dict:
    return {"type": "blob", "path": path, "size": 100}


class MissingPersonaErrorTest(unittest.TestCase):
    def message(self, blobs, truncated=False, raises=None) -> str:
        source = GitHubPersonaSource()
        if raises is not None:
            patch = mock.patch.object(GitHubPersonaSource, "_tree_blobs", side_effect=raises)
        else:
            patch = mock.patch.object(
                GitHubPersonaSource, "_tree_blobs", return_value=(blobs, truncated)
            )
        with patch:
            err = source._missing_persona_error(OWNER, REPO, SHA, SLUG, ATTEMPTED)
        self.assertIsInstance(err, PersonaSourceError)
        return str(err)

    # ---------------------------------------------------------------- 1

    def test_available_slugs_are_listed(self):
        msg = self.message([blob("personas/luozhenyu/SKILL.md"), blob("personas/xiangshuai/SKILL.md")])
        self.assertIn("luozhenyu", msg)
        self.assertIn("xiangshuai", msg)

    def test_it_does_not_claim_those_slugs_are_importable_personas(self):
        """措辭守衛：路徑形狀符合 ≠ 那是一份 persona。

        實測 `anthropics/skills` 的 19 個 skill 全部 `is_usable() == False`。
        說「可以匯入」是超出證據範圍的斷言，而代價是使用者照著去撞 409。
        """
        msg = self.message([blob("skills/docx/SKILL.md"), blob("skills/pdf/SKILL.md")])
        self.assertIn("形狀符合", msg)
        self.assertNotIn("可以匯入的是", msg)

    def test_long_lists_are_truncated_but_the_total_is_stated(self):
        msg = self.message([blob(f"personas/p{i}/SKILL.md") for i in range(19)])
        self.assertIn("共 19 個", msg)
        # 只列前 8 個，不要把 19 個名字全灌進一句錯誤訊息
        self.assertNotIn("p12", msg)

    # ---------------------------------------------------------------- 2

    def test_root_level_repo_is_told_to_switch_to_url_mode(self):
        msg = self.message([blob("SKILL.md"), blob("README.md")])
        self.assertIn("網址", msg)
        self.assertIn("根目錄", msg)

    def test_the_suggested_url_pins_the_commit_sha_not_a_branch(self):
        """**這條是重點。** 含斜線的分支名在網址模式裡表達不出來。

        zeng-shiqiang 的預設分支是 `auto-optimize/20260825-…`：網址模式按
        `/` 切段，把它填進 ref 那一段會被切壞、必然 404。用 SHA 沒有這個
        問題，而且順便把版本固定住。
        """
        msg = self.message([blob("SKILL.md")])
        self.assertIn(f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{SHA}/SKILL.md", msg)

    def test_no_url_is_suggested_when_there_is_no_root_file(self):
        """正對照：沒看到根目錄檔案就不要憑猜測給一個會 404 的連結。"""
        msg = self.message([blob("README.md"), blob("docs/guide.md")])
        self.assertNotIn("raw.githubusercontent.com", msg)
        self.assertIn("找不到任何 persona 檔案", msg)

    def test_personas_take_priority_over_a_root_file(self):
        """兩者都有時講 slug——那是使用者本來就在用的模式，不必叫他換。"""
        msg = self.message([blob("SKILL.md"), blob("personas/luozhenyu/SKILL.md")])
        self.assertIn("luozhenyu", msg)
        self.assertNotIn("改用", msg)

    # ---------------------------------------------------------------- 3

    def test_a_truncated_tree_says_the_list_may_be_incomplete(self):
        msg = self.message([blob("personas/luozhenyu/SKILL.md")], truncated=True)
        self.assertIn("可能不完整", msg)

    def test_an_untruncated_tree_does_not_say_that(self):
        """正對照，不然「永遠都說可能不完整」也會讓上面那條通過。"""
        msg = self.message([blob("personas/luozhenyu/SKILL.md")], truncated=False)
        self.assertNotIn("可能不完整", msg)

    # ---------------------------------------------------------------- 4

    def test_a_failing_tree_call_never_masks_the_original_error(self):
        """指路是加分。拿不到就退回原本那句，不可以變成「列舉失敗」。"""
        msg = self.message(None, raises=PersonaSourceError("GitHub API rate limit exceeded"))
        self.assertIn(SLUG, msg)
        self.assertIn("試過的路徑", msg)
        self.assertNotIn("rate limit", msg)

    def test_the_base_message_is_always_present(self):
        """每一種分支都要保留「找不到哪個 slug、試過哪些路徑」。"""
        for blobs in ([blob("personas/a/SKILL.md")], [blob("SKILL.md")], [blob("README.md")]):
            with self.subTest(blobs=[b["path"] for b in blobs]):
                msg = self.message(blobs)
                self.assertIn(SLUG, msg)
                self.assertIn("試過的路徑", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
