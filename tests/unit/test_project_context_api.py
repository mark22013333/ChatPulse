import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from test_project_context import ProjectContextFixture, CHAT
import unittest
from test_draft_stream_wiring import FakeClient, FakeProvider, _drain
from dashboard.api import server
from core import config, repository as repo
from core.errors import ChatPulseError, InvalidParameter, ProjectContextStale


class ProjectContextApiTest(ProjectContextFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        repo.set_project_context_opt_in(self.a, True)
        self.viewer = {"id": self.a, "google_user_id": "users/fake-a", "display_name": "甲"}
        self.client = FakeClient()
        self.reads = []
        self.client.get_message = lambda name: {
            "name": self.mention["message_name"], "text": "假問題", "sender": {"name": "users/peer"},
            "createTime": "2026-10-01T00:00:00Z", "thread": {"name": self.mention["thread_name"]},
        }
        self.client.list_thread_messages = lambda *a, **k: [self.client.get_message("")]
        self.client.fetch_recent_messages = lambda sid, **kw: self.reads.append(sid) or [{
            "name": sid + "/messages/evidence", "text": "FAKE_PM_CONTENT" if sid == "spaces/pm" else "FAKE_TARGET_CONTEXT", "sender": {"name": "users/peer"},
            "createTime": "2026-10-01T00:00:00Z",
        }]
        self.client.send_message = Mock(return_value={"name": "spaces/target/messages/sent", "createTime": "now"})
        self.provider = FakeProvider()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, name, value in (
            (config, "PROJECT_CONTEXT_ENABLED", True),
            (server, "get_client", Mock(return_value=self.client)),
            (server, "get_provider", Mock(return_value=self.provider)),
            (server, "resolve_model", lambda *a, **kw: ("fake", None)),
            (server, "space_shape", lambda *a: ("SPACE", "THREADED_MESSAGES")),
            (server, "learn_names", lambda *a: None),
            (server, "name_resolver_for", lambda *a: lambda uid: "假成員"),
            (server, "space_display_name", lambda *a: "假 PM Space"),
            (server.providers, "resolve_name", lambda *a: "fake"),
        ):
            self.stack.enter_context(patch.object(target, name, value))

    def request(self, selection=None, **kw):
        return server.DraftRequest(project_selection=server.ProjectSelection(**(selection or self.select())), **kw)

    def stream(self, request=None):
        response = server.draft_stream(self.mid, request or self.request(), self.viewer)
        events = asyncio.run(_drain(response.body_iterator))
        return [json.loads(event.split("data: ", 1)[1]) for event in events]

    def complete(self):
        events = self.stream()
        return next(e for e in events if e["type"] == "done")

    def test_actual_prompt_metadata_only_zero_content_and_positive(self):
        events = self.stream()
        self.assertNotIn("spaces/pm", self.reads)
        self.assertNotIn("FAKE_PM_CONTENT", self.provider.prompt)
        self.assertIn("甲案", self.provider.prompt)
        self.assertNotIn("PM 共用", self.provider.prompt)
        self.assertNotIn("乙案", self.provider.prompt)
        self.assertTrue(any(e["type"] == "done" for e in events))
        self.stream(self.request(self.select("reply_context")))
        self.assertEqual(self.reads.count("spaces/pm"), 1)
        self.assertIn("FAKE_PM_CONTENT", self.provider.prompt)

    def test_mixed_legacy_and_wrong_target_rejected_before_client(self):
        for request in (
            self.request(reference_space_ids=["spaces/pm"]),
            self.request(code_refs=[{"project_id": 3}]),
            self.request(merge_mention_ids=[self.mid + 1]),
            self.request({**self.select(), "target_thread_name": "spaces/target/threads/other"}),
        ):
            with self.assertRaises(ChatPulseError):
                server.draft_stream(self.mid, request, self.viewer)
        server.get_client.assert_not_called()

    def test_revision_change_during_stream_never_done(self):
        def stream(prompt, **kw):
            yield "### ✍️ 建議回話\n第一段"
            repo.save_project_profile(self.a, "已更新", [CHAT], profile_id=self.profile["id"], revision=1)
            yield "過期段落"
        self.provider.stream_text = stream
        events = self.stream()
        self.assertFalse(any(e["type"] == "done" for e in events))
        self.assertTrue(any(e["type"] == "error" for e in events))
        stored = server.get_stored_draft(self.mid, self.viewer)
        self.assertEqual(stored["scope_state"], "stale")
        self.assertEqual(stored["generation_config"]["project_status"], "partial")
        self.assertNotIn("過期段落", stored["content_md"])

    def test_send_invalid_digest_missing_cross_anchor_stale_zero_client(self):
        done = self.complete()
        did = done["draft_id"]
        server.get_client.reset_mock()
        for request in (
            server.ReplyRequest(text="假回話", draft_id=did),
            server.ReplyRequest(text="假回話", draft_id=did, scope_digest="wrong"),
            server.ReplyRequest(text="假回話", draft_id=did + 1, scope_digest=done["scope_digest"]),
            server.ReplyRequest(text="假回話", scope_digest=done["scope_digest"]),
        ):
            with self.assertRaises(ProjectContextStale):
                server.reply_to_mention(self.mid, request, self.viewer)
        other = repo.upsert_draft_target(self.a, {
            "space_id": "spaces/other", "message_name": "spaces/other/messages/q",
            "thread_name": "spaces/other/threads/q", "create_time": "2026-10-01T00:00:00Z"})
        req = server.ReplyRequest(text="假回話", draft_id=did, scope_digest=done["scope_digest"])
        with self.assertRaises(ProjectContextStale):
            server.reply_to_mention(other, req, self.viewer)
        repo.save_project_profile(self.a, "改版", [CHAT], profile_id=self.profile["id"], revision=1)
        with self.assertRaises(ProjectContextStale):
            server.reply_to_mention(self.mid, req, self.viewer)
        server.get_client.assert_not_called()
        self.client.send_message.assert_not_called()

    def test_valid_send_and_gate_closed_never_downgrades(self):
        done = self.complete()
        req = server.ReplyRequest(text="人工改過仍未語意查證", draft_id=done["draft_id"], scope_digest=done["scope_digest"])
        repo.set_project_context_opt_in(self.a, False)
        server.get_client.reset_mock()
        with self.assertRaises(ProjectContextStale):
            server.reply_to_mention(self.mid, req, self.viewer)
        server.get_client.assert_not_called()
        repo.set_project_context_opt_in(self.a, True)
        server.reply_to_mention(self.mid, req, self.viewer)
        self.client.send_message.assert_called_once()

    def test_restore_disabled_gates_marks_snapshot_stale(self):
        self.complete()
        original = server.get_stored_draft(self.mid, self.viewer)
        self.assertEqual(original["scope_state"], "current")
        for gate in ("feature", "opt_in"):
            with self.subTest(gate=gate):
                with patch.object(config, "PROJECT_CONTEXT_ENABLED", gate != "feature"):
                    repo.set_project_context_opt_in(self.a, gate != "opt_in")
                    stored = server.get_stored_draft(self.mid, self.viewer)
                    self.assertEqual(stored["scope_state"], "stale")
                    self.assertEqual(stored["generation_config"], original["generation_config"])
                    self.assertEqual(stored["content_md"], original["content_md"])
        repo.set_project_context_opt_in(self.a, True)
        self.assertEqual(server.get_stored_draft(self.mid, self.viewer)["scope_state"], "current")

    def test_git_fingerprint_change_and_limit(self):
        git = repo.create_code_project(self.a, name="假 Git", repo_path="/fake/git", branches={"production": "main"})
        source = {"id": "git", "kind": "git", "label": "假 Git", "code_project_id": git,
                  "environment": "production", "paths": []}
        self.profile = repo.save_project_profile(self.a, "Git 案", [source])
        selection = {**self.select(), "sources": [{"binding_id": "git", "use": "metadata_only"}]}
        approved = self.admit(selection)
        self.assertEqual(approved.git_refs, ())
        repo.update_code_project(self.a, git, include_globs=["*.py"])
        self.assertFalse(repo.project_scope_current(self.a, self.mention, approved.scope))
        for enabled in (False,):
            repo.update_code_project(self.a, git, enabled=enabled)
            with self.assertRaises(ValueError):
                self.admit(selection)
        repo.update_code_project(self.a, git, enabled=True)
        self.profile = repo.save_project_profile(self.a, "多 Git", [{**source, "id": f"g{i}"} for i in range(3)])
        selection = {**self.select(), "sources": [{"binding_id": f"g{i}", "use": "reply_context"} for i in range(3)]}
        with self.assertRaises(ValueError):
            self.admit(selection)

    def test_bound_git_delete_is_4xx(self):
        git = repo.create_code_project(self.a, name="被綁 Git", repo_path="/fake/git", branches={"production": "main"})
        repo.save_project_profile(self.a, "Git 案", [{"id": "git", "kind": "git", "label": "Git",
            "code_project_id": git, "environment": "production", "paths": []}])
        with self.assertRaises(InvalidParameter):
            server.remove_code_project(git, self.viewer)


    def test_git_changes_during_generation_are_partial_before_next_chunk(self):
        git = repo.create_code_project(self.a, name="Git 中途改版", repo_path="/fake/git", branches={"production": "main"})
        self.profile = repo.save_project_profile(self.a, "Git 案", [{
            "id": "git", "kind": "git", "label": "假 Git", "code_project_id": git,
            "environment": "production", "paths": ["src/main.py"],
        }])
        selection = {**self.select(), "sources": [{"binding_id": "git", "use": "reply_context"}]}
        captured = []
        def collect(resolved, terms):
            captured.extend(resolved)
            return [], []
        def stream(prompt, **kw):
            yield "### ✍️ 建議回話\n第一段"
            repo.update_code_project(self.a, git, branches={"production": "release"})
            yield "不採用"
        self.provider.stream_text = stream
        with patch.object(server, "collect_code_context", collect):
            events = self.stream(self.request(selection))
        self.assertEqual(captured[0][0]["branches"], {"production": "main"})
        self.assertEqual(captured[0][2], "main")
        self.assertFalse(any(e["type"] == "done" for e in events))
        self.assertTrue(any(e["type"] == "error" for e in events))
        stored = server.get_stored_draft(self.mid, self.viewer)
        self.assertEqual(stored["generation_config"]["project_status"], "partial")
        self.assertEqual(stored["scope_state"], "stale")

    def test_cross_viewer_draft_and_target_changed_send_zero_client(self):
        done = self.complete()
        req = server.ReplyRequest(text="假回話", draft_id=done["draft_id"], scope_digest=done["scope_digest"])
        mid = repo.upsert_draft_target(self.b, {
            "space_id": "spaces/target", "message_name": "spaces/target/messages/q",
            "thread_name": "spaces/target/threads/q", "create_time": "2026-10-01T00:00:00Z",
        })
        server.get_client.reset_mock()
        with self.assertRaises(ProjectContextStale):
            server.reply_to_mention(mid, req, {**self.viewer, "id": self.b})
        altered = {**self.mention, "thread_name": "spaces/target/threads/changed"}
        with patch.object(repo, "get_mention", return_value=altered):
            with self.assertRaises(ProjectContextStale):
                server.reply_to_mention(self.mid, req, self.viewer)
        server.get_client.assert_not_called()


    def test_owned_fake_git_content_positive_and_metadata_only_zero_reader(self):
        root = Path(self.tmp.name) / "fake-git"
        root.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git("init", "-b", "main")
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text('def fake_symbol():\n    return "FAKE_CODE_EVIDENCE"\n')
        git("add", "src/main.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "假資料")
        sha = git("rev-parse", "--short", "HEAD")
        pid = repo.create_code_project(self.a, name="假原始碼", repo_path=str(root), branches={"production": "main"})
        self.profile = repo.save_project_profile(self.a, "Git 範圍", [{
            "id": "git", "kind": "git", "label": "假 Git", "code_project_id": pid,
            "environment": "production", "paths": ["src/main.py"],
        }])
        selection = {**self.select(), "sources": [{"binding_id": "git", "use": "metadata_only"}]}
        real_collect = server.code_search.collect
        with patch.object(server.code_search, "collect", wraps=real_collect) as collect:
            self.stream(self.request(selection))
            collect.assert_not_called()
            self.assertNotIn("FAKE_CODE_EVIDENCE", self.provider.prompt)
            selection["sources"][0]["use"] = "reply_context"
            events = self.stream(self.request(selection))
            collect.assert_called_once()
        self.assertIn("FAKE_CODE_EVIDENCE", self.provider.prompt)
        meta = next(e for e in events if e["type"] == "meta")
        self.assertEqual(meta["code_refs"][0]["commit_sha"], sha)
        self.assertEqual(meta["project_scope"]["sources"][0]["resolved_branch"], "main")
