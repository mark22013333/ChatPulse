import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core import config, db, repository as repo
from core.project_context import git_fingerprint

CHAT = {"id": "pm", "kind": "chat", "label": "PM 共用", "space_id": "spaces/pm", "category": "pm"}


class ProjectContextFixture:
    def setUp(self):
        fixtures = Path(__file__).resolve().parents[2] / ".audit" / "pr03-owner" / "fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="project-context-", dir=fixtures)
        self.addCleanup(self.tmp.cleanup)
        db.close_connection()
        self.patches = [patch.object(config, "DB_PATH", str(Path(self.tmp.name) / "fake.db")),
                        patch.object(config, "ensure_data_dir", lambda: None)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(db.close_connection)
        db.init_db()
        self.a = repo.upsert_viewer("users/fake-a", None, display_name="甲")["id"]
        self.b = repo.upsert_viewer("users/fake-b", None, display_name="乙")["id"]
        self.mid = repo.upsert_draft_target(self.a, {
            "space_id": "spaces/target", "message_name": "spaces/target/messages/q",
            "thread_name": "spaces/target/threads/q", "create_time": "2026-10-01T00:00:00Z"})
        self.mention = repo.get_mention(self.a, self.mid)
        self.profile = repo.save_project_profile(self.a, "甲案", [CHAT])

    def select(self, use="metadata_only"):
        return {"profile_id": self.profile["id"], "revision": self.profile["revision"],
                "sources": [{"binding_id": "pm", "use": use}], "target_space_id": self.mention["space_id"],
                "target_thread_name": self.mention["thread_name"]}

    def admit(self, selection=None):
        return repo.admit_project_scope(self.a, self.mid, selection or self.select(), 50, [], 2)



class ProjectContextTest(ProjectContextFixture, unittest.TestCase):
    def test_reinit_and_opt_in_default(self):
        db.init_db()
        self.assertEqual(repo.get_project_profile(self.a, self.profile["id"])["sources"], [CHAT])
        self.assertFalse(repo.get_preferences(self.a)["project_context_opt_in"])
        self.assertTrue(repo.set_project_context_opt_in(self.a, True)["project_context_opt_in"])
        self.assertEqual(db.query_one("SELECT value FROM schema_meta WHERE key='schema_version'")["value"], str(db.SCHEMA_VERSION))

    def test_v9_preferences_upgrade(self):
        conn = db.get_connection()
        conn.execute("DROP TABLE project_source_bindings")
        conn.execute("DROP TABLE project_profiles")
        conn.execute("DROP INDEX idx_code_projects_owner_id")
        conn.execute("UPDATE schema_meta SET value = '9' WHERE key = 'schema_version'")
        conn.execute("ALTER TABLE preferences DROP COLUMN project_context_opt_in")
        conn.commit()
        db.init_db()
        self.assertFalse(repo.get_preferences(self.a)["project_context_opt_in"])

    def test_private_cas_and_rollback(self):
        self.assertIsNone(repo.get_project_profile(self.b, self.profile["id"]))
        with self.assertRaises(ValueError):
            repo.save_project_profile(self.a, "改壞", [CHAT, {"id": "git", "kind": "git",
                "label": "假 Git", "code_project_id": 999, "environment": "production", "paths": []}],
                profile_id=self.profile["id"], revision=1)
        self.assertEqual(repo.get_project_profile(self.a, self.profile["id"]), self.profile)
        updated = repo.save_project_profile(self.a, "新名", [CHAT], profile_id=self.profile["id"], revision=1)
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(ValueError):
            self.admit()
        with self.assertRaises(ValueError):
            repo.save_project_profile(self.a, "舊寫入", [CHAT], profile_id=self.profile["id"], revision=1)

    def test_composite_owner_fk(self):
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO project_source_bindings(owner_viewer_id,profile_id,binding_id,kind,label,space_id,category) VALUES(?,?,?,?,?,?,?)",
                       (self.b, self.profile["id"], "foreign", "chat", "錯 owner", "spaces/x", "pm"))
        other = repo.create_code_project(self.b, name="乙 Git", repo_path="/fake/repo", branches={"production": "main"})
        with self.assertRaises(ValueError):
            repo.save_project_profile(self.a, "錯 Git", [{"id": "g", "kind": "git", "label": "Git",
                "code_project_id": other, "environment": "production", "paths": []}])
        self.assertEqual(len(repo.list_project_profiles(self.a)), 1)

    def test_metadata_only_and_approved_projection(self):
        admitted = self.admit()
        self.assertEqual(admitted.chat_ids, ())
        self.assertEqual(admitted.git_refs, ())
        self.assertEqual(admitted.scope["sources"][0]["use"], "metadata_only")
        self.assertEqual(self.admit(self.select("reply_context")).chat_ids, ("spaces/pm",))
        for changes in ({"target_thread_name": "spaces/target/threads/other"}, {"sources": []}):
            with self.assertRaises(ValueError):
                self.admit({**self.select(), **changes})

    def test_scoped_save_owned_anchor_and_stale_partial(self):
        scope = self.admit().scope
        cfg = {"meta": {"project_scope": scope}, "project_status": "complete", "semantic_status": "unverified"}
        result = repo.create_project_draft(self.a, self.mid, "假草稿", cfg)
        did = result["draft_id"]
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(repo.get_owned_draft(self.b, self.mid, did))
        self.assertIsNone(repo.get_owned_draft(self.a, self.mid + 1, did))
        repo.save_project_profile(self.a, "更新", [CHAT], profile_id=self.profile["id"], revision=1)
        result = repo.create_project_draft(self.a, self.mid, "過期草稿", cfg)
        saved = json.loads(repo.get_owned_draft(self.a, self.mid, result["draft_id"])["generation_config_json"])
        self.assertEqual(saved["project_status"], "partial")
        self.assertEqual(result["status"], "partial")

    def test_check_rejects_null_variant_fields(self):
        valid_git = repo.create_code_project(self.a, name="有效 Git", repo_path="/fake/git",
                                            branches={"production": "main"})
        for kind, space, category, pid, env, paths in (
            ("chat", "spaces/a", None, None, None, None),
            ("git", None, None, valid_git, None, "[]"),
        ):
            with self.assertRaises(sqlite3.IntegrityError) as raised:
                db.execute("INSERT INTO project_source_bindings(owner_viewer_id,profile_id,binding_id,kind,label,space_id,category,code_project_id,environment,paths_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (self.a, self.profile["id"], "invalid", kind, "錯誤來源", space, category, pid, env, paths))
            self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_CHECK)

    def test_fingerprint_ignores_verify_time_but_covers_read_settings(self):
        p = dict(repo_path="/fake", branches={"production": "main"}, default_env="production",
                 include_globs=[], exclude_globs=[], enabled=True)
        self.assertEqual(git_fingerprint(p), git_fingerprint({**p, "last_verified_at": "later"}))
        for key, value in (("enabled", False), ("repo_path", "/other"), ("branches", {"production": "v2"}),
                           ("include_globs", ["*.py"]), ("exclude_globs", ["secret"])):
            self.assertNotEqual(git_fingerprint(p), git_fingerprint({**p, key: value}))

    def test_source_limits_and_invalid_paths(self):
        for sources in ([{**CHAT, "id": str(i)} for i in range(13)], [CHAT, CHAT]):
            with self.assertRaises(ValueError):
                repo.save_project_profile(self.a, "超量", sources)
