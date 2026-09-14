"""Draft Worklog 的證據蒐集（`core/worklog_evidence.py`）。

這一套測試守住四件「錯了看不出來」的事：

  1. **日界由 `tz_offset` 切**。同一個 commit 在 `+08:00` 與 `-05:00` 底下
     必須落在**不同的日期**。錯了的話草稿照樣產得出來，只是那天少一筆、
     前一天多一筆，而且沒有任何人會察覺。
  2. **`--since`／`--until` 濾的是 committer date，不是 `%aI`**。所以
     rebase／amend 過的 commit（兩個日期差好幾天）仍然要撈得到——這是
     `WORKLOG_GIT_WINDOW_PADDING_HOURS` 那圈放寬存在的唯一理由。
  3. **subject 可以含任何字元**。分隔符用 `\\x1f`／`\\x1e` 就是為了這個；
     這裡直接餵含分隔符、換行、引號、emoji 的 subject 進解析器。
  4. **Chat 的母體絕不是全部的 Space**，而且不比對發送者就會把別人的
     閒聊算成自己的工時。

Chat 那條路徑一律用假 client，**不打真的 Google API**。
git 那條路徑用暫存目錄裡現建的真 repo，**絕不碰 `data/chatpulse.db`**。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_evidence*.py'
"""

import ast
import math
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import code_search  # noqa: E402
from core import config as cfg  # noqa: E402
from core import worklog_evidence as we  # noqa: E402
from core.errors import ChatApiError, InvalidParameter, WorkRepoUnavailable  # noqa: E402


FS = "\x1f"
RS = "\x1e"


# --------------------------------------------------------------------------
# 時區與日界
# --------------------------------------------------------------------------


class TzOffsetTests(unittest.TestCase):
    def test_accepts_common_forms(self):
        self.assertEqual(we.normalize_tz_offset("+08:00"), "+08:00")
        self.assertEqual(we.normalize_tz_offset("+0800"), "+08:00")
        self.assertEqual(we.normalize_tz_offset("-05:00"), "-05:00")
        self.assertEqual(we.normalize_tz_offset(" +05:30 "), "+05:30")
        self.assertEqual(we.normalize_tz_offset("Z"), "+00:00")
        self.assertEqual(we.normalize_tz_offset("utc"), "+00:00")

    def test_rejects_garbage(self):
        """非法值直接擋，不做寬容解析——寬容解析的結果是工時落在錯的一天。"""
        for bad in ("", None, "Asia/Taipei", "+8", "08:00", "+25:00", "+08:70", "＋08:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidParameter):
                    we.normalize_tz_offset(bad)

    def test_day_bounds_is_half_open(self):
        start, end = we.day_bounds("2026-09-13", "+08:00")
        self.assertEqual(start.isoformat(), "2026-09-13T00:00:00+08:00")
        self.assertEqual(end.isoformat(), "2026-09-14T00:00:00+08:00")
        # 開區間：00:00:00 屬於新的一天，不可以同時算進兩天
        self.assertFalse(we._within(end, start, end))
        self.assertTrue(we._within(start, start, end))

    def test_day_bounds_follows_offset(self):
        start_tw, _ = we.day_bounds("2026-09-13", "+08:00")
        start_ny, _ = we.day_bounds("2026-09-13", "-05:00")
        self.assertNotEqual(start_tw.astimezone(timezone.utc), start_ny.astimezone(timezone.utc))

    def test_bad_work_date_is_rejected(self):
        for bad in ("", "2026/09/13", "13-09-2026", "2026-13-01"):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidParameter):
                    we.day_bounds(bad, "+08:00")

    def test_naive_timestamp_is_never_counted(self):
        """沒有偏移的時間戳不猜歸屬。寧可漏，也不要把工時算在錯的一天。"""
        start, end = we.day_bounds("2026-09-13", "+08:00")
        naive = datetime(2026, 9, 13, 10, 0, 0)
        self.assertFalse(we._within(naive, start, end))
        self.assertFalse(we._within(None, start, end))


# --------------------------------------------------------------------------
# 權重輸入（啟發式，不是科學）
# --------------------------------------------------------------------------


class WeightTests(unittest.TestCase):
    def test_commit_weight_is_log_of_churn(self):
        self.assertAlmostEqual(we.commit_weight_input(10, 5), math.log(16))
        self.assertEqual(we.commit_weight_input(0, 0), 0.0)

    def test_commit_weight_ignores_negative_noise(self):
        self.assertEqual(we.commit_weight_input(-5, 0), 0.0)

    def test_chat_weight_uses_char_count(self):
        self.assertAlmostEqual(we.chat_weight_input(99), math.log(100))

    def test_mention_gets_a_bonus(self):
        plain = we.chat_weight_input(99, False)
        mentioned = we.chat_weight_input(99, True)
        self.assertGreater(mentioned, plain)
        self.assertAlmostEqual(mentioned, plain * cfg.WORKLOG_CHAT_MENTION_BONUS)

    def test_scales_are_comparable(self):
        """兩種來源要放進同一個分配器比大小，就必須在同一把尺上。

        這是相對計畫 §8「chat 用字數」的刻意差異：字數是線性的，一則
        200 字的訊息會壓過一次 50 行的改動，而那顯然不是花比較久的事。
        """
        long_message = we.chat_weight_input(200, False)
        small_commit = we.commit_weight_input(30, 20)
        self.assertLess(abs(long_message - small_commit), 2.0)


# --------------------------------------------------------------------------
# 關鍵字抽取
# --------------------------------------------------------------------------


class TermTests(unittest.TestCase):
    def test_issue_key_comes_first(self):
        terms = we.extract_terms("fix(auth): PEI-133 修掉 token 沒續期的問題")
        self.assertEqual(terms[0], "PEI-133")

    def test_conventional_scope_is_kept(self):
        self.assertIn("worklog", we.extract_terms("feat(worklog): 工時草稿掛進導航"))

    def test_code_shaped_tokens_reuse_code_search(self):
        terms = we.extract_terms("refactor: 把 `list_messages_since` 換成 fetchRecent")
        self.assertIn("list_messages_since", terms)
        self.assertIn("fetchRecent", terms)

    def test_common_stopwords_are_dropped(self):
        terms = we.extract_terms("docs: 更新 說明")
        self.assertNotIn("更新", terms)
        self.assertNotIn("說明", terms)

    def test_cjk_runs_survive_when_there_is_no_ascii(self):
        """這個團隊的 commit subject 多半是中文。只跑 `_IDENT_RE` 會一個詞都抽不到。"""
        terms = we.extract_terms("重構：訂閱系統的推播重試邏輯")
        self.assertTrue(terms, "全中文的 subject 不該抽出空清單")

    def test_cjk_edge_particles_are_trimmed(self):
        self.assertIn("基礎層", we.extract_terms("feat: 作為 Draft Worklog 的基礎層"))

    def test_respects_max_terms(self):
        long_text = " ".join(f"PEI-{i}" for i in range(1, 40))
        self.assertLessEqual(len(we.extract_terms(long_text)), cfg.WORKLOG_MAX_TERMS)


class StandardsReferenceTests(unittest.TestCase):
    """⚠️ 迴歸：`terms[0]` 會原封不動變成送去 ZPlanner 的 `q`。

    2026-09-13 實測：`docs(zplanner): 補 ADR-0008 的降級路徑說明` 的 `terms[0]`
    是 `ADR-0008`，於是那一群的候選清單必然是空的——正是上一輪加黑名單本來要
    防的後果，只是黑名單當時只作用在分群的 `label` 上。
    """

    def test_needs_the_full_issue_key_shape(self):
        self.assertTrue(we.is_standards_reference("ADR-0008"))
        self.assertTrue(we.is_standards_reference("UTF-8"))
        self.assertTrue(we.is_standards_reference("CVE-2024"))
        self.assertFalse(we.is_standards_reference("PEI-133"), "真的單號不可以被誤殺")
        self.assertFalse(we.is_standards_reference("zplanner"))
        self.assertFalse(we.is_standards_reference(""))
        self.assertFalse(
            we.is_standards_reference("照 ADR-0008 改"),
            "只認完整形狀，不是包含就算——不然整句話都會被當成標準編號",
        )

    def test_find_issue_keys_stays_pure(self):
        """形狀的唯一定義不套這個判準，兩個問題分開才不會互相汙染。"""
        self.assertEqual(we.find_issue_keys("ADR-0008"), ["ADR-0008"])

    def test_standards_reference_is_demoted_not_dropped(self):
        terms = we.extract_terms("docs(zplanner): 補 ADR-0008 的降級路徑說明")
        self.assertNotEqual(terms[0], "ADR-0008", "它不可以是送去查候選的那個詞")
        self.assertIn("ADR-0008", terms, "但它仍然是這筆 commit 在講什麼的線索，不要刪")

    def test_a_real_key_still_comes_first(self):
        terms = we.extract_terms("fix: PEI-133 照 ADR-0008 改用 UTF-8")
        self.assertEqual(terms[0], "PEI-133")

    def test_the_blacklist_has_an_escape_hatch(self):
        """誤殺的出口：真的有一個代號叫 `SQL` 的專案時，把前綴拿掉即可。"""
        self.assertTrue(we.is_standards_reference("SQL-1234"), "預設會誤殺，這是已知取捨")
        trimmed = frozenset(cfg.WORKLOG_NON_ISSUE_PREFIXES - {"SQL"})
        with mock.patch.object(cfg, "WORKLOG_NON_ISSUE_PREFIXES", trimmed):
            self.assertFalse(we.is_standards_reference("SQL-1234"))
            self.assertEqual(we.extract_terms("fix: SQL-1234 修好了")[0], "SQL-1234")


# --------------------------------------------------------------------------
# git log 的輸出解析（subject 可以含任何字元）
# --------------------------------------------------------------------------


def _header(sha, short, aiso, email, decorate, subject):
    return FS.join([sha, short, aiso, email, decorate, subject]) + RS


class LogParsingTests(unittest.TestCase):
    SHA_A = "a" * 40
    SHA_B = "b" * 40

    def test_parses_numstat_of_the_previous_record(self):
        """git 的排版是 header、換行、numstat、空行、下一個 header。

        每一塊的內容屬於**上一筆**——這裡錯了的話所有 commit 的增刪行數會
        整排錯位一格，而且看起來完全正常。
        """
        stdout = (
            _header(self.SHA_A, "aaaaaaa", "2026-09-13T10:00:00+08:00", "a@x.com", "", "第一筆")
            + "\n12\t3\tcore/a.py\n0\t7\tcore/b.py\n\n"
            + _header(self.SHA_B, "bbbbbbb", "2026-09-13T11:00:00+08:00", "a@x.com", "", "第二筆")
            + "\n1\t1\tREADME.md\n"
        )
        commits = we._parse_log_output(stdout)
        self.assertEqual([c.subject for c in commits], ["第一筆", "第二筆"])
        self.assertEqual((commits[0].insertions, commits[0].deletions), (12, 10))
        self.assertEqual(commits[0].files_changed, 2)
        self.assertEqual((commits[1].insertions, commits[1].deletions), (1, 1))

    def test_binary_numstat_dashes_do_not_crash(self):
        stdout = (
            _header(self.SHA_A, "aaaaaaa", "2026-09-13T10:00:00+08:00", "a@x.com", "", "加圖")
            + "\n-\t-\tdocs/a.png\n"
        )
        commits = we._parse_log_output(stdout)
        self.assertEqual(commits[0].files_changed, 1)
        self.assertEqual((commits[0].insertions, commits[0].deletions), (0, 0))

    def test_subject_with_field_separator(self):
        """subject 裡有 `\\x1f` 時，多出來的分隔符留在 subject，欄位不許錯位。"""
        subject = f"fix: 欄位{FS}分隔符也要活著"
        stdout = _header(
            self.SHA_A, "aaaaaaa", "2026-09-13T10:00:00+08:00", "a@x.com", "main", subject
        )
        commits = we._parse_log_output(stdout)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].author_email, "a@x.com")
        self.assertEqual(commits[0].decorations, "main")
        self.assertEqual(commits[0].subject, subject)

    def test_subject_with_record_separator(self):
        """subject 裡有 `\\x1e` 時不可以被切成兩筆 commit。"""
        stdout = (
            _header(
                self.SHA_A, "aaaaaaa", "2026-09-13T10:00:00+08:00", "a@x.com", "", f"前半{RS}後半"
            )
            + "\n2\t0\tcore/a.py\n"
        )
        commits = we._parse_log_output(stdout)
        self.assertEqual(len(commits), 1, "不可以被 \\x1e 切成兩筆")
        self.assertEqual(commits[0].subject, f"前半{RS}後半")
        self.assertEqual(commits[0].insertions, 2)

    def test_subject_with_newline_quotes_and_emoji(self):
        subject = 'feat: 「引號」與 "double" 與 emoji 🎉🐛\n第二行'
        stdout = (
            _header(self.SHA_A, "aaaaaaa", "2026-09-13T10:00:00+08:00", "a@x.com", "", subject)
            + "\n5\t5\tcore/a.py\n\n"
            + _header(self.SHA_B, "bbbbbbb", "2026-09-13T11:00:00+08:00", "a@x.com", "", "正常")
        )
        commits = we._parse_log_output(stdout)
        self.assertEqual(len(commits), 2)
        self.assertIn("🎉🐛", commits[0].subject)
        self.assertIn("第二行", commits[0].subject)
        self.assertEqual(commits[0].insertions, 5, "換行不可以吃掉 numstat")
        self.assertEqual(commits[1].subject, "正常")

    def test_empty_output(self):
        self.assertEqual(we._parse_log_output(""), [])

    def test_decorations(self):
        self.assertEqual(
            we._parse_decorations("HEAD -> feature/x, origin/main, tag: v1.2"),
            ("feature/x", "origin/main"),
        )
        self.assertEqual(we._parse_decorations(""), ())


# --------------------------------------------------------------------------
# git log 的 argv
# --------------------------------------------------------------------------


class LogArgsTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = we.day_bounds("2026-09-13", "+08:00")

    def _args(self, emails, branches=()):
        return we._log_args(
            author_emails=emails, branch_filter=branches, since=self.start, until=self.end
        )

    def test_one_author_flag_per_email(self):
        """git 對多個 `--author` 是 OR。少一個就靜默漏掉那個信箱的 commit。"""
        args = self._args(["a@x.com", "b@y.com"])
        self.assertIn("--author=a@x.com", args)
        self.assertIn("--author=b@y.com", args)

    def test_never_reads_diff_content(self):
        args = self._args(["a@x.com"])
        self.assertIn("--numstat", args)
        self.assertIn("--no-merges", args)
        for forbidden in ("-p", "--patch", "-u", "--full-diff"):
            self.assertNotIn(forbidden, args)

    def test_empty_branch_filter_means_all(self):
        """`branch_filter` 空 ＝ 掃 `--all`，不是「不掃」（語意與 code_projects 相反）。"""
        self.assertIn("--all", self._args(["a@x.com"], ()))

    def test_branch_filter_replaces_all(self):
        args = self._args(["a@x.com"], ["main", "feature/x"])
        self.assertNotIn("--all", args)
        self.assertIn("main", args)
        self.assertIn("feature/x", args)
        self.assertEqual(args[-1], "--", "結尾要有 -- 讓分支名不會被當成 pathspec")

    def test_author_patterns_are_fixed_strings(self):
        """信箱裡的 `.` 與 `+` 不可以被當成正規表示式元字元。"""
        args = self._args(["a.b+c@x.com"])
        self.assertIn("--fixed-strings", args)
        self.assertIn("--regexp-ignore-case", args)

    def test_lower_bound_is_padded(self):
        """`--since` 濾的是 committer date，所以下界要比那一天早。"""
        args = self._args(["a@x.com"])
        since = next(a for a in args if a.startswith("--since="))[len("--since=") :]
        self.assertLess(datetime.fromisoformat(since), self.start)

    def test_there_is_no_upper_bound(self):
        """🔴 不可以有 `--until`。

        上界放寬固定時數只覆蓋得了「N 小時內完成的 rebase」，把功能分支擺兩週
        再 rebase 是日常。超過的那些會被 git 直接濾掉，而且 `truncated` 仍是
        `False`、`skipped` 仍是空的——使用者完全看不出那天少了東西。
        這條測試就是釘住那個上界不會被「為了效能」加回來。
        """
        args = self._args(["a@x.com"])
        self.assertEqual([a for a in args if a.startswith("--until")], [])

    def test_scan_limit_is_not_the_per_day_cap(self):
        """`-n` 用的是掃描預算，不是單日保留上限。

        拿掉上界之後視窗是 `[日界 − padding, ∞)`，git log 由新到舊輸出，`-n`
        砍掉的正好是最舊的一端（＝目標日）。用單日上限當 `-n` 等於把預算花在
        目標日**之後**的 commit 上。
        """
        args = self._args(["a@x.com"])
        self.assertIn(f"-n{cfg.WORKLOG_GIT_SCAN_LIMIT}", args)
        self.assertGreater(
            cfg.WORKLOG_GIT_SCAN_LIMIT,
            cfg.WORKLOG_MAX_COMMITS_PER_REPO,
            "掃描預算必須大於單日保留上限，否則回補舊日期一定撈不到",
        )


# --------------------------------------------------------------------------
# 真的 git repo（暫存目錄，不碰任何專案資料）
# --------------------------------------------------------------------------


class TempRepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-worklog-evidence-")
        self.root = self._tmp.name
        self.repo_path = os.path.join(self.root, "repo")
        os.makedirs(self.repo_path)
        self.env = dict(os.environ)
        self.env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": self.root,
                "XDG_CONFIG_HOME": self.root,
                "GIT_AUTHOR_NAME": "測試員",
                "GIT_COMMITTER_NAME": "測試員",
                "GIT_COMMITTER_EMAIL": "committer@example.com",
            }
        )
        self._run("init", "-q", "-b", "main")

    def tearDown(self):
        # 只清自己在 tempfile 裡建的東西，不碰任何專案檔案
        self._tmp.cleanup()

    def _run(self, *args):
        proc = subprocess.run(
            ["git", *args], cwd=self.repo_path, env=self.env, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def _commit(self, subject, *, email, authored, committed=None, lines=1):
        path = os.path.join(self.repo_path, f"f{abs(hash(subject)) % 10000}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x\n" * lines)
        self._run("add", "-A")
        env = dict(self.env)
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_AUTHOR_DATE"] = authored
        env["GIT_COMMITTER_DATE"] = committed or authored
        proc = subprocess.run(
            ["git", "commit", "-q", "-m", subject],
            cwd=self.repo_path,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TimezoneBoundaryTests(TempRepoCase):
    """⚠️ 驗收條件：同一個 commit 在兩種偏移下要落在**不同**日期。"""

    def setUp(self):
        super().setUp()
        # 台北時間 2026-09-13 01:30＝UTC 2026-09-12 17:30＝紐約 2026-09-12 12:30
        self._commit(
            "fix: 半夜一點的 commit", email="me@example.com", authored="2026-09-13T01:30:00+08:00"
        )

    def _collect(self, work_date, tz_offset):
        found, _scan = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="tz",
            author_emails=["me@example.com"],
            work_date=work_date,
            tz_offset=tz_offset,
        )
        return found

    def test_lands_on_the_13th_in_taipei(self):
        self.assertEqual(len(self._collect("2026-09-13", "+08:00")), 1)
        self.assertEqual(len(self._collect("2026-09-12", "+08:00")), 0)

    def test_lands_on_the_12th_in_new_york(self):
        self.assertEqual(len(self._collect("2026-09-12", "-05:00")), 1)
        self.assertEqual(len(self._collect("2026-09-13", "-05:00")), 0)

    def test_occurred_at_is_rendered_in_the_viewers_offset(self):
        taipei = self._collect("2026-09-13", "+08:00")[0]
        new_york = self._collect("2026-09-12", "-05:00")[0]
        self.assertEqual(taipei.sha, new_york.sha, "前置條件：這兩筆是同一個 commit")
        self.assertTrue(taipei.occurred_at.endswith("+08:00"))
        self.assertTrue(new_york.occurred_at.endswith("-05:00"))
        self.assertTrue(taipei.occurred_at.startswith("2026-09-13"))
        self.assertTrue(new_york.occurred_at.startswith("2026-09-12"))


class RepoCollectionTests(TempRepoCase):
    def test_collects_only_the_listed_authors(self):
        self._commit("mine", email="me@example.com", authored="2026-09-13T10:00:00+08:00")
        self._commit("theirs", email="other@example.com", authored="2026-09-13T11:00:00+08:00")
        found, scan = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertEqual([e.title for e in found], ["mine"])
        self.assertEqual(scan.commit_count, 1)
        self.assertTrue(scan.scanned_all_branches)

    def test_author_match_is_case_insensitive(self):
        self._commit("mine", email="Me@Example.com", authored="2026-09-13T10:00:00+08:00")
        found, _ = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertEqual(len(found), 1)

    def test_rebased_commit_is_still_found(self):
        """`--since`／`--until` 濾的是 committer date。

        author date 在 9/13、committer date 在 9/16（rebase 過）的 commit
        仍然要算進 9/13——這是放寬視窗那圈唯一存在的理由，拿掉就會靜默漏掉。
        """
        self._commit(
            "rebase 過的",
            email="me@example.com",
            authored="2026-09-13T14:00:00+08:00",
            committed="2026-09-16T09:00:00+08:00",
        )
        found, _ = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertEqual([e.title for e in found], ["rebase 過的"])

    def test_commit_rebased_two_weeks_later_is_still_found(self):
        """⚠️ 迴歸：committer date 比 author date 晚**兩週**。

        舊寫法前後各放寬 72 小時，只覆蓋得了「3 天內完成的 rebase」。
        2026-09-13 實測：author date 9/01、committer date 9/13 的 commit，
        查 9/01 只撈到 1 筆（實際 2 筆），而且 `truncated=False`、`skipped=[]`
        ——完全無聲。把功能分支擺兩週再 rebase 是日常，不是邊緣情況。
        """
        self._commit(
            "被 rebase 過的",
            email="me@example.com",
            authored="2026-08-18T10:00:00+08:00",
            committed="2026-09-01T10:00:00+08:00",  # 晚了兩週才進主線
        )
        self._commit(
            "當天就 push 的",
            email="me@example.com",
            authored="2026-08-18T11:00:00+08:00",
        )
        found, scan = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-08-18",
            tz_offset="+08:00",
        )
        self.assertEqual([e.title for e in found], ["被 rebase 過的", "當天就 push 的"])
        self.assertFalse(scan.truncated, "全部撈到了就不該亮旗標")

    def _three_commits_on_one_day(self):
        for i in range(3):
            self._commit(
                f"第 {i} 筆",
                email="me@example.com",
                authored=f"2026-09-13T1{i}:00:00+08:00",
            )

    def _collect_one_day(self):
        return we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )

    def test_scan_budget_exhausted_is_flagged_as_truncated(self):
        """掃描預算用完 ⇒ 被砍掉的是最舊的一端（＝目標日），一定要標 truncated。

        沒有這個旗標的話，「漏了」與「那天真的只做這些」長得一模一樣。
        """
        self._three_commits_on_one_day()
        with mock.patch.object(cfg, "WORKLOG_GIT_SCAN_LIMIT", 2):
            found, scan = self._collect_one_day()
        self.assertTrue(scan.truncated)
        self.assertEqual(len(found), 2)

    def test_a_complete_scan_is_not_truncated(self):
        """正對照：預算沒用完就不可以亮旗標，不然旗標等於沒有。"""
        self._three_commits_on_one_day()
        _found, scan = self._collect_one_day()
        self.assertFalse(scan.truncated)
        self.assertEqual(scan.commit_count, 3)

    def test_per_day_cap_truncates_and_flags(self):
        """單日保留上限是另一種「有東西沒進來」，同樣要標 truncated。"""
        self._three_commits_on_one_day()
        with mock.patch.object(cfg, "WORKLOG_MAX_COMMITS_PER_REPO", 2):
            found, scan = self._collect_one_day()
        self.assertEqual(len(found), 2)
        self.assertEqual(scan.commit_count, 2)
        self.assertTrue(scan.truncated)
        # 先排序再截斷：同一天重產兩次要得到同一份草稿
        self.assertEqual([e.title for e in found], ["第 0 筆", "第 1 筆"])

    def test_scans_unmerged_branches_by_default(self):
        """「這天我在哪都做了什麼」——還沒合併的 feature 分支不可以漏掉。"""
        self._commit("在 main", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        self._run("checkout", "-q", "-b", "feature/x")
        self._commit("在 feature", email="me@example.com", authored="2026-09-13T10:00:00+08:00")
        self._run("checkout", "-q", "main")
        found, _ = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertEqual({e.title for e in found}, {"在 main", "在 feature"})

    def test_branch_filter_narrows_the_scan(self):
        self._commit("在 main", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        self._run("checkout", "-q", "-b", "feature/x")
        self._commit("在 feature", email="me@example.com", authored="2026-09-13T10:00:00+08:00")
        self._run("checkout", "-q", "main")
        found, scan = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            branch_filter=["main"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertEqual([e.title for e in found], ["在 main"])
        self.assertFalse(scan.scanned_all_branches)
        self.assertEqual(scan.branches_scanned, ("main",))

    def test_numstat_becomes_weight_input(self):
        self._commit("大改", email="me@example.com", authored="2026-09-13T09:00:00+08:00", lines=40)
        self._commit("小改", email="me@example.com", authored="2026-09-13T10:00:00+08:00", lines=1)
        found, _ = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        by_title = {e.title: e for e in found}
        self.assertEqual(by_title["大改"].insertions, 40)
        self.assertGreater(by_title["大改"].weight_input, by_title["小改"].weight_input)

    def test_ref_is_the_evidence_key(self):
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        found, _ = we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )
        self.assertTrue(found[0].ref.startswith("commit:"))
        self.assertEqual(found[0].ref, f"commit:{found[0].sha}")
        self.assertEqual(found[0].to_meta()["ref"], found[0].ref)

    def test_does_not_touch_working_tree(self):
        """ADR-0006 鐵律：讀 git 一律不碰工作區。"""
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        tracked = os.path.join(self.repo_path, "tracked.txt")
        with open(tracked, "w", encoding="utf-8") as f:
            f.write("v1\n")
        self._run("add", "tracked.txt")
        self._commit("加檔", email="me@example.com", authored="2026-09-13T09:30:00+08:00")
        with open(tracked, "w", encoding="utf-8") as f:
            f.write("未提交的修改\n")
        with open(os.path.join(self.repo_path, "untracked.txt"), "w", encoding="utf-8") as f:
            f.write("還沒 add\n")

        before = self._run("status", "--porcelain").stdout
        head_before = self._run("rev-parse", "HEAD").stdout
        self.assertTrue(before.strip(), "前置條件：工作區要真的是髒的")

        we.collect_repo_commits(
            repo_path=self.repo_path,
            repo_name="r",
            author_emails=["me@example.com"],
            work_date="2026-09-13",
            tz_offset="+08:00",
        )

        self.assertEqual(self._run("status", "--porcelain").stdout, before)
        self.assertEqual(self._run("rev-parse", "HEAD").stdout, head_before)
        with open(tracked, encoding="utf-8") as f:
            self.assertEqual(f.read(), "未提交的修改\n")

    def test_uses_shared_git_conventions(self):
        """git 呼叫沿用 `code_search._git`，不是另外複製一份。"""
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        with mock.patch.object(code_search, "_git", wraps=code_search._git) as spy:
            we.collect_repo_commits(
                repo_path=self.repo_path,
                repo_name="r",
                author_emails=["me@example.com"],
                work_date="2026-09-13",
                tz_offset="+08:00",
            )
        self.assertTrue(spy.called)
        for call in spy.call_args_list:
            self.assertGreaterEqual(len(call.args), 2)
            for arg in call.args[1:]:
                self.assertIsInstance(arg, str)

    def test_missing_path_raises_work_repo_unavailable(self):
        with self.assertRaises(WorkRepoUnavailable):
            we.collect_repo_commits(
                repo_path=os.path.join(self.root, "not-there"),
                repo_name="r",
                author_emails=["me@example.com"],
                work_date="2026-09-13",
                tz_offset="+08:00",
            )

    def test_empty_author_emails_is_refused(self):
        """不填信箱就掃全部作者，等於把同事的 commit 算成自己的工時。"""
        with self.assertRaises(WorkRepoUnavailable):
            we.collect_repo_commits(
                repo_path=self.repo_path,
                repo_name="r",
                author_emails=[],
                work_date="2026-09-13",
                tz_offset="+08:00",
            )


class GitEvidenceDegradationTests(TempRepoCase):
    def test_one_broken_repo_does_not_kill_the_draft(self):
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        rows = [
            {
                "id": 1,
                "name": "好的",
                "repo_path": self.repo_path,
                "author_emails": ["me@example.com"],
                "branch_filter": [],
                "enabled": True,
            },
            {
                "id": 2,
                "name": "壞掉的",
                "repo_path": os.path.join(self.root, "gone"),
                "author_emails": ["me@example.com"],
                "branch_filter": [],
                "enabled": True,
            },
        ]
        evidence, scans, skipped = we.collect_git_evidence(
            rows, work_date="2026-09-13", tz_offset="+08:00"
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual([s.name for s in scans], ["好的"])
        self.assertEqual([s.label for s in skipped], ["壞掉的"])
        self.assertTrue(skipped[0].reason, "跳過一定要有 reason，靜默跳過等於讓人以為那天沒做事")
        self.assertEqual(skipped[0].kind, "repo")

    def test_disabled_repo_is_not_reported_as_skipped(self):
        rows = [
            {
                "id": 1,
                "name": "關掉的",
                "repo_path": self.repo_path,
                "author_emails": ["me@example.com"],
                "branch_filter": [],
                "enabled": False,
            }
        ]
        evidence, scans, skipped = we.collect_git_evidence(
            rows, work_date="2026-09-13", tz_offset="+08:00"
        )
        self.assertEqual((evidence, scans, skipped), ([], [], []))


# --------------------------------------------------------------------------
# Chat 證據（一律假 client，不打真的 Google API）
# --------------------------------------------------------------------------


class FakeChatClient:
    """只實作 `list_messages_since`。**沒有任何網路呼叫。**

    ⚠️ 這個假 client 必須**照著真 API 的三個實測行為**做，否則模擬本身是錯的，
    而「模擬錯了」的失敗與通過一樣不可信：

      1. filter 只有 `createTime >`（**嚴格大於、沒有上界**），見
         `docs/R1-findings.md:68`；
      2. 預設排序是 `createTime` **ASC**，見 `docs/HANDOFF.md:147`；
      3. `max_messages` 從**最舊的那一端**開始截斷。

    2026-09-13 之前的版本三條都沒做（`since` 與 `max_messages` 直接忽略、
    原樣回傳整份清單），所以「額度被別人的閒聊吃光」這個缺陷在測試裡
    完全表現不出來。`FakeChatClientFidelityTests` 是它的正對照。
    """

    def __init__(self, by_space, error_spaces=()):
        self.by_space = by_space
        self.error_spaces = set(error_spaces)
        self.calls = []

    def list_messages_since(self, space_id, since, max_messages=None):
        self.calls.append((space_id, since, max_messages))
        if space_id in self.error_spaces:
            raise ChatApiError("連線 Google Chat 失敗：測試用")
        cutoff = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        matched = [
            m
            for m in self.by_space.get(space_id, [])
            if (we.parse_rfc3339(m.get("createTime") or "") or _FAR_PAST) > cutoff
        ]
        matched.sort(key=lambda m: m["createTime"])
        return matched[:max_messages] if max_messages is not None else matched


#: 解析不出時間的訊息一律排在最前面，讓它被 `createTime >` 濾掉——真 API 不會
#: 回這種東西，這裡只是不讓假 client 在畸形輸入上爆掉。
_FAR_PAST = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _msg(name, *, sender, create_time, text, mention=False):
    message = {
        "name": name,
        "sender": {"name": sender},
        "createTime": create_time,
        "text": text,
    }
    if mention:
        message["annotations"] = [
            {"type": "USER_MENTION", "userMention": {"type": "MENTION", "user": {"name": "users/9"}}}
        ]
    return message


ME = "users/1"
OTHER = "users/2"


class ChatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.space = "spaces/AAA"
        self.messages = [
            _msg("spaces/AAA/messages/1", sender=ME, create_time="2026-09-13T02:00:00Z",
                 text="改好了整篩 API 的網域設定"),
            _msg("spaces/AAA/messages/2", sender=OTHER, create_time="2026-09-13T02:05:00Z",
                 text="別人的發言，不可以算成我的工時"),
            _msg("spaces/AAA/messages/3", sender=ME, create_time="2026-09-13T03:00:00Z",
                 text="@PM 這張單我今天收掉", mention=True),
            _msg("spaces/AAA/messages/4", sender=ME, create_time="2026-09-14T01:00:00Z",
                 text="這是隔天的發言"),
            _msg("spaces/AAA/messages/5", sender=ME, create_time="2026-09-13T04:00:00Z", text="好"),
        ]
        self.client = FakeChatClient({self.space: self.messages})

    def _collect(self, **kwargs):
        params = dict(
            work_date="2026-09-13",
            tz_offset="+08:00",
            google_user_id=ME,
            space_names={self.space: "訂閱系統"},
        )
        params.update(kwargs)
        return we.collect_chat_evidence(self.client, [self.space], **params)

    def test_only_the_viewers_own_messages(self):
        evidence, scans, skipped = self._collect()
        self.assertEqual(skipped, [])
        refs = [e.ref for e in evidence]
        self.assertIn("chat:spaces/AAA/messages/1", refs)
        self.assertIn("chat:spaces/AAA/messages/3", refs)
        self.assertNotIn("chat:spaces/AAA/messages/2", refs)
        self.assertEqual(scans[0].message_count, 2)
        self.assertEqual(scans[0].space_name, "訂閱系統")

    def test_day_boundary_uses_the_offset(self):
        """UTC 02:00 ＝ 台北 10:00（同一天）；UTC 隔天 01:00 ＝ 台北 09:00（隔天）。"""
        evidence, _, _ = self._collect()
        self.assertNotIn("chat:spaces/AAA/messages/4", [e.ref for e in evidence])

        evidence_utc, _, _ = self._collect(tz_offset="+00:00")
        self.assertNotIn("chat:spaces/AAA/messages/4", [e.ref for e in evidence_utc])

        evidence_next, _, _ = self._collect(work_date="2026-09-14")
        self.assertEqual([e.ref for e in evidence_next], ["chat:spaces/AAA/messages/4"])

    def test_very_short_messages_are_dropped(self):
        evidence, _, _ = self._collect()
        self.assertNotIn("chat:spaces/AAA/messages/5", [e.ref for e in evidence])

    def test_mention_gets_the_bonus(self):
        evidence, _, _ = self._collect()
        by_ref = {e.ref: e for e in evidence}
        mentioned = by_ref["chat:spaces/AAA/messages/3"]
        self.assertTrue(mentioned.is_mention)
        self.assertAlmostEqual(
            mentioned.weight_input,
            we.chat_weight_input(mentioned.char_count, True),
        )

    def test_meta_never_carries_the_message_body(self):
        """訊息內文不落地（與 mentions 表同一條鐵律），meta 只給識別資訊。"""
        evidence, _, _ = self._collect()
        meta = evidence[0].to_meta()
        self.assertIn("message_name", meta)
        self.assertNotIn("text", meta)
        self.assertNotIn("title", meta)

    def test_without_user_id_nothing_is_collected(self):
        evidence, scans, skipped = self._collect(google_user_id="")
        self.assertEqual((evidence, scans), ([], []))
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].kind, "space")

    def test_since_is_nudged_back_for_the_exclusive_filter(self):
        """`list_messages_since` 的 filter 是嚴格大於，整點的發言不可以掉。"""
        self._collect()
        _space, since, max_messages = self.client.calls[0]
        start, _end = we.day_bounds("2026-09-13", "+08:00")
        self.assertLess(since, start)
        # 每次要的是**翻頁的一塊**，不是「自己的發言保留上限」。兩個常數的
        # 預設值剛好都是 200，寫錯了會矇混過關，所以這裡指名道姓比對。
        self.assertEqual(max_messages, we._CHAT_SCAN_CHUNK)

    def test_api_failure_degrades_to_skipped(self):
        client = FakeChatClient({}, error_spaces={self.space})
        evidence, scans, skipped = we.collect_chat_evidence(
            client,
            [self.space],
            work_date="2026-09-13",
            tz_offset="+08:00",
            google_user_id=ME,
        )
        self.assertEqual((evidence, scans), ([], []))
        self.assertEqual(skipped[0].kind, "space")
        self.assertIn("Google Chat", skipped[0].reason)


class FakeChatClientFidelityTests(unittest.TestCase):
    """正對照：先證明假 client 真的像那支 API，再相信它跑出來的結論。

    模擬沒做對的時候，「未通過」跟「通過」一樣不可信。
    """

    def setUp(self):
        self.msgs = [
            _msg("spaces/S/messages/a", sender=ME, create_time="2026-09-13T01:00:00Z", text="一"),
            _msg("spaces/S/messages/b", sender=ME, create_time="2026-09-13T03:00:00Z", text="三"),
            _msg("spaces/S/messages/c", sender=ME, create_time="2026-09-13T02:00:00Z", text="二"),
        ]
        self.client = FakeChatClient({"spaces/S": self.msgs})

    def _names(self, since, max_messages=None):
        got = self.client.list_messages_since("spaces/S", since, max_messages=max_messages)
        return [m["name"].rsplit("/", 1)[-1] for m in got]

    def test_filter_is_strictly_greater_and_has_no_upper_bound(self):
        at_two = datetime(2026, 9, 13, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(self._names(at_two), ["b"], "02:00 那則自己要被排除（嚴格大於）")

    def test_results_are_ascending(self):
        epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(self._names(epoch), ["a", "c", "b"])

    def test_cap_cuts_the_newest_end(self):
        epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(self._names(epoch, max_messages=2), ["a", "c"])


class ChatPagingTests(unittest.TestCase):
    """⚠️ 迴歸：則數上限數的是「整個 Space 的訊息」，不是「你的訊息」。

    2026-09-13 實測：一個從 09:00 起每 2 分鐘一則的聊天室，Viewer 當天 5 則
    發言只蒐集到 3 則——17:00 與 18:20 那兩則不見了，而 `skipped` 是空的、
    `message_count: 3` 看起來就像那天只講了 3 句話。

    所以停止條件必須是「翻到日界」而不是「額度用完」，`truncated` 也必須
    只在後者成立時才為真。
    """

    SPACE = "spaces/BUSY"
    MINE = ("09:05", "14:10", "15:30", "17:00", "18:20")

    def setUp(self):
        messages = []
        # 熱門聊天室：當天 09:00 起每 2 分鐘一則**別人的**訊息，共 300 則
        for i in range(300):
            hour, minute = 9 + (i * 2) // 60, (i * 2) % 60
            messages.append(
                _msg(
                    f"{self.SPACE}/messages/other{i}",
                    sender=OTHER,
                    create_time=f"2026-09-13T{hour:02d}:{minute:02d}:00+08:00",
                    text="閒聊閒聊",
                )
            )
        for i, clock in enumerate(self.MINE):
            messages.append(
                _msg(
                    f"{self.SPACE}/messages/mine{i}",
                    sender=ME,
                    create_time=f"2026-09-13T{clock}:00+08:00",
                    text=f"我報告進度第 {i} 次，改好了登入的問題",
                )
            )
        self.client = FakeChatClient({self.SPACE: messages})

    def _collect(self):
        return we.collect_chat_evidence(
            self.client,
            [self.SPACE],
            work_date="2026-09-13",
            tz_offset="+08:00",
            google_user_id=ME,
            space_names={self.SPACE: "專案討論"},
        )

    def test_my_messages_survive_a_busy_space(self):
        evidence, scans, skipped = self._collect()
        self.assertEqual(skipped, [])
        self.assertEqual(
            [e.occurred_at[11:16] for e in evidence],
            list(self.MINE),
            "下午之後的發言不可以被別人的閒聊擠掉",
        )
        self.assertEqual(scans[0].message_count, len(self.MINE))

    def test_reaching_the_day_boundary_is_not_truncated(self):
        """翻到日界自然停下是**正常結束**。

        一個同時代表「正常」與「漏了」的旗標等於沒有旗標——使用者看到它亮著
        卻沒事，下一次真的漏了也不會再看。
        """
        _evidence, scans, _skipped = self._collect()
        self.assertFalse(scans[0].truncated)

    def test_it_really_had_to_page(self):
        """前置條件：這個情境確實翻了不只一次，否則上面兩條沒有證明力。"""
        self._collect()
        self.assertGreater(len(self.client.calls), 1)
        self.assertEqual({c[0] for c in self.client.calls}, {self.SPACE})

    def test_budget_running_out_before_the_boundary_is_truncated(self):
        """預算用完而且還沒翻到日界 —— 這才是 truncated 的唯一意思。"""
        with mock.patch.object(cfg, "WORKLOG_CHAT_SCAN_PER_SPACE", 50):
            evidence, scans, _skipped = self._collect()
        self.assertTrue(scans[0].truncated)
        self.assertLess(len(evidence), len(self.MINE), "前置條件：這樣確實會漏")

    def test_own_message_cap_is_also_truncated(self):
        """自己的發言多到超過保留上限，同樣是「有東西沒進來」。"""
        with mock.patch.object(cfg, "WORKLOG_MAX_CHAT_MESSAGES_PER_SPACE", 2):
            evidence, scans, _skipped = self._collect()
        self.assertEqual(len(evidence), 2)
        self.assertTrue(scans[0].truncated)
        self.assertEqual(
            [e.occurred_at[11:16] for e in evidence],
            list(self.MINE[:2]),
            "先排序再截斷，同一天重產兩次要得到同一份草稿",
        )

    def test_a_quiet_space_costs_one_call(self):
        """正對照：訊息少的時候不可以為了翻頁多打 API。"""
        client = FakeChatClient(
            {
                self.SPACE: [
                    _msg(
                        f"{self.SPACE}/messages/only",
                        sender=ME,
                        create_time="2026-09-13T10:00:00+08:00",
                        text="今天只講了這一句，改好了登入的問題",
                    )
                ]
            }
        )
        evidence, scans, _skipped = we.collect_chat_evidence(
            client,
            [self.SPACE],
            work_date="2026-09-13",
            tz_offset="+08:00",
            google_user_id=ME,
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(scans[0].truncated)

    def test_repeated_messages_are_counted_once(self):
        """假設 API 在翻頁邊界重複給了同一則，也不可以變成兩筆工時證據。"""
        repeated = _msg(
            f"{self.SPACE}/messages/dup",
            sender=ME,
            create_time="2026-09-13T10:00:00+08:00",
            text="同一則訊息被回了兩次",
        )

        class RepeatingClient(FakeChatClient):
            def list_messages_since(self, space_id, since, max_messages=None):
                got = super().list_messages_since(space_id, since, max_messages=max_messages)
                return got + [repeated]

        client = RepeatingClient({self.SPACE: [repeated]})
        evidence, _scans, _skipped = we.collect_chat_evidence(
            client,
            [self.SPACE],
            work_date="2026-09-13",
            tz_offset="+08:00",
            google_user_id=ME,
        )
        self.assertEqual(len(evidence), 1)


class SpacePopulationTests(unittest.TestCase):
    def test_union_keeps_pinned_first_and_dedupes(self):
        got = we.resolve_chat_space_ids(
            pinned_space_ids=["spaces/A", "spaces/B"],
            mention_space_ids=["spaces/B", "spaces/C"],
        )
        self.assertEqual(got, ["spaces/A", "spaces/B", "spaces/C"])

    def test_hard_cap_so_we_never_sweep_the_whole_account(self):
        """🔴 整個帳號有四百多個 Space，不可以有「全掃」這個選項。"""
        many = [f"spaces/{i}" for i in range(500)]
        got = we.resolve_chat_space_ids(pinned_space_ids=many)
        self.assertEqual(len(got), cfg.WORKLOG_MAX_SPACES)

    def test_blank_ids_are_dropped(self):
        self.assertEqual(
            we.resolve_chat_space_ids(pinned_space_ids=["", "  ", "spaces/A"]), ["spaces/A"]
        )


# --------------------------------------------------------------------------
# 一天份的總帳
# --------------------------------------------------------------------------


class DayEvidenceTests(TempRepoCase):
    def test_merges_both_sources_in_time_order(self):
        self._commit("寫程式", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        client = FakeChatClient(
            {
                "spaces/AAA": [
                    _msg(
                        "spaces/AAA/messages/1",
                        sender=ME,
                        create_time="2026-09-13T03:00:00Z",  # 台北 11:00
                        text="回報進度：整篩 API 已改好",
                    )
                ]
            }
        )
        day = we.collect_day_evidence(
            work_date="2026-09-13",
            tz_offset="+08:00",
            work_repos=[
                {
                    "id": 1,
                    "name": "repo",
                    "repo_path": self.repo_path,
                    "author_emails": ["me@example.com"],
                    "branch_filter": [],
                    "enabled": True,
                }
            ],
            chat_client=client,
            space_ids=["spaces/AAA"],
            google_user_id=ME,
        )
        self.assertFalse(day.is_empty)
        self.assertEqual([e.kind for e in day.evidence], ["commit", "chat"])
        self.assertEqual(day.tz_offset, "+08:00")
        self.assertEqual(day.since, "2026-09-13T00:00:00+08:00")
        self.assertEqual(day.until, "2026-09-14T00:00:00+08:00")

    def test_meta_shape(self):
        self._commit("寫程式", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        day = we.collect_day_evidence(
            work_date="2026-09-13",
            tz_offset="+0800",
            work_repos=[
                {
                    "id": 1,
                    "name": "repo",
                    "repo_path": self.repo_path,
                    "author_emails": ["me@example.com"],
                    "branch_filter": [],
                    "enabled": True,
                }
            ],
        )
        meta = day.to_meta()
        for key in (
            "work_date",
            "tz_offset",
            "since",
            "until",
            "repos",
            "repos_skipped",
            "spaces",
            "spaces_skipped",
            "commits",
            "chat",
        ):
            self.assertIn(key, meta)
        self.assertEqual(meta["tz_offset"], "+08:00", "回報的偏移要是正規化過的")
        self.assertEqual(meta["chat"], [])
        self.assertEqual(meta["commits"][0]["ref"], day.evidence[0].ref)

    def test_empty_day_is_detectable(self):
        day = we.collect_day_evidence(work_date="2026-09-13", tz_offset="+08:00")
        self.assertTrue(day.is_empty, "呼叫端據此發 SSE 的 NO_EVIDENCE")

    def test_bad_offset_raises_instead_of_producing_a_wrong_day(self):
        with self.assertRaises(InvalidParameter):
            we.collect_day_evidence(work_date="2026-09-13", tz_offset="Asia/Taipei")


# --------------------------------------------------------------------------
# 「我的工作 repo」的登錄驗證（從 server.py 搬過來）
# --------------------------------------------------------------------------


class VerifyWorkRepoTests(TempRepoCase):
    def test_real_repo_is_ok(self):
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        result = we.verify_work_repo(self.repo_path)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["detected_emails"], ["me@example.com"])

    def test_missing_path_does_not_raise(self):
        result = we.verify_work_repo(os.path.join(self.root, "not-there"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "路徑不存在或不是資料夾")

    def test_record_callback_receives_the_error(self):
        seen = []
        out = we.verify_and_record_work_repo(
            {"id": 7, "name": "壞掉的", "repo_path": os.path.join(self.root, "gone")},
            record=seen.append,
        )
        self.assertEqual(seen, ["路徑不存在或不是資料夾"])
        self.assertEqual(out["last_verify_error"], "路徑不存在或不是資料夾")
        self.assertFalse(out["verification"]["ok"])
        self.assertEqual(out["name"], "壞掉的", "原本的欄位要原封不動帶回去")

    def test_record_failure_does_not_break_the_response(self):
        """寫回失敗只該吞掉寫回，不該讓設定頁整份清單拿不到。"""
        self._commit("一筆", email="me@example.com", authored="2026-09-13T09:00:00+08:00")
        errors = []

        def boom(_error):
            raise RuntimeError("資料庫壞了")

        out = we.verify_and_record_work_repo(
            {"id": 7, "name": "好的", "repo_path": self.repo_path},
            record=boom,
            on_record_error=errors.append,
        )
        self.assertTrue(out["verification"]["ok"])
        self.assertEqual(len(errors), 1)


class ModuleBoundaryTests(unittest.TestCase):
    def test_does_not_import_repository(self):
        """與 `code_search` 同一條可測性慣例：不接資料庫也能測。

        用 AST 看真正的 import 敘述，不是對整份原始碼做字串比對——
        後者會被註解與 docstring 裡的「不 import repository」自己絆倒，
        那是把關工具本身沒有正對照的典型形態。
        """
        with open(we.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
        self.assertNotIn("repository", imported)
        self.assertNotIn("db", imported)
        self.assertNotIn("core.repository", imported)
        # 正對照：確認這個檢查真的看得到 import（否則「沒問題」毫無意義）
        self.assertIn("code_search", imported)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
