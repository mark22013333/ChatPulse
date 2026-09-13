"""「我的工作 repo」（`work_repos`）的資料表、存取層與 API 驗證。

這張表是 Draft Worklog 的證據來源（ADR-0008）：記「要從哪些本機 git repo、
用哪些作者信箱去撈 commit」。它與 `code_projects` 高度同型但**語意不同**，
所以這一套測試特別守住三件容易在日後被「順手統一」掉的事：

  1. `branch_filter` 空陣列 ＝ 掃**全部**分支，不是「不掃」。它與
     `code_projects` 的「環境→釘死一個分支」正好相反。
  2. `author_emails` 是**陣列**。同一個人在不同 repo 常設不同 `user.email`，
     退化成單值會讓另一個信箱的 commit 靜默漏掉。
  3. `UNIQUE(viewer_id, repo_path)`。同一個 repo 登錄兩次會讓同一批 commit
     被算兩遍，時數會分錯——這條不是為了美觀。

另外驗遷移：**舊版資料庫**（沒有這張表、schema_version=5）跑過 `init_db()`
之後，表要出現而且既有資料一筆都不能掉。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_work_repos*.py'
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from core.errors import (  # noqa: E402
    InvalidParameter,
    WorkRepoNotFound,
    WorkRepoUnavailable,
)
from dashboard.api import server  # noqa: E402


# --------------------------------------------------------------------------
# 暫存資料庫
# --------------------------------------------------------------------------


class TempDbCase(unittest.TestCase):
    """每個測試一個全新的暫存 SQLite 檔。

    `db` 模組把連線快取在 thread-local，而 `cfg.DB_PATH` 是模組層的字串，
    所以切換資料庫的順序是固定的：先關掉既有連線，再蓋掉 `cfg` 的兩個路徑，
    最後才 `init_db()`。少關一次連線就會寫到上一個測試的檔案，
    少蓋一次 `DATA_DIR` 就會在正式資料夾裡留下 token.key。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-work-repos-")
        self.tmpdir = self._tmp.name
        self.db_path = os.path.join(self.tmpdir, "test.db")
        # init_db() 每次都會 INFO 一行遷移結果。那在正式執行時有用，在測試
        # 輸出裡只會蓋掉真正的失敗訊息，所以這裡暫時把它靜音。
        self._db_log = logging.getLogger("chatpulse.db")
        self._db_log_level = self._db_log.level
        self._db_log.setLevel(logging.WARNING)
        db.close_connection()
        self._patches = [
            mock.patch.object(cfg, "DB_PATH", self.db_path),
            mock.patch.object(cfg, "DATA_DIR", self.tmpdir),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    def new_viewer(self, suffix: str = "1") -> int:
        v = repo.upsert_viewer(f"users/{suffix.rjust(6, '0')}", f"v{suffix}@example.com", f"Viewer {suffix}")
        return int(v["id"])


def table_names(path: str) -> set:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


#: 一個「這次改動之前」的資料庫長什麼樣：有 viewers／preferences／code_projects，
#: 沒有 work_repos，schema_meta 記著 5。刻意手寫而不是複製當前的 `_SCHEMA`——
#: 複製的話這個測試就會跟著被測的東西一起變，等於什麼都沒驗。
_V5_SCHEMA = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_user_id TEXT NOT NULL UNIQUE,
    email TEXT, display_name TEXT,
    created_at TEXT NOT NULL, last_seen_at TEXT
);
CREATE TABLE preferences (
    viewer_id INTEGER PRIMARY KEY REFERENCES viewers(id) ON DELETE CASCADE,
    pinned_space_ids TEXT NOT NULL DEFAULT '[]',
    default_limit INTEGER NOT NULL DEFAULT 50,
    default_style TEXT NOT NULL DEFAULT 'general',
    default_provider TEXT,
    default_reply_tone TEXT,
    default_persona_id INTEGER,
    default_reply_prompt_id INTEGER,
    default_sepia_enabled INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE code_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    name TEXT NOT NULL, repo_path TEXT NOT NULL,
    default_env TEXT NOT NULL DEFAULT 'production',
    include_globs TEXT NOT NULL DEFAULT '[]',
    exclude_globs TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_verified_at TEXT, last_verify_error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(viewer_id, name)
);
"""


# --------------------------------------------------------------------------
# 資料表與遷移
# --------------------------------------------------------------------------


class TestWorkRepoSchema(TempDbCase):
    def test_schema_version_bumped(self):
        self.assertEqual(db.SCHEMA_VERSION, 6, "新增 work_repos 必須遞增 SCHEMA_VERSION")

    def test_fresh_db_has_work_repos(self):
        db.init_db()
        self.assertIn("work_repos", table_names(self.db_path))
        version = db.query_one("SELECT value FROM schema_meta WHERE key='schema_version'")
        self.assertEqual(version["value"], "6")

    def test_columns_and_defaults(self):
        db.init_db()
        cols = {
            r["name"]: r
            for r in db.query_all("PRAGMA table_info(work_repos)")
        }
        for name in (
            "id", "viewer_id", "name", "repo_path", "author_emails", "branch_filter",
            "default_zplanner_project_id", "default_zplanner_project_key",
            "enabled", "last_verified_at", "last_verify_error",
            "created_at", "updated_at",
        ):
            self.assertIn(name, cols, f"缺少欄位 {name}")
        # 兩個 JSON 陣列欄位是 NOT NULL 且預設空陣列——空陣列在 branch_filter
        # 的意思是「掃全部分支」，所以它必須存得下去，不能是 NULL。
        for name in ("author_emails", "branch_filter"):
            self.assertEqual(cols[name]["notnull"], 1, f"{name} 應為 NOT NULL")
            self.assertEqual(cols[name]["dflt_value"], "'[]'", f"{name} 的預設應是 '[]'")
        # 兩個 ZPlanner 欄位可為 NULL：「這個 repo 沒有預設專案」是合法狀態
        self.assertEqual(cols["default_zplanner_project_id"]["notnull"], 0)
        self.assertEqual(cols["default_zplanner_project_key"]["notnull"], 0)

    def test_both_unique_constraints_exist(self):
        db.init_db()
        vid = self.new_viewer()
        repo.create_work_repo(vid, name="客服後端", repo_path="/repos/cs-backend")
        with self.assertRaises(sqlite3.IntegrityError):
            repo.create_work_repo(vid, name="客服後端", repo_path="/repos/other")
        with self.assertRaises(sqlite3.IntegrityError):
            # 路徑重複比名稱重複嚴重：同一批 commit 會被算兩遍
            repo.create_work_repo(vid, name="另一個名字", repo_path="/repos/cs-backend")

    def test_unique_is_per_viewer(self):
        """唯一性是 (viewer_id, …)，不是全域——兩位 Viewer 可以登錄同一個路徑。"""
        db.init_db()
        a, b = self.new_viewer("1"), self.new_viewer("2")
        repo.create_work_repo(a, name="客服後端", repo_path="/repos/cs-backend")
        repo.create_work_repo(b, name="客服後端", repo_path="/repos/cs-backend")
        self.assertEqual(len(repo.list_work_repos(a)), 1)
        self.assertEqual(len(repo.list_work_repos(b)), 1)

    def test_migration_from_v5_adds_table_and_keeps_data(self):
        """舊版資料庫跑遷移：表要出現，既有資料一筆都不能掉。"""
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(_V5_SCHEMA)
            con.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', '5')"
            )
            con.execute(
                "INSERT INTO viewers(id, google_user_id, email, display_name, created_at) "
                "VALUES(7, 'users/777', 'old@example.com', '既有 Viewer', '2026-01-01T00:00:00+00:00')"
            )
            con.execute(
                "INSERT INTO preferences(viewer_id, pinned_space_ids, default_style, updated_at) "
                "VALUES(7, '[\"spaces/AAA\"]', 'technical', '2026-01-01T00:00:00+00:00')"
            )
            con.execute(
                "INSERT INTO code_projects(id, viewer_id, name, repo_path, created_at, updated_at) "
                "VALUES(3, 7, '既有參考專案', '/repos/legacy', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            con.commit()
        finally:
            con.close()

        self.assertNotIn("work_repos", table_names(self.db_path), "前置條件：舊 DB 不該有這張表")

        db.init_db()

        self.assertIn("work_repos", table_names(self.db_path))
        version = db.query_one("SELECT value FROM schema_meta WHERE key='schema_version'")
        self.assertEqual(version["value"], "6", "schema_version 要被更新")

        viewer = db.query_one("SELECT * FROM viewers WHERE id = 7")
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer["email"], "old@example.com")
        prefs = db.query_one("SELECT * FROM preferences WHERE viewer_id = 7")
        self.assertEqual(prefs["default_style"], "technical")
        self.assertEqual(json.loads(prefs["pinned_space_ids"]), ["spaces/AAA"])
        project = db.query_one("SELECT * FROM code_projects WHERE id = 3")
        self.assertEqual(project["name"], "既有參考專案")
        self.assertEqual(project["repo_path"], "/repos/legacy")

        # 遷移完之後這張表真的能用（不只是存在）
        rid = repo.create_work_repo(7, name="客服後端", repo_path="/repos/cs-backend")
        self.assertEqual(repo.get_work_repo(7, rid)["name"], "客服後端")

    def test_migration_is_idempotent(self):
        """init_db() 跑兩次不會清掉已經寫進去的登錄。"""
        db.init_db()
        vid = self.new_viewer()
        rid = repo.create_work_repo(vid, name="客服後端", repo_path="/repos/cs-backend")
        db.init_db()
        self.assertIsNotNone(repo.get_work_repo(vid, rid))

    def test_cascade_delete_with_viewer(self):
        db.init_db()
        vid = self.new_viewer()
        repo.create_work_repo(vid, name="客服後端", repo_path="/repos/cs-backend")
        db.execute("DELETE FROM viewers WHERE id = ?", (vid,))
        self.assertEqual(repo.list_work_repos(vid), [])


# --------------------------------------------------------------------------
# 存取層
# --------------------------------------------------------------------------


class TestWorkRepoRepository(TempDbCase):
    def setUp(self) -> None:
        super().setUp()
        db.init_db()
        self.vid = self.new_viewer("1")
        self.other = self.new_viewer("2")

    def test_create_and_get_round_trip(self):
        rid = repo.create_work_repo(
            self.vid,
            name="客服後端",
            repo_path="/repos/cs-backend",
            author_emails=["me@corp.com", "me@personal.dev"],
            branch_filter=["main", "release/uat"],
            default_zplanner_project_id=42,
            default_zplanner_project_key="PEI",
        )
        row = repo.get_work_repo(self.vid, rid)
        self.assertEqual(row["name"], "客服後端")
        self.assertEqual(row["repo_path"], "/repos/cs-backend")
        # JSON 欄位解開後是真的 list，不是字串
        self.assertEqual(row["author_emails"], ["me@corp.com", "me@personal.dev"])
        self.assertEqual(row["branch_filter"], ["main", "release/uat"])
        self.assertEqual(row["default_zplanner_project_id"], 42)
        self.assertEqual(row["default_zplanner_project_key"], "PEI")
        self.assertIs(row["enabled"], True)
        self.assertIsNone(row["last_verified_at"])
        self.assertIsNone(row["last_verify_error"])

    def test_defaults_are_empty_lists_not_none(self):
        """不給就是空陣列。`branch_filter=[]` 的意思是**掃全部分支**，
        所以它必須是一個存得下去的值，不能變成 NULL。"""
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        row = repo.get_work_repo(self.vid, rid)
        self.assertEqual(row["author_emails"], [])
        self.assertEqual(row["branch_filter"], [])
        self.assertIsNone(row["default_zplanner_project_id"])
        self.assertIsNone(row["default_zplanner_project_key"])

    def test_viewer_isolation(self):
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        self.assertIsNone(repo.get_work_repo(self.other, rid), "別人的 id 猜對了也讀不到")
        self.assertEqual(repo.list_work_repos(self.other), [])
        self.assertIsNone(repo.update_work_repo(self.other, rid, name="偷改"))
        self.assertFalse(repo.delete_work_repo(self.other, rid))
        self.assertEqual(repo.get_work_repo(self.vid, rid)["name"], "A", "原值不該被動到")

    def test_list_orders_by_name_and_filters_enabled(self):
        repo.create_work_repo(self.vid, name="B 專案", repo_path="/repos/b")
        a = repo.create_work_repo(self.vid, name="A 專案", repo_path="/repos/a")
        repo.update_work_repo(self.vid, a, enabled=False)
        names = [r["name"] for r in repo.list_work_repos(self.vid)]
        self.assertEqual(names, ["A 專案", "B 專案"])
        self.assertEqual(
            [r["name"] for r in repo.list_work_repos(self.vid, enabled_only=True)],
            ["B 專案"],
        )

    def test_update_partial_keeps_untouched_fields(self):
        rid = repo.create_work_repo(
            self.vid,
            name="A",
            repo_path="/repos/a",
            author_emails=["me@corp.com"],
            branch_filter=["main"],
            default_zplanner_project_id=42,
            default_zplanner_project_key="PEI",
        )
        updated = repo.update_work_repo(self.vid, rid, name="A 改名")
        self.assertEqual(updated["name"], "A 改名")
        self.assertEqual(updated["repo_path"], "/repos/a")
        self.assertEqual(updated["author_emails"], ["me@corp.com"])
        self.assertEqual(updated["branch_filter"], ["main"])
        self.assertEqual(updated["default_zplanner_project_id"], 42)
        self.assertEqual(updated["default_zplanner_project_key"], "PEI")

    def test_update_lists_are_whole_replacement(self):
        """整組取代，不是逐項合併——否則「把某個舊信箱拿掉」沒辦法表達。"""
        rid = repo.create_work_repo(
            self.vid, name="A", repo_path="/repos/a",
            author_emails=["old@corp.com", "me@corp.com"], branch_filter=["main"],
        )
        updated = repo.update_work_repo(self.vid, rid, author_emails=["me@corp.com"])
        self.assertEqual(updated["author_emails"], ["me@corp.com"])
        # 清空回「掃全部分支」也要表達得出來
        updated = repo.update_work_repo(self.vid, rid, branch_filter=[])
        self.assertEqual(updated["branch_filter"], [])

    def test_zplanner_fields_use_unset_sentinel(self):
        """沒傳 ＝ 不改；傳 `None` ＝ 清除。

        這兩個欄位的 `None` 有實際語意（「這個 repo 沒有預設的 ZPlanner 專案」
        是使用者會主動選的狀態），所以不能沿用「None ＝ 不改」的舊慣例。
        """
        rid = repo.create_work_repo(
            self.vid, name="A", repo_path="/repos/a",
            default_zplanner_project_id=42, default_zplanner_project_key="PEI",
        )
        kept = repo.update_work_repo(self.vid, rid, name="A2")
        self.assertEqual(kept["default_zplanner_project_id"], 42)
        self.assertEqual(kept["default_zplanner_project_key"], "PEI")

        cleared = repo.update_work_repo(
            self.vid, rid,
            default_zplanner_project_id=None, default_zplanner_project_key=None,
        )
        self.assertIsNone(cleared["default_zplanner_project_id"])
        self.assertIsNone(cleared["default_zplanner_project_key"])

    def test_update_touches_updated_at(self):
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        before = repo.get_work_repo(self.vid, rid)
        with mock.patch.object(repo, "_now", return_value="2099-01-01T00:00:00+00:00"):
            after = repo.update_work_repo(self.vid, rid, name="A2")
        self.assertNotEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(after["created_at"], before["created_at"], "created_at 不該被動")

    def test_find_by_name_and_path(self):
        repo.create_work_repo(self.vid, name="客服後端", repo_path="/repos/cs-backend")
        self.assertIsNotNone(repo.find_work_repo_by_name(self.vid, "客服後端"))
        self.assertIsNotNone(repo.find_work_repo_by_name(self.vid, "  客服後端  "))
        self.assertIsNone(repo.find_work_repo_by_name(self.vid, "不存在"))
        self.assertIsNotNone(repo.find_work_repo_by_path(self.vid, "/repos/cs-backend"))
        self.assertIsNone(repo.find_work_repo_by_path(self.vid, "/repos/other"))
        # 查名稱／路徑同樣不跨 Viewer
        self.assertIsNone(repo.find_work_repo_by_name(self.other, "客服後端"))
        self.assertIsNone(repo.find_work_repo_by_path(self.other, "/repos/cs-backend"))

    def test_delete(self):
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        self.assertTrue(repo.delete_work_repo(self.vid, rid))
        self.assertFalse(repo.delete_work_repo(self.vid, rid))
        self.assertIsNone(repo.get_work_repo(self.vid, rid))

    def test_record_verification(self):
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        repo.record_work_repo_verification(self.vid, rid, "路徑不存在或不是資料夾")
        row = repo.get_work_repo(self.vid, rid)
        self.assertIsNotNone(row["last_verified_at"])
        self.assertEqual(row["last_verify_error"], "路徑不存在或不是資料夾")
        repo.record_work_repo_verification(self.vid, rid, None)
        self.assertIsNone(repo.get_work_repo(self.vid, rid)["last_verify_error"])

    def test_record_verification_does_not_cross_viewers(self):
        rid = repo.create_work_repo(self.vid, name="A", repo_path="/repos/a")
        repo.record_work_repo_verification(self.other, rid, "壞了")
        self.assertIsNone(repo.get_work_repo(self.vid, rid)["last_verify_error"])

    def test_assert_unique_helper(self):
        rid = repo.create_work_repo(self.vid, name="客服後端", repo_path="/repos/cs-backend")
        with self.assertRaises(InvalidParameter):
            server._assert_work_repo_unique(
                self.vid, name="客服後端", repo_path=None, exclude_id=None
            )
        with self.assertRaises(InvalidParameter) as ctx:
            server._assert_work_repo_unique(
                self.vid, name=None, repo_path="/repos/cs-backend", exclude_id=None
            )
        self.assertIn("算兩遍", str(ctx.exception), "訊息要說清楚為什麼路徑不能重複")
        # 更新自己時不算衝突
        server._assert_work_repo_unique(
            self.vid, name="客服後端", repo_path="/repos/cs-backend", exclude_id=rid
        )

    def test_require_work_repo_raises_not_found(self):
        with self.assertRaises(WorkRepoNotFound):
            server._require_work_repo(self.vid, 9999)


# --------------------------------------------------------------------------
# 錯誤型別
# --------------------------------------------------------------------------


class TestWorkRepoErrors(unittest.TestCase):
    def test_not_found_contract(self):
        err = WorkRepoNotFound()
        self.assertEqual(err.code, "WORK_REPO_NOT_FOUND")
        self.assertEqual(err.http_status, 404)
        self.assertEqual(
            err.to_dict(), {"error": {"code": "WORK_REPO_NOT_FOUND", "message": err.message}}
        )

    def test_unavailable_contract(self):
        err = WorkRepoUnavailable()
        self.assertEqual(err.code, "WORK_REPO_UNAVAILABLE")
        self.assertEqual(err.http_status, 409)

    def test_codes_are_distinct_from_code_project_ones(self):
        """刻意與參考專案那兩個分開：兩份登錄、兩個設定頁，下一步動作不同。"""
        from core.errors import CodeProjectNotFound, CodeProjectUnavailable

        self.assertNotEqual(WorkRepoNotFound.code, CodeProjectNotFound.code)
        self.assertNotEqual(WorkRepoUnavailable.code, CodeProjectUnavailable.code)
        # 但狀態碼刻意相同——語意同族（找不到 / 東西在但不能用）
        self.assertEqual(WorkRepoUnavailable.http_status, CodeProjectUnavailable.http_status)


# --------------------------------------------------------------------------
# API 層的輸入整理
# --------------------------------------------------------------------------


class TestWorkRepoInputCleaning(unittest.TestCase):
    def test_strips_dedupes_and_keeps_order(self):
        out = server._clean_str_list(
            ["  me@corp.com ", "", "me@personal.dev", "me@corp.com", "   "],
            label="作者信箱",
        )
        self.assertEqual(out, ["me@corp.com", "me@personal.dev"])

    def test_none_becomes_empty_list(self):
        self.assertEqual(server._clean_str_list(None, label="作者信箱"), [])

    def test_rejects_control_characters(self):
        for bad in ["a@b.com\nrm", "a@b.com\x00", "a\rb@example.com"]:
            with self.assertRaises(InvalidParameter):
                server._clean_str_list([bad], label="作者信箱")

    def test_trailing_whitespace_is_stripped_not_rejected(self):
        """結尾的換行只是使用者從別處貼過來的空白，剝掉就好，不必報錯。
        真正要擋的是**夾在中間**的控制字元（那代表有人在拼字串）。"""
        self.assertEqual(
            server._clean_str_list(["a@b.com\r\n", "\ta@b.com "], label="作者信箱"),
            ["a@b.com"],
        )

    def test_repo_path_rules(self):
        server._validate_repo_path(None)  # 沒帶就不驗（PATCH 用）
        server._validate_repo_path("/repos/a", label="工作 repo 的路徑")
        for bad in ["", "   ", "repos/a", "/repos/\x00a"]:
            with self.assertRaises(InvalidParameter):
                server._validate_repo_path(bad, label="工作 repo 的路徑")

    def test_repo_path_message_uses_label(self):
        """訊息的主詞要對：參考專案與工作 repo 是兩個不同的設定頁。"""
        with self.assertRaises(InvalidParameter) as ctx:
            server._validate_repo_path("repos/a", label="工作 repo 的路徑")
        self.assertIn("工作 repo 的路徑", str(ctx.exception))
        with self.assertRaises(InvalidParameter) as ctx:
            server._validate_repo_path("repos/a")
        self.assertIn("專案路徑", str(ctx.exception))


# --------------------------------------------------------------------------
# verify：路徑還是不是一個可讀的 git repo
# --------------------------------------------------------------------------


def _git_env(home: str) -> dict:
    """把 git 與使用者自己的設定隔離開，測試才不會被個人 gitconfig 影響。"""
    env = dict(os.environ)
    env.update(
        {
            "HOME": home,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.path.join(home, "nonexistent-gitconfig"),
            "GIT_AUTHOR_NAME": "測試",
            "GIT_COMMITTER_NAME": "測試",
            "GIT_COMMITTER_EMAIL": "committer@example.com",
        }
    )
    return env


@unittest.skipUnless(shutil.which("git"), "環境沒有 git")
class TestVerifyWorkRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-work-repo-git-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.repo_path = os.path.join(self.root, "repo")
        os.makedirs(self.repo_path)
        self.env = _git_env(self.root)
        self._run("init", "-q")
        self._run("config", "user.name", "測試")
        self._run("config", "user.email", "committer@example.com")
        self._run("config", "commit.gpgsign", "false")
        # 三筆 commit，作者信箱刻意包含一組只差大小寫的
        self._commit("第一筆", "a@example.com")
        self._commit("第二筆", "b@example.com")
        self._commit("第三筆", "B@Example.com")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"git {' '.join(args)} 失敗：{proc.stderr}")
        return proc

    def _commit(self, subject: str, author_email: str) -> None:
        env = dict(self.env)
        env["GIT_AUTHOR_EMAIL"] = author_email
        proc = subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", subject],
            cwd=self.repo_path,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_real_repo_is_ok(self):
        result = server._verify_work_repo(self.repo_path)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])
        self.assertTrue(result["head_sha"])
        self.assertLessEqual(len(result["head_sha"]), 40)

    def test_detected_emails_dedupe_and_order(self):
        """最近用過的信箱排前面，只差大小寫的視為同一個。

        這份清單是**參考**：使用者拿它填 `author_emails`，系統不自動寫進去。
        """
        result = server._verify_work_repo(self.repo_path)
        self.assertEqual(result["detected_emails"], ["B@Example.com", "a@example.com"])

    def test_missing_path(self):
        result = server._verify_work_repo(os.path.join(self.root, "not-there"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "路徑不存在或不是資料夾")
        self.assertIsNone(result["head_sha"])
        self.assertEqual(result["detected_emails"], [])

    def test_directory_that_is_not_a_repo(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        result = server._verify_work_repo(plain)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "這個路徑不是 git repo")

    def test_empty_path(self):
        result = server._verify_work_repo("")
        self.assertFalse(result["ok"])

    def test_repo_without_commits_is_still_ok(self):
        """剛 git init、還沒有任何 commit 的 repo 也是 repo，不該判成壞掉。"""
        fresh = os.path.join(self.root, "fresh")
        os.makedirs(fresh)
        subprocess.run(["git", "init", "-q"], cwd=fresh, env=self.env, check=True)
        result = server._verify_work_repo(fresh)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["head_sha"])
        self.assertEqual(result["detected_emails"], [])

    def test_does_not_touch_working_tree(self):
        """ADR-0006 鐵律：讀 git 一律不碰工作區。

        先弄髒工作區（一個未追蹤檔＋一個已修改檔），跑完 verify 之後
        `git status --porcelain` 必須逐字相同——沒有 checkout、沒有 stage、
        沒有把誰的修改吃掉。
        """
        tracked = os.path.join(self.repo_path, "tracked.txt")
        with open(tracked, "w", encoding="utf-8") as f:
            f.write("v1\n")
        self._run("add", "tracked.txt")
        self._commit("加一個檔", "a@example.com")
        with open(tracked, "w", encoding="utf-8") as f:
            f.write("未提交的修改\n")
        with open(os.path.join(self.repo_path, "untracked.txt"), "w", encoding="utf-8") as f:
            f.write("還沒 add\n")

        before = self._run("status", "--porcelain").stdout
        head_before = self._run("rev-parse", "HEAD").stdout
        self.assertTrue(before.strip(), "前置條件：工作區要真的是髒的")

        result = server._verify_work_repo(self.repo_path)
        self.assertTrue(result["ok"])

        self.assertEqual(self._run("status", "--porcelain").stdout, before)
        self.assertEqual(self._run("rev-parse", "HEAD").stdout, head_before)
        with open(tracked, encoding="utf-8") as f:
            self.assertEqual(f.read(), "未提交的修改\n")

    def test_uses_shared_git_conventions(self):
        """git 呼叫沿用 `code_search._git`，不是另外複製一份。

        那裡把 argv list、`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`
        與逾時綁在一起；複製出去就會有第二個能各自漂移的地方。
        """
        from core import code_search

        with mock.patch.object(
            code_search, "_git", wraps=code_search._git
        ) as spy:
            server._verify_work_repo(self.repo_path)
        self.assertTrue(spy.called)
        for call in spy.call_args_list:
            # 一律 argv 形式的多個位置參數，不是一整條 shell 字串
            self.assertGreaterEqual(len(call.args), 2)
            for arg in call.args[1:]:
                self.assertIsInstance(arg, str)
                self.assertNotIn("|", arg, "不要在 shell 裡接管線，去重在 Python 做")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
