"""「Viewer 自己選 model」的 core 層：供應商註冊表、偏好欄位與遷移。

這個功能最容易壞的不是「有沒有做到」，是**兩種模型字串被混用**：

  - **裸值**：傳給供應商的值本身（`opus`、`gemini-3.6-flash`）。
  - **識別字**：記進 `token_usage.model` 的值。ClaudeCLI 會加前綴變成
    `claude-cli:opus`。

把識別字當裸值往回傳，會組出 `--model claude-cli:opus`，而且**要到真的執行
`claude` 才會失敗**——測試裡看起來一切正常。所以下面每一條跟 model 有關的
斷言都同時檢查「裸值沒有前綴」與「識別字有前綴」，不是只檢查其中一邊。

另外守住三件事：
  1. `auto` ＋ 指定 model 要當場拋，不能放行。`auto` 自己挑供應商，把 `opus`
     餵給 Gemini 會壞，而那要到呼叫 API 才看得出來（專案硬規則：認不出來就拋）。
  2. `describe()` 的新鍵要**每一條路徑都給滿**，包含初始化失敗的 fallback——
     少給鍵不會讓前端顯示「這家壞了」，只會讓它讀到 undefined。
  3. 偏好的**讀取**路徑遇到壞 JSON 要退化成「沒有偏好」，**寫入**路徑遇到
     型別不對要拋。兩邊方向相反是刻意的，理由寫在 repository 的 docstring。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_provider_model_choice*.py'
"""

import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, providers, repository as repo  # noqa: E402
from core.errors import InvalidParameter  # noqa: E402
from core.providers.claude_cli import ClaudeCLIProvider  # noqa: E402
from core.providers.gemini import GeminiProvider  # noqa: E402
from mcp_app import mcp_server  # noqa: E402


# --------------------------------------------------------------------------
# 一、供應商註冊表
# --------------------------------------------------------------------------


class TestResolvePassesModel(unittest.TestCase):
    """`resolve(..., model=…)` 要真的把值送進實作的建構子。"""

    def test_concrete_provider_receives_model(self):
        p = providers.resolve("claude_cli", model="sonnet")
        self.assertEqual(p.model_id, "sonnet", "裸值要原樣收到")
        self.assertEqual(p.model, "claude-cli:sonnet", "識別字才帶前綴")

    def test_model_reaches_the_cli_arguments_without_prefix(self):
        """光看 property 不夠——要確認送進 CLI 的那個 argv 也是裸值。

        這是本功能唯一會「安靜地錯」的地方：`--model claude-cli:sonnet`
        在單元測試裡不會有任何症狀，要真的開子行程才會失敗。
        """
        argv = providers.resolve("claude_cli", model="sonnet")._argv(None, stream=False)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertNotIn("claude-cli:sonnet", argv)

    def test_gemini_receives_model(self):
        p = providers.resolve("gemini", model="gemini-experimental")
        self.assertEqual(p.model_id, "gemini-experimental")
        # Gemini 沒有前綴，識別字與裸值相同
        self.assertEqual(p.model, "gemini-experimental")

    def test_no_model_falls_back_to_config_default(self):
        p = providers.resolve("claude_cli")
        self.assertEqual(p.model_id, cfg.CLAUDE_CLI_MODEL)

    def test_blank_model_is_treated_as_unspecified(self):
        """API 與 CLI 的「沒填」都是空字串，原樣往下傳會變成 `--model ''`。"""
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                p = providers.resolve("claude_cli", model=blank)
                self.assertEqual(p.model_id, cfg.CLAUDE_CLI_MODEL)

    def test_surrounding_whitespace_is_stripped(self):
        """自訂輸入框最常見的髒值就是尾端空白。"""
        self.assertEqual(providers.resolve("claude_cli", model=" haiku ").model_id, "haiku")

    def test_single_candidate_alias_accepts_model(self):
        """`claude` 底下只有一個實作，指定 model 沒有歧義，要放行。"""
        with mock.patch.object(
            ClaudeCLIProvider, "available", return_value=(True, "測試環境")
        ):
            p = providers.resolve("claude", model="haiku")
        self.assertIsInstance(p, ClaudeCLIProvider)
        self.assertEqual(p.model_id, "haiku")


class TestAutoRejectsModel(unittest.TestCase):
    """多候選別名 ＋ 指定 model ＝ 當場拋，不進入 fallback 迴圈。"""

    def test_auto_with_model_raises(self):
        with self.assertRaises(InvalidParameter):
            providers.resolve("auto", model="opus")

    def test_auto_without_model_still_works(self):
        """這條是正對照：確認上面那個紅燈是 model 造成的，不是 `auto` 本身壞了。"""
        with mock.patch.object(
            ClaudeCLIProvider, "available", return_value=(True, "測試環境")
        ):
            self.assertIsInstance(providers.resolve("auto"), ClaudeCLIProvider)

    def test_auto_with_model_does_not_build_any_provider(self):
        """要在**建構之前**擋下來：否則會先開一個註定用不到的供應商實例。"""
        with mock.patch.object(
            providers, "_build", side_effect=AssertionError("不該建構任何供應商")
        ):
            with self.assertRaises(InvalidParameter):
                providers.resolve("auto", model="opus")

    def test_auto_with_blank_model_is_not_rejected(self):
        """空字串＝沒指定，不該被當成「指定了 model」而擋下 auto。"""
        with mock.patch.object(
            ClaudeCLIProvider, "available", return_value=(True, "測試環境")
        ):
            self.assertIsInstance(providers.resolve("auto", model=""), ClaudeCLIProvider)


class TestIsMultiCandidate(unittest.TestCase):
    """「這個名稱會不會挑選多個實作」的唯一判準。

    2026-09-17 驗收實測：dashboard 手抄了一份 `("auto",)`，在 `_ALIASES` 新增
    第二個多候選別名之後那份抄本不會跟著變，於是 `opus` 真的被送進 Gemini 而
    沒有任何一層擋下來。所以這裡除了驗現況，還要驗「**表變了判準要跟著變**」。
    """

    def test_auto_is_multi_candidate(self):
        self.assertTrue(providers.is_multi_candidate("auto"))

    def test_single_candidate_alias_is_not(self):
        self.assertFalse(providers.is_multi_candidate("claude"))

    def test_concrete_names_are_not(self):
        for name in providers.MODEL_CHOICES:
            with self.subTest(name=name):
                self.assertFalse(providers.is_multi_candidate(name))

    def test_unknown_name_is_not(self):
        """不認得的名稱由 resolve() 負責拒絕，這裡不搶著判。"""
        self.assertFalse(providers.is_multi_candidate("不存在的供應商"))

    def test_case_and_whitespace_are_normalised(self):
        self.assertTrue(providers.is_multi_candidate("  AUTO "))

    def test_none_follows_the_server_default(self):
        """「沒指定」不等於「不是別名」——伺服器預設本身就可能是 auto。"""
        with mock.patch.object(cfg, "AI_PROVIDER", "auto"):
            self.assertTrue(providers.is_multi_candidate(None))
        with mock.patch.object(cfg, "AI_PROVIDER", "claude_cli"):
            self.assertFalse(providers.is_multi_candidate(None))

    def test_a_new_multi_candidate_alias_is_picked_up(self):
        """在別名表加一條，判準要立刻跟著變——這正是手抄複本做不到的事。"""
        patched = dict(providers._ALIASES)
        patched["best"] = ["claude_cli", "gemini"]
        with mock.patch.object(providers, "_ALIASES", patched):
            self.assertTrue(providers.is_multi_candidate("best"))
            # 而且 resolve() 要用同一個判準，不是自己再寫一份條件
            with self.assertRaises(InvalidParameter):
                providers.resolve("best", model="opus")

    def test_a_new_single_candidate_alias_is_still_allowed(self):
        """正對照：新別名只有一個候選時不該被擋，否則上面那條只是「全擋」。"""
        patched = dict(providers._ALIASES)
        patched["local"] = ["claude_cli"]
        with mock.patch.object(providers, "_ALIASES", patched), mock.patch.object(
            ClaudeCLIProvider, "available", return_value=(True, "測試環境")
        ):
            self.assertFalse(providers.is_multi_candidate("local"))
            self.assertEqual(providers.resolve("local", model="haiku").model_id, "haiku")


class TestDescribeExposesModels(unittest.TestCase):
    def test_describe_has_model_id_and_models(self):
        d = ClaudeCLIProvider(model="sonnet").describe()
        self.assertEqual(d["model_id"], "sonnet")
        self.assertEqual(d["model"], "claude-cli:sonnet", "既有鍵的語意不可改變")
        self.assertIn("sonnet", d["models"])

    def test_models_is_a_plain_list(self):
        """`model_choices` 是 tuple，但 JSON 序列化要的是 list。"""
        self.assertIsInstance(ClaudeCLIProvider().describe()["models"], list)
        self.assertIsInstance(GeminiProvider().describe()["models"], list)

    def test_model_choices_contain_no_prefix(self):
        """白名單一律是裸值——混進識別字就會被原樣送回來當參數。"""
        for choices in providers.MODEL_CHOICES.values():
            for value in choices:
                self.assertNotIn(":", value, f"{value} 看起來像識別字而不是裸值")

    def test_describe_all_covers_every_implementation(self):
        described = providers.describe_all()
        self.assertEqual(
            {d["name"] for d in described}, set(providers.MODEL_CHOICES)
        )
        for d in described:
            self.assertIn("model_id", d)
            self.assertIn("models", d)

    def test_describe_all_fallback_branch_also_has_the_new_keys(self):
        """初始化失敗的那條路徑也要給滿鍵，否則前端讀到 undefined。"""
        with mock.patch.object(providers, "_build", side_effect=RuntimeError("壞了")):
            described = providers.describe_all()
        self.assertTrue(described)
        for d in described:
            self.assertFalse(d["available"])
            self.assertEqual(d["model_id"], "?")
            self.assertEqual(d["models"], [])
            # 初始化都失敗了不該宣稱支援視覺：呼叫端會拿它決定要不要先下載附件
            self.assertFalse(d["supports_vision"])

    def test_fallback_branch_has_exactly_the_same_keys_as_describe(self):
        """兩條路徑的鍵要一模一樣。

        逐個列舉鍵名會讓「日後又加一個鍵、只加在正常路徑」重演——這次補
        `supports_vision` 就是上一輪只補了兩個鍵留下的。改成比對鍵的集合，
        下次漏了會當場紅燈。
        """
        healthy = {frozenset(d) for d in providers.describe_all()}
        with mock.patch.object(providers, "_build", side_effect=RuntimeError("壞了")):
            broken = {frozenset(d) for d in providers.describe_all()}
        self.assertEqual(healthy, broken, "fallback 分支與 describe() 的鍵不一致")

    def test_model_choices_matches_the_classes(self):
        self.assertEqual(
            providers.MODEL_CHOICES["claude_cli"], ClaudeCLIProvider.model_choices
        )
        self.assertEqual(providers.MODEL_CHOICES["gemini"], GeminiProvider.model_choices)


# --------------------------------------------------------------------------
# 二、偏好欄位與遷移
# --------------------------------------------------------------------------


class TestMcpRejectsBadComboBeforeFetching(unittest.TestCase):
    """`auto` ＋ model 必定失敗，不可以先白抓一次訊息才發現。

    一次 `fetch_recent_messages()` 是一次 Google Chat API 呼叫（配額）＋ Viewer
    的等待時間，而這個組合換不到任何東西。把檢查搬回訊息抓取之後，這一條會紅。
    """

    def summarize(self, **kwargs) -> tuple:
        """跑一次 summarize_chat_space，回傳 (輸出字串, 假的 chat client)。

        space 一律用完整 ID，`_resolve_space()` 才不會先打一次 API 去查名稱——
        否則就分不清「沒抓訊息」是因為擋得夠早，還是因為查名稱先失敗了。
        """
        client = mock.MagicMock()
        client.fetch_recent_messages.return_value = []
        with mock.patch.object(mcp_server, "get_chat_client", return_value=client):
            out = mcp_server.summarize_chat_space("spaces/AAA", **kwargs)
        return out, client

    def test_auto_with_model_does_not_fetch_any_message(self):
        out, client = self.summarize(provider="auto", model="opus")
        client.fetch_recent_messages.assert_not_called()
        self.assertIn("INVALID_PARAMETER", out)

    def test_empty_provider_follows_the_server_default(self):
        """沒帶 provider 時算的是伺服器預設——預設是 auto 就一樣要擋。"""
        with mock.patch.object(cfg, "AI_PROVIDER", "auto"):
            out, client = self.summarize(model="opus")
        client.fetch_recent_messages.assert_not_called()
        self.assertIn("INVALID_PARAMETER", out)

    def test_a_legal_combination_still_fetches(self):
        """正對照：上面兩條的「沒抓訊息」不是因為這條路徑整個不通了。"""
        _, client = self.summarize(provider="claude_cli", model="sonnet")
        client.fetch_recent_messages.assert_called_once()

    def test_no_model_with_auto_still_fetches(self):
        """`auto` 本身合法，只有配上 model 才是非法組合。"""
        _, client = self.summarize(provider="auto")
        client.fetch_recent_messages.assert_called_once()


class TempDbCase(unittest.TestCase):
    """每個測試一個全新的暫存 SQLite 檔（作法同 tests/unit/test_work_repos.py）。

    順序不能換：先關掉 thread-local 連線，再蓋掉 cfg 的兩個路徑，最後 init_db()。
    少關一次連線就會寫到上一個測試的檔案，少蓋一次 DATA_DIR 就會在正式資料夾
    留下 token.key。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-model-choice-")
        self.tmpdir = self._tmp.name
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self._db_log = logging.getLogger("chatpulse.db")
        self._db_log_level = self._db_log.level
        self._db_log.setLevel(logging.WARNING)
        # 偏好裡的髒資料會印 WARNING，那在正式執行時是唯一的線索，但在測試輸出
        # 裡只會蓋掉真正的失敗訊息（下面有好幾條刻意餵壞資料）。
        # ⚠️ 只設 `propagate = False` **不夠**：沒有任何 handler 的 record 會走
        # logging 的 lastResort handler 直接印到 stderr。拉高 level 才是真的擋住。
        # `assertLogs` 會在自己的區塊內把 level 調回來，所以驗 log 那幾條照樣驗得到。
        self._repo_log = logging.getLogger("chatpulse.repository")
        self._repo_log_level = self._repo_log.level
        self._repo_log.setLevel(logging.ERROR)
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
        self._repo_log.setLevel(self._repo_log_level)
        self._tmp.cleanup()

    def new_viewer(self, suffix: str = "1") -> int:
        v = repo.upsert_viewer(
            f"users/{suffix.rjust(6, '0')}", f"v{suffix}@example.com", f"Viewer {suffix}"
        )
        return int(v["id"])


def preference_columns(path: str) -> set:
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(preferences)")}
    finally:
        con.close()


#: 「這次改動之前」的 preferences 長什麼樣：沒有 default_models_json。
#: 刻意手寫而不是複製當前的 `_SCHEMA`——複製的話這個測試會跟著被測的東西
#: 一起變，等於什麼都沒驗。
_OLD_SCHEMA = """
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
"""


class TestDefaultModelsMigration(TempDbCase):
    def test_fresh_db_has_the_column(self):
        db.init_db()
        self.assertIn("default_models_json", preference_columns(self.db_path))

    def test_schema_version_bumped(self):
        # 不釘死數字（同 test_work_repos.py 的理由：釘死會讓每次正常演進都變紅燈）
        self.assertGreaterEqual(db.SCHEMA_VERSION, 8)

    def test_migration_on_existing_db_keeps_data(self):
        """舊資料庫升級：欄位要長出來，既有偏好一格都不能掉。"""
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(_OLD_SCHEMA)
            con.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', '7')"
            )
            con.execute(
                "INSERT INTO viewers(id, google_user_id, email, display_name, created_at) "
                "VALUES(9, 'users/999', 'old@example.com', '既有 Viewer', "
                "'2026-01-01T00:00:00+00:00')"
            )
            con.execute(
                "INSERT INTO preferences(viewer_id, pinned_space_ids, default_style, "
                "default_provider, updated_at) "
                "VALUES(9, '[\"spaces/AAA\"]', 'technical', 'gemini', "
                "'2026-01-01T00:00:00+00:00')"
            )
            con.commit()
        finally:
            con.close()

        self.assertNotIn(
            "default_models_json",
            preference_columns(self.db_path),
            "前置條件：舊 DB 不該有這個欄位",
        )

        db.init_db()

        self.assertIn("default_models_json", preference_columns(self.db_path))
        prefs = repo.get_preferences(9)
        self.assertEqual(prefs["default_provider"], "gemini", "既有偏好不可被覆蓋")
        self.assertEqual(prefs["default_style"], "technical")
        self.assertEqual(prefs["pinned_space_ids"], ["spaces/AAA"])
        # 舊資料補上的欄位是 NULL，讀取端要當成「沒有偏好」而不是錯誤
        self.assertEqual(prefs["default_models"], {})

    def test_migration_is_repeatable(self):
        """遷移會在每次啟動時跑。第二次不可以因為欄位已存在而炸。"""
        db.init_db()
        vid = self.new_viewer()
        repo.set_default_models(vid, {"claude_cli": "sonnet"})
        db.init_db()
        db.init_db()
        self.assertEqual(repo.get_default_models(vid), {"claude_cli": "sonnet"})


class TestDefaultModelsRepository(TempDbCase):
    def setUp(self) -> None:
        super().setUp()
        db.init_db()
        self.viewer_id = self.new_viewer()

    def raw_json(self):
        row = db.query_one(
            "SELECT default_models_json FROM preferences WHERE viewer_id = ?",
            (self.viewer_id,),
        )
        return row["default_models_json"]

    # --- 沒設過 ---------------------------------------------------------

    def test_unset_returns_empty_dict(self):
        self.assertEqual(repo.get_default_models(self.viewer_id), {})
        self.assertIsNone(self.raw_json(), "沒設過就不該有值")

    def test_get_preferences_exposes_default_models(self):
        self.assertEqual(repo.get_preferences(self.viewer_id)["default_models"], {})
        repo.set_default_models(self.viewer_id, {"gemini": "gemini-3.6-flash"})
        self.assertEqual(
            repo.get_preferences(self.viewer_id)["default_models"],
            {"gemini": "gemini-3.6-flash"},
        )

    # --- 寫入 -----------------------------------------------------------

    def test_roundtrip(self):
        repo.set_default_models(
            self.viewer_id, {"claude_cli": "opus", "gemini": "gemini-3.6-flash"}
        )
        self.assertEqual(
            repo.get_default_models(self.viewer_id),
            {"claude_cli": "opus", "gemini": "gemini-3.6-flash"},
        )

    def test_write_is_a_full_overwrite(self):
        """契約是整份覆寫，不是合併——第二次沒帶的鍵要消失。"""
        repo.set_default_models(self.viewer_id, {"claude_cli": "opus", "gemini": "x"})
        repo.set_default_models(self.viewer_id, {"claude_cli": "haiku"})
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "haiku"})

    def test_empty_values_are_dropped(self):
        repo.set_default_models(
            self.viewer_id,
            {"claude_cli": "opus", "gemini": "", "other": None, "blank": "   "},
        )
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "opus"})
        self.assertNotIn("null", (self.raw_json() or ""), "不要把 null 存進 JSON")

    def test_clearing_everything_writes_null_not_empty_object(self):
        """「沒有偏好」在 DB 裡只能有一種樣子，否則日後查資料會漏判。"""
        repo.set_default_models(self.viewer_id, {"claude_cli": "opus"})
        repo.set_default_models(self.viewer_id, {})
        self.assertIsNone(self.raw_json())
        self.assertEqual(repo.get_default_models(self.viewer_id), {})

    def test_values_are_stripped(self):
        repo.set_default_models(self.viewer_id, {" claude_cli ": " opus "})
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "opus"})

    def test_non_string_value_raises(self):
        """寫入路徑遇到型別不對要拋——靜默跳過會讓前端顯示「已儲存」卻沒存。"""
        with self.assertRaises(InvalidParameter):
            repo.set_default_models(self.viewer_id, {"claude_cli": 123})

    def test_write_updates_the_timestamp(self):
        before = repo.get_preferences(self.viewer_id)["updated_at"]
        repo.set_default_models(self.viewer_id, {"claude_cli": "opus"})
        self.assertIsNotNone(repo.get_preferences(self.viewer_id)["updated_at"])
        self.assertIsNotNone(before)

    def test_write_does_not_disturb_other_preferences(self):
        repo.update_preferences(self.viewer_id, default_provider="gemini", default_style="technical")
        repo.set_default_models(self.viewer_id, {"claude_cli": "opus"})
        prefs = repo.get_preferences(self.viewer_id)
        self.assertEqual(prefs["default_provider"], "gemini")
        self.assertEqual(prefs["default_style"], "technical")

    def test_update_preferences_does_not_clobber_default_models(self):
        """既有的偏好更新路徑沒有帶這個欄位，不可以把它洗掉。"""
        repo.set_default_models(self.viewer_id, {"claude_cli": "opus"})
        repo.update_preferences(self.viewer_id, default_style="technical")
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "opus"})

    # --- 壞資料 ---------------------------------------------------------

    def write_raw(self, value):
        db.execute(
            "UPDATE preferences SET default_models_json = ? WHERE viewer_id = ?",
            (value, self.viewer_id),
        )

    def test_broken_json_degrades_to_no_preference(self):
        """讀取路徑不拋：偏好壞掉不該讓整個頁面打不開，而且 Viewer 自己修不了。"""
        for broken in ("{不是 JSON", "", "null", "[]", '"opus"', "123"):
            with self.subTest(broken=broken):
                self.write_raw(broken)
                self.assertEqual(repo.get_default_models(self.viewer_id), {})
                self.assertEqual(
                    repo.get_preferences(self.viewer_id)["default_models"], {}
                )

    def test_non_string_values_in_stored_json_are_ignored(self):
        """手改過的 DB 可能有數字或 null；它們會一路流進 --model。"""
        self.write_raw(json.dumps({"claude_cli": "opus", "gemini": 5, "x": None}))
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "opus"})

    def test_a_valid_value_still_reads_after_the_broken_cases(self):
        """正對照：上面那一串「回空 dict」不是因為讀取函式恆回空。"""
        self.write_raw(json.dumps({"claude_cli": "sonnet"}))
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "sonnet"})

    # --- 已被移除的供應商（讀取時要濾掉，否則 Viewer 從此存不了偏好）------

    def test_keys_of_removed_providers_are_filtered_out(self):
        """`claude_api` 已於 2026-09-05 移除，舊資料裡還留著它的鍵。"""
        self.write_raw(json.dumps({"claude_api": "opus-4", "claude_cli": "sonnet"}))
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "sonnet"})
        self.assertEqual(
            repo.get_preferences(self.viewer_id)["default_models"],
            {"claude_cli": "sonnet"},
            "兩條讀取路徑都要濾，只濾一條等於沒濾",
        )

    def test_alias_keys_are_filtered_out(self):
        """別名當鍵存進來也對應不回實作，同樣要濾掉。"""
        self.write_raw(json.dumps({"auto": "opus", "claude": "opus"}))
        self.assertEqual(repo.get_default_models(self.viewer_id), {})

    def test_the_stale_key_heals_itself_on_the_next_save(self):
        """自癒：讀取濾掉 → 前端拿到乾淨的一份 → 整份存回 → DB 裡的髒鍵消失。

        這是這個修法的重點。只在 API 那一層濾的話，DB 裡的髒鍵會永遠留著。
        """
        self.write_raw(json.dumps({"claude_api": "opus-4", "claude_cli": "sonnet"}))
        clean = repo.get_default_models(self.viewer_id)     # 前端拿到的內容
        repo.set_default_models(self.viewer_id, clean)      # 「設為預設」送整份
        self.assertNotIn("claude_api", self.raw_json() or "")
        self.assertEqual(repo.get_default_models(self.viewer_id), {"claude_cli": "sonnet"})

    def test_dropping_a_key_is_logged(self):
        """Viewer 只會看到「偏好自己不見了」，沒有 log 就查不出原因。"""
        self.write_raw(json.dumps({"claude_api": "opus-4"}))
        with self.assertLogs("chatpulse.repository", level="WARNING") as captured:
            repo.get_default_models(self.viewer_id)
        self.assertTrue(any("claude_api" in line for line in captured.output))

    def test_broken_json_is_logged(self):
        self.write_raw("{不是 JSON")
        with self.assertLogs("chatpulse.repository", level="WARNING"):
            repo.get_default_models(self.viewer_id)

    def test_a_clean_preference_logs_nothing(self):
        """正對照：上面那些 log 斷言不是因為這支函式每次都在印警告。"""
        self.write_raw(json.dumps({"claude_cli": "sonnet"}))
        logger = logging.getLogger("chatpulse.repository")
        with mock.patch.object(logger, "warning") as warn:
            self.assertEqual(
                repo.get_default_models(self.viewer_id), {"claude_cli": "sonnet"}
            )
        warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
