"""Viewer 自己的顯示名稱：不可以是一串 `users/1098…`。

**這個 bug 的形狀值得記住。** 舊的三 scope token 沒有 userinfo 權限，
`identity.resolve()` 那條 fallback 路徑就拿 user_id 當 display_name 回傳。
它不會報錯，只是畫面右上角變成 `users/109827265019732088641`；
更糟的是 server 登入時還會 `directory.remember(user_id, display_name)`，
把同一串寫進**人名名錄**——於是連對話裡的「我（users/1098…）」也是這麼來的，
而那份名錄是摘要與草稿都在用的。

三道防線都要有測試：
  1. 產生端（identity）不再回傳 id 當名字
  2. 寫入端（directory.remember）擋掉 id 形狀的名字
  3. 讀取端（viewer_display_name）擋掉資料庫裡已經有的舊資料，並修回來

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import directory, identity  # noqa: E402
from dashboard.api import server  # noqa: E402

ME = "users/109827265019732088641"


class TestLooksLikeUserId(unittest.TestCase):
    def test_detects_the_resource_name(self):
        self.assertTrue(directory.looks_like_user_id(ME))
        self.assertTrue(directory.looks_like_user_id("  users/123  "))

    def test_real_names_pass_through(self):
        for name in ("鄭浩宇", "Sica Lee", "markcheng00806@intumit.com", "user"):
            self.assertFalse(directory.looks_like_user_id(name), name)

    def test_empty_is_not_an_id(self):
        self.assertFalse(directory.looks_like_user_id(None))
        self.assertFalse(directory.looks_like_user_id(""))


class TestIdentityFallback(unittest.TestCase):
    """沒有 userinfo 權限時，寧可沒有名字，也不要拿 id 充數。"""

    def _resolve(self, email=""):
        with mock.patch.object(identity, "fetch_userinfo", lambda creds: None), \
             mock.patch.object(cfg, "BOOTSTRAP_USER_ID", ME), \
             mock.patch.object(cfg, "BOOTSTRAP_EMAIL", email):
            return identity.resolve(object())

    def test_display_name_is_none_not_the_user_id(self):
        out = self._resolve()
        self.assertEqual(out["google_user_id"], ME)
        self.assertIsNone(out["display_name"])

    def test_bootstrap_email_is_used_when_given(self):
        out = self._resolve(email="me@example.com")
        self.assertEqual(out["display_name"], "me@example.com")

    def test_bare_numeric_id_is_normalised(self):
        with mock.patch.object(identity, "fetch_userinfo", lambda creds: None), \
             mock.patch.object(cfg, "BOOTSTRAP_USER_ID", "109827265019732088641"), \
             mock.patch.object(cfg, "BOOTSTRAP_EMAIL", ""):
            self.assertEqual(identity.resolve(object())["google_user_id"], ME)

    def test_real_userinfo_still_wins(self):
        info = {"sub": "109827265019732088641", "email": "a@b.c", "name": "鄭浩宇"}
        with mock.patch.object(identity, "fetch_userinfo", lambda creds: info):
            out = identity.resolve(object())
        self.assertEqual(out["display_name"], "鄭浩宇")


class TestRememberRejectsIds(unittest.TestCase):
    def test_user_id_shaped_name_is_not_written(self):
        """寫進去會蓋掉從 annotation 學到的真名字（那個 INSERT 是 upsert）。"""
        with mock.patch.object(directory.db, "execute") as ex:
            directory.remember(ME, ME, source="identity")
        ex.assert_not_called()

    def test_a_real_name_is_written(self):
        with mock.patch.object(directory.db, "execute") as ex:
            directory.remember(ME, "鄭浩宇", source="identity")
        ex.assert_called_once()

    def test_empty_name_is_not_written(self):
        with mock.patch.object(directory.db, "execute") as ex:
            directory.remember(ME, "", source="identity")
        ex.assert_not_called()


class TestLoadAllFiltersPoisonedRows(unittest.TestCase):
    """名錄唯一的出口，濾在這裡就等於所有讀取端都乾淨。

    舊資料還在資料庫裡，所以光是修寫入端不夠。
    """

    def _rows(self, rows):
        return mock.patch.object(directory.db, "query_all", lambda *a, **k: rows)

    def test_user_id_shaped_names_are_dropped(self):
        with self._rows([{"user_id": ME, "display_name": ME}]):
            self.assertEqual(directory.load_all(), {})

    def test_real_names_survive(self):
        with self._rows(
            [
                {"user_id": ME, "display_name": ME},
                {"user_id": "users/2", "display_name": "李姿誼"},
            ]
        ):
            self.assertEqual(directory.load_all(), {"users/2": "李姿誼"})

    def test_resolver_degrades_to_short_code_not_the_raw_id(self):
        """濾掉之後要退回「成員…8641」，不是把 id 印出來。"""
        with self._rows([{"user_id": "users/109827265019732088641", "display_name": ME}]):
            r = directory.make_resolver()
            self.assertEqual(r(ME), "成員…8641")

    def test_self_label_has_no_parenthetical_id(self):
        """「我（users/1098…）」就是這樣長出來的。"""
        with self._rows([{"user_id": ME, "display_name": ME}]):
            self.assertEqual(directory.make_resolver(ME)(ME), "我")


class TestViewerDisplayName(unittest.TestCase):
    def _name(self, viewer, table=None):
        with mock.patch.object(directory, "load_all", lambda: table or {}):
            return server.viewer_display_name(viewer)

    def test_a_stored_real_name_wins(self):
        v = {"google_user_id": ME, "display_name": "鄭浩宇", "email": "a@b.c"}
        self.assertEqual(self._name(v, {ME: "別的名字"}), "鄭浩宇")

    def test_a_stored_user_id_falls_back_to_the_directory(self):
        """就是畫面上那個 bug 的情況。"""
        v = {"google_user_id": ME, "display_name": ME, "email": "a@b.c"}
        self.assertEqual(self._name(v, {ME: "鄭浩宇"}), "鄭浩宇")

    def test_falls_back_to_email_when_nobody_knows_the_name(self):
        v = {"google_user_id": ME, "display_name": ME, "email": "a@b.c"}
        self.assertEqual(self._name(v, {}), "a@b.c")

    def test_returns_none_when_there_is_nothing_usable(self):
        v = {"google_user_id": ME, "display_name": ME, "email": None}
        self.assertIsNone(self._name(v, {}))

    def test_never_returns_the_user_id(self):
        for table in ({}, {ME: ME}):
            v = {"google_user_id": ME, "display_name": ME, "email": None}
            self.assertNotEqual(self._name(v, table), ME)

    def test_directory_failure_degrades_to_email(self):
        v = {"google_user_id": ME, "display_name": ME, "email": "a@b.c"}

        def boom():
            raise RuntimeError("DB 掛了")

        with mock.patch.object(directory, "load_all", boom):
            self.assertEqual(server.viewer_display_name(v), "a@b.c")


class TestViewerPublicRepairs(unittest.TestCase):
    def test_it_writes_the_good_name_back(self):
        """資料庫裡那筆壞的要修回來，不然每次讀都要重查一次名錄。"""
        v = {"id": 1, "google_user_id": ME, "display_name": ME, "email": "a@b.c"}
        with mock.patch.object(directory, "load_all", lambda: {ME: "鄭浩宇"}), \
             mock.patch.object(server.repo, "upsert_viewer") as up:
            out = server._viewer_public(v)
        self.assertEqual(out["display_name"], "鄭浩宇")
        up.assert_called_once_with(ME, "a@b.c", "鄭浩宇")

    def test_it_does_not_touch_a_legitimate_name(self):
        """使用者本來就正確的名字不可以被名錄蓋掉。"""
        v = {"id": 1, "google_user_id": ME, "display_name": "我取的名字", "email": "a@b.c"}
        with mock.patch.object(directory, "load_all", lambda: {ME: "鄭浩宇"}), \
             mock.patch.object(server.repo, "upsert_viewer") as up:
            out = server._viewer_public(v)
        self.assertEqual(out["display_name"], "我取的名字")
        up.assert_not_called()

    def test_repair_failure_does_not_break_login(self):
        v = {"id": 1, "google_user_id": ME, "display_name": ME, "email": "a@b.c"}
        with mock.patch.object(directory, "load_all", lambda: {ME: "鄭浩宇"}), \
             mock.patch.object(server.repo, "upsert_viewer", side_effect=RuntimeError):
            out = server._viewer_public(v)
        self.assertEqual(out["display_name"], "鄭浩宇")


if __name__ == "__main__":
    unittest.main(verbosity=2)
