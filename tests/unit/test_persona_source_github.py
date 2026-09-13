"""`core/persona_sources/github.py` 的「找不到 persona 時要指路」單元測試。

**這一套守的是一個 discoverability 缺口，不是一個崩潰。**

Repository 模式試五條候選路徑，全部 404 時原本只回「試過的路徑：…」。那對
「slug 打錯」有用，對真正最常見的情況卻完全沒用：**生態裡的多數 repo 是
「一個 repo 一個 persona、SKILL.md 放在根目錄」**（2026-09-11 實測
`jangviktor-web/zeng-shiqiang`、`superj0107/kaishengwang-perspective`、
`zhuoshu-perspective`、`chenboling-perspective`、`shugui-perspective` 五個都是；
2026-09-12 再測 `alchaincyf` 的 14 個人物 skill repo，也全部是）。

這個檔案有兩套測試，守的是同一個缺口的兩個階段：

* `SinglePersonaRepoTest`（2026-09-12 新增）——**那種 repo 現在直接匯入**。
  這是主要路徑。
* `MissingPersonaErrorTest`——兩種結構都沒有、或 slug 真的打錯時，錯誤訊息
  要指路。這是退而求其次的路徑，守三件事：

  1. 有 slug 就列出來，而且措辭**不可以**是「可以匯入」——`_LISTING_RE` 只
     證明路徑形狀符合。實測 `anthropics/skills` 有 19 個
     `skills/<名稱>/SKILL.md`，全部都不是 persona。說「可以匯入」會把人
     送去撞 409。
  2. 樹被截斷時要說清單可能不完整（原本的程式碼有個 `pass` 與一句「照實說」
     的註解，但實際上什麼都沒說）。
  3. 列舉失敗（rate limit、格式怪）**不可以蓋掉原本的錯誤**。

原本還有兩條「只有根目錄檔案時要說改用網址模式」的測試，2026-09-12 移除：
那個分支走不到了，能匯就不該叫使用者換模式。

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

    def test_a_repo_with_nothing_says_so_plainly(self):
        """兩種結構都沒有時，要講清楚是「這個 repo 裡沒有」而不是「slug 打錯」。

        （原本這裡還斷言訊息不含 raw 網址。2026-09-12 產生網址的分支整段移除
        之後，那條斷言永遠成立、測不到任何東西，所以拿掉。）
        """
        msg = self.message([blob("README.md"), blob("docs/guide.md")])
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


#: 一份剛好能過淨化的最小 persona。內容不重要，重要的是它被取回來了。
ROOT_SKILL_MD = "---\nname: feynman-perspective\n---\n\n## 心智模型\n\n- 先問這個說法的前提是什麼\n"


class SinglePersonaRepoTest(unittest.TestCase):
    """「一個 repo 一個 persona、SKILL.md 放根目錄」要能用 Repository 模式匯入。

    這是生態裡的主流長相：2026-09-12 實測 `alchaincyf` 的 14 個人物 skill
    repo 全部如此。改版前它們在 Repository 模式下**永遠**找不到，只能改用
    網址模式手打 raw 網址——而那條路徑沒有預設分支解析（14 個裡有 7 個的
    預設分支是 `master` 不是 `main`，照 UI placeholder 填必然 404），
    也沒有 commit SHA 釘版。

    下面同時守住反向：`fxp/persona-distill-skills` 的根目錄 `SKILL.md` 是
    「如何蒸餾 persona」的方法論而不是 persona，那個 repo 另有
    `personas/<名稱>/`，**不可以**因為這次放寬而被當成 persona 收進來。
    """

    def source_with(self, blobs, root_body=ROOT_SKILL_MD):
        """把 GitHub 換成假的：五條候選路徑一律 404，根目錄檔案有內容。"""
        def fake_get(url, accept=None):
            if url.endswith("/SKILL.md") and f"/{SHA}/SKILL.md" in url:
                return root_body, url
            raise PersonaSourceError(f"404 Not Found: {url}")

        source = GitHubPersonaSource()
        return source, mock.patch.multiple(
            "core.persona_sources.github",
            safe_get=mock.Mock(side_effect=fake_get),
        ), mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(return_value=(blobs, False)),
        )

    def fetch(self, blobs, slug=SLUG):
        source, patch_mod, patch_cls = self.source_with(blobs)
        with patch_mod, patch_cls:
            return source._fetch_by_path(f"{OWNER}/{REPO}", slug, None)

    # ---------------------------------------------------------------- 匯入

    def test_a_root_level_skill_is_imported_instead_of_erroring(self):
        fetched = self.fetch([blob("SKILL.md"), blob("README.md")])
        self.assertIn("心智模型", fetched.raw_text)
        self.assertEqual(fetched.extra["path"], "SKILL.md")

    def test_the_import_is_pinned_to_a_commit_sha(self):
        """Repository 模式的價值就在這裡——網址模式沒有這一層。"""
        fetched = self.fetch([blob("SKILL.md")])
        self.assertEqual(fetched.source_commit_sha, SHA)
        self.assertEqual(fetched.source_repository, f"{OWNER}/{REPO}")

    def test_the_slug_does_not_have_to_match(self):
        """這種 repo 只有一份 persona，沒有第二個候選可以選錯。

        要求 slug 對得上只會讓「跟 repo 名差一個字」變成一個使用者看不出
        該怎麼修的 404。填過的字仍然記在 provenance 裡。
        """
        fetched = self.fetch([blob("SKILL.md")], slug="whatever")
        self.assertIn("心智模型", fetched.raw_text)
        self.assertEqual(fetched.extra["requested_slug"], "whatever")

    def test_the_name_hint_comes_from_the_repo_name(self):
        """沒有目錄層可以當名字，只剩 repo 名。"""
        fetched = self.fetch([blob("SKILL.md")])
        self.assertEqual(fetched.name_hint, "some-repo")

    # ---------------------------------------------------------------- 反向

    def test_a_repo_with_personas_dirs_never_falls_back_to_the_root_file(self):
        """`fxp/persona-distill-skills` 的實測案例：根目錄那份是方法論。"""
        with self.assertRaises(PersonaSourceError) as caught:
            self.fetch([blob("SKILL.md"), blob("personas/luozhenyu/SKILL.md")])
        self.assertIn("luozhenyu", str(caught.exception))

    def test_a_truncated_tree_never_falls_back_to_the_root_file(self):
        """「這個 repo 沒有 personas/ 結構」是全稱斷言，截斷的樹證明不了它。

        大型 repo 的 `personas/` 目錄可能整個落在截斷之外。那時放行等於把
        `fxp/persona-distill-skills` 那個反例重新打開——匯進一份方法論。
        寧可退回錯誤訊息讓使用者自己指定。
        """
        source, patch_mod, _ = self.source_with([blob("SKILL.md")])
        with patch_mod, mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(return_value=([blob("SKILL.md")], True)),
        ):
            with self.assertRaises(PersonaSourceError):
                source._fetch_by_path(f"{OWNER}/{REPO}", SLUG, None)

    def test_listing_does_not_invent_one_from_a_truncated_tree_either(self):
        source = GitHubPersonaSource()
        with mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(return_value=([blob("SKILL.md")], True)),
        ):
            self.assertEqual(source.list_personas(repository=f"{OWNER}/{REPO}"), [])

    def test_a_repo_with_neither_still_raises(self):
        with self.assertRaises(PersonaSourceError) as caught:
            self.fetch([blob("README.md"), blob("docs/guide.md")])
        self.assertIn("找不到任何 persona 檔案", str(caught.exception))

    def test_a_failing_tree_call_still_reports_the_attempted_paths(self):
        """樹拿不到時不可以變成「列舉失敗」，要退回原本那句。"""
        source = GitHubPersonaSource()
        with mock.patch(
            "core.persona_sources.github.safe_get",
            side_effect=PersonaSourceError("404 Not Found"),
        ), mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(side_effect=PersonaSourceError("rate limit exceeded")),
        ):
            with self.assertRaises(PersonaSourceError) as caught:
                source._fetch_by_path(f"{OWNER}/{REPO}", SLUG, None)
        self.assertIn("試過的路徑", str(caught.exception))
        self.assertNotIn("rate limit", str(caught.exception))

    # ---------------------------------------------------------------- 列舉

    def test_listing_shows_the_single_persona(self):
        """UI 的「列舉 → 挑一個」流程對這種 repo 也要走得通。

        列不出來的話使用者看到的是空清單，而空清單看起來就像
        「這個 repo 沒東西」——那正是改版前的體驗。
        """
        source = GitHubPersonaSource()
        with mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(return_value=([blob("SKILL.md")], False)),
        ):
            listed = source.list_personas(repository=f"{OWNER}/{REPO}")
        self.assertEqual([item["id"] for item in listed], ["some-repo"])
        self.assertEqual(listed[0]["commit_sha"], SHA)

    def test_listing_is_unchanged_for_a_multi_persona_repo(self):
        """正對照：有 slug 結構時不可以多冒出一筆根目錄的東西。"""
        source = GitHubPersonaSource()
        with mock.patch.multiple(
            GitHubPersonaSource,
            _resolve_commit=mock.Mock(return_value=(SHA, "master")),
            _tree_blobs=mock.Mock(
                return_value=([blob("SKILL.md"), blob("personas/luozhenyu/SKILL.md")], False)
            ),
        ):
            listed = source.list_personas(repository=f"{OWNER}/{REPO}")
        self.assertEqual([item["id"] for item in listed], ["luozhenyu"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
