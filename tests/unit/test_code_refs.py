"""`resolve_code_refs()`：草稿要查哪個專案的哪個環境。

**這是整併 PR #2 的核心。** 原本 main 的形狀是
`code_project_ids: List[int]` ＋ 全域一個 `code_environment`，
而 `collect_code_context` 對 id 做 `dict.fromkeys()` 去重——
所以「同一個專案送兩次、環境不同」（＝比對正式與 UAT）**做不到**，
儘管 `cfg.CODE_MAX_PROJECTS_PER_DRAFT = 2` 的註解正是為了那件事而寫。

改成 `code_refs: [{project_id, environment}]` 之後才成立。這一套守住：
  1. 同一個 project_id 出現兩次配不同環境**不可以被去重**
  2. 解析在 SSE 開始**之前**做，錯誤是 4xx 不是 error 事件
  3. 環境沒有對應分支要擋下來——查錯環境正是這個功能存在的理由

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core.errors import CodeProjectNotFound, InvalidParameter  # noqa: E402
from dashboard.api import server  # noqa: E402


def project(pid=3, name="智慧客服後端", branches=None, default_env="production", enabled=True):
    return {
        "id": pid,
        "name": name,
        "repo_path": "/repos/demo",
        "branches": branches if branches is not None else {"production": "main", "uat": "release/uat"},
        "default_env": default_env,
        "include_globs": [],
        "exclude_globs": [],
        "enabled": enabled,
    }


def ref(pid=3, environment=None, paths=()):
    return server.CodeRefRequest(project_id=pid, environment=environment, paths=list(paths))


class ResolveCase(unittest.TestCase):
    def resolve(self, refs, table=None):
        table = table if table is not None else {3: project()}
        with mock.patch.object(server.repo, "get_code_project", lambda vid, pid: table.get(pid)):
            return server.resolve_code_refs(1, refs)


class TestProductionVsUat(ResolveCase):
    """這個功能最有價值的用法，也是舊形狀做不到的那一個。"""

    def test_same_project_twice_with_different_environments_is_kept(self):
        out = self.resolve([ref(3, "production"), ref(3, "uat")])
        self.assertEqual(len(out), 2)
        self.assertEqual([env for _, env, _, _ in out], ["production", "uat"])
        self.assertEqual([br for _, _, br, _ in out], ["main", "release/uat"])

    def test_order_follows_the_request(self):
        out = self.resolve([ref(3, "uat"), ref(3, "production")])
        self.assertEqual([env for _, env, _, _ in out], ["uat", "production"])

    def test_config_comment_is_now_actually_achievable(self):
        """CODE_MAX_PROJECTS_PER_DRAFT 設 2 就是為了這個，要真的放得下兩筆。"""
        self.assertGreaterEqual(cfg.CODE_MAX_PROJECTS_PER_DRAFT, 2)
        self.assertEqual(len(self.resolve([ref(3, "production"), ref(3, "uat")])), 2)


class TestDefaults(ResolveCase):
    def test_environment_omitted_uses_the_project_default(self):
        out = self.resolve([ref(3, None)])
        self.assertEqual(out[0][1], "production")

    def test_paths_are_passed_through(self):
        out = self.resolve([ref(3, "production", ["app/a.py"])])
        self.assertEqual(out[0][3], ["app/a.py"])

    def test_empty_refs_returns_empty(self):
        self.assertEqual(self.resolve([]), [])

    def test_over_the_cap_is_truncated_not_rejected(self):
        out = self.resolve([ref(3, "production")] * (cfg.CODE_MAX_PROJECTS_PER_DRAFT + 3))
        self.assertEqual(len(out), cfg.CODE_MAX_PROJECTS_PER_DRAFT)


class TestRejections(ResolveCase):
    def test_missing_project_is_404(self):
        with self.assertRaises(CodeProjectNotFound):
            self.resolve([ref(999)], table={})

    def test_environment_without_a_branch_is_rejected(self):
        """查錯環境會產出「看似有憑有據、實則錯誤」的答案——這是這功能的存在理由。"""
        p = project(branches={"production": "main"})
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve([ref(3, "uat")], table={3: p})
        self.assertIn("uat", str(cm.exception))

    def test_unknown_environment_is_400_not_500(self):
        """code_search 拋的是 ValueError；沒轉換的話會變成 500。"""
        with self.assertRaises(InvalidParameter):
            self.resolve([ref(3, "staging")])

    def test_disabled_project_is_rejected(self):
        with self.assertRaises(InvalidParameter) as cm:
            self.resolve([ref(3)], table={3: project(enabled=False)})
        self.assertIn("已停用", str(cm.exception))

    def test_nothing_resolves_when_the_feature_is_off(self):
        with mock.patch.object(cfg, "CODE_ENABLED", False):
            self.assertEqual(self.resolve([ref(3)]), [])


class TestEnvHelper(unittest.TestCase):
    def test_valid_values_pass(self):
        for env in cfg.CODE_ENVIRONMENTS:
            self.assertEqual(server._env(env), env)

    def test_invalid_value_becomes_invalid_parameter(self):
        with self.assertRaises(InvalidParameter):
            server._env("staging")


class TestDraftStreamWiring(unittest.TestCase):
    """code_refs 要在 SSE 開始之前解析——錯誤是 4xx，不是 error 事件。"""

    def test_bad_project_raises_before_any_sse_output(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_draft_stream_wiring import MENTION, VIEWER, FakeClient  # noqa: PLC0415

        req = server.DraftRequest(code_refs=[{"project_id": 999}])
        with mock.patch.object(server.repo, "get_mention", lambda vid, mid: dict(MENTION)), \
             mock.patch.object(server.repo, "get_code_project", lambda vid, pid: None), \
             mock.patch.object(server, "viewer_default_models", lambda vid: {}), \
             mock.patch.object(server, "get_client", lambda vid: FakeClient()), \
             mock.patch.object(server.providers, "resolve_name", lambda p=None: "fake"):
            with self.assertRaises(CodeProjectNotFound):
                # 連 StreamingResponse 都還沒建立就該拋
                server.draft_stream(7, req, VIEWER)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGitReadFailures(unittest.TestCase):
    def setUp(self):
        import subprocess
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.path, check=True, capture_output=True, text=True).stdout.strip()
        git('init', '-b', 'main')
        git('config', 'user.email', 'fixture@example.test')
        git('config', 'user.name', 'Fixture')
        Path(self.path, 'demo.py').write_text('def known_symbol():\n    return 42\n', encoding='utf-8')
        git('add', 'demo.py')
        git('commit', '-m', '合成測試')
        self.sha = git('rev-parse', 'HEAD')

    def collect(self, **kwargs):
        from core import code_search
        return code_search.collect(repo_path=self.path, project_name='合成專案', environment='production', branch='main', terms=['known_symbol'], **kwargs)

    def test_real_git_hit_and_successful_no_hit(self):
        from core import code_search
        context, skipped = self.collect()
        self.assertEqual(context.commit_sha, self.sha)
        self.assertEqual(context.hits[0].path, 'demo.py')
        self.assertEqual(skipped, [])
        self.assertEqual(code_search._grep(self.path, self.sha, ['absent_symbol'], []), {})

    def test_invalid_pathspec_is_not_successful_no_hit(self):
        from core.errors import ChatPulseError
        with self.assertRaises(ChatPulseError) as raised:
            self.collect(include_globs=[':(unsupported)demo.py'])
        self.assertEqual(raised.exception.code, 'CODE_READ_FAILED')
        self.assertNotIn(self.path, str(raised.exception))

    def test_show_missing_file_is_not_skipped(self):
        from core.errors import ChatPulseError
        with self.assertRaises(ChatPulseError) as raised:
            self.collect(explicit_paths=['absent.py'])
        self.assertEqual(raised.exception.code, 'CODE_READ_FAILED')

    def test_show_failure_after_real_grep_stops_collection(self):
        from core import code_search
        from core.errors import ChatPulseError
        import subprocess
        real_git = code_search._git
        def fail_show(path, *args, **kwargs):
            if args[0] == 'show':
                return subprocess.CompletedProcess(args, 128, '', 'private stderr')
            return real_git(path, *args, **kwargs)
        with mock.patch.object(code_search, '_git', side_effect=fail_show):
            with self.assertRaises(ChatPulseError) as raised:
                self.collect()
        self.assertEqual(raised.exception.code, 'CODE_READ_FAILED')
        self.assertNotIn('private stderr', str(raised.exception))

    def test_timeout_is_not_partial_success(self):
        from core import code_search
        from core.errors import ChatPulseError
        from pathlib import Path
        import shutil
        wrapper = Path(self.path, 'slow_git.py')
        wrapper.write_text(
            f'#!{sys.executable}\n'
            'import os, sys, time\n'
            "if 'grep' in sys.argv: time.sleep(1)\n"
            f'os.execv({shutil.which("git")!r}, ["git", *sys.argv[1:]])\n',
            encoding='utf-8',
        )
        wrapper.chmod(0o700)
        real_git = code_search._git
        def short_grep(path, *args, **kwargs):
            return real_git(path, *args, timeout=0.1 if args[0] == 'grep' else 5)
        with mock.patch.object(cfg, 'CODE_GIT_BIN', str(wrapper)), \
             mock.patch.object(code_search, '_git', side_effect=short_grep):
            with self.assertRaises(ChatPulseError) as raised:
                self.collect()
        self.assertEqual(raised.exception.code, 'CODE_READ_FAILED')
