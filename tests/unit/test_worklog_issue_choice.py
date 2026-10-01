"""「記到哪張單」的選擇方式（ADR-0008 的 2026-10-01 補充）。

守的是五件「畫面看起來沒事」的壞法：

  1. **空查詢送進 ZPlanner**：它對空字串回 0 筆而不是全部，畫面就會說「沒有
     任何可記工時的單」——錯得很合理。
  2. **記憶變成預選**：「上次記到 XXX」被直接填進四欄，Viewer 一路按送出就送了
     系統猜的單（ADR-0008 的唯一硬規則）。正對照是「有記憶」本身——沒有記憶時
     四欄是 NULL 證明不了任何事。
  3. **記憶記錯地方**：跨兩個 repo 的段、或被拒絕的 PUT，留下一個指向錯的來源、
     或從沒存成功的選擇的記憶。
  4. **重產把 Viewer 的選擇丟掉**：同一段還在卻被清成 NULL。
  5. **預設專案加權套到別的 repo**：A repo 的段因為 B repo 設了預設專案而被加權，
     理由文字還寫「該 repo」。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`；ZPlanner 一律是假的
client，不會發出任何真的請求。假資料刻意不 import 其他測試檔（這個 repo 的慣例）。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_issue_choice*.py'
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from core.errors import (  # noqa: E402
    ConfigurationError,
    InvalidParameter,
    WorklogAlreadySubmitted,
    ZPlannerApiError,
    ZPlannerUnavailable,
)
from core.worklog_evidence import DayEvidence, Evidence, extract_terms  # noqa: E402
from dashboard.api import server  # noqa: E402

DATE = "2026-09-13"
TZ = "+08:00"
ATTRIBUTION = ("issue_id", "issue_key", "project_id", "project_key")


# --------------------------------------------------------------------------
# 假資料
# --------------------------------------------------------------------------


def commit(sha: str, subject: str, *, repo_name: str = "cs-backend") -> Evidence:
    return Evidence(
        kind="commit",
        ref=f"commit:{sha}",
        occurred_at=f"{DATE}T10:00:00+08:00",
        title=subject,
        source_label=repo_name,
        terms=tuple(extract_terms(subject)),
        weight_input=1.0,
        sha=sha,
        short_sha=sha[:7],
        author_email="me@corp.com",
    )


def chat(mid: str, text: str, *, space_id: str = "spaces/AAA", name: str = "客服專案群") -> Evidence:
    return Evidence(
        kind="chat",
        ref=f"chat:{space_id}/messages/{mid}",
        occurred_at=f"{DATE}T11:00:00+08:00",
        title=text[:80],
        source_label=name,
        terms=tuple(extract_terms(text)),
        weight_input=1.0,
        space_id=space_id,
        message_name=f"{space_id}/messages/{mid}",
        char_count=len(text),
    )


def day_evidence(*items) -> DayEvidence:
    return DayEvidence(
        work_date=DATE,
        tz_offset=TZ,
        since=f"{DATE}T00:00:00+08:00",
        until="2026-09-14T00:00:00+08:00",
        evidence=tuple(items),
    )


class FakeZPlanner:
    """唯讀的假 client。`queries` 記下每一次真的「送出去」的關鍵字。"""

    def __init__(self, *, ok: bool = True, issues: Any = None, error: Exception = None):
        self._ok = ok
        self._issues = issues if issues is not None else []
        self._error = error
        self.queries: List[str] = []

    def available(self):
        return (True, "使用 ZPLANNER_APIKEY") if self._ok else (False, "未設定 ZPLANNER_BASE_URL")

    def list_worklogable_issues(self, q: str):
        self.queries.append(q)
        if self._error is not None:
            raise self._error
        if not (q or "").strip():
            # 照真實 ZPlanner 的行為：空字串回 0 筆（不是全部）。測試靠 `queries`
            # 是否為空判斷有沒有被送出去，這裡不另外拋。
            return []
        return [i for i in self._issues if q.lower() in json.dumps(i, ensure_ascii=False).lower()]

    def list_project_members(self, project_id: int):
        return [{"user_email": "me@corp.com", "role": "developer"}]

    def list_project_role_permissions(self, project_id: int):
        return [{"role": "developer", "can_worklog": True}]


ISSUES = [
    {"id": 9, "key": "PEI-133", "title": "匯出效能", "project_id": 42, "project_key": "PEI"},
    {"id": 10, "key": "PEI-140", "title": "設定頁", "project_id": 42, "project_key": "PEI"},
    {"id": 20, "key": "OPS-7", "title": "設定備份", "project_id": 77, "project_key": "OPS"},
]


async def _collect(iterator):
    return [chunk async for chunk in iterator]


def _parse(frames) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for frame in frames:
        text = frame if isinstance(frame, str) else frame.decode("utf-8")
        for line in text.strip().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


class IssueChoiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-worklog-choice-")
        self.tmpdir = self._tmp.name
        self._db_log = logging.getLogger("chatpulse.db")
        self._db_log_level = self._db_log.level
        self._db_log.setLevel(logging.WARNING)
        db.close_connection()
        self._patches = [
            mock.patch.object(cfg, "DB_PATH", os.path.join(self.tmpdir, "test.db")),
            mock.patch.object(cfg, "DATA_DIR", self.tmpdir),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._restore)
        db.init_db()
        row = repo.upsert_viewer("users/000001", "me@corp.com", "我")
        self.vid = int(row["id"])
        self.viewer = {"id": self.vid, "google_user_id": "users/000001", "email": "me@corp.com"}
        # cs-backend 設了預設專案 42，chatpulse 沒設——第 5 條的對照組
        repo.create_work_repo(
            self.vid,
            name="cs-backend",
            repo_path="/repos/cs-backend",
            author_emails=["me@corp.com"],
            default_zplanner_project_id=42,
            default_zplanner_project_key="PEI",
        )
        repo.create_work_repo(
            self.vid, name="chatpulse", repo_path="/repos/chatpulse", author_emails=["me@corp.com"]
        )

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    # -- 端點 --------------------------------------------------------------

    def stream(self, evidence: DayEvidence, *, zplanner=None):
        client = zplanner if zplanner is not None else FakeZPlanner(ok=False)
        request = server.WorklogDraftRequest(tz_offset=TZ)
        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", lambda **kw: evidence
        ), mock.patch.object(server, "zplanner_client", lambda: client):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            return _parse(asyncio.run(_collect(response.body_iterator)))

    def search(self, q: str, *, zplanner):
        with mock.patch.object(server, "zplanner_client", lambda: zplanner):
            return server.search_worklog_issues(q=q, viewer=self.viewer)

    def put(self, entries: List[Dict[str, Any]]):
        body = server.WorklogDayUpdateBody(entries=entries)
        return server.update_worklog_day(DATE, body, self.viewer)

    def stored(self) -> Dict[str, Any]:
        """回頭查 DB。**回應說了什麼不算數，落地了什麼才算。**"""
        return repo.get_worklog_draft(self.vid, DATE)

    def entry_by_label(self, label: str) -> Dict[str, Any]:
        for e in server.get_worklog_day(DATE, self.viewer)["entries"]:
            if e["label"] == label:
                return e
        self.fail(f"找不到 label 為 {label} 的 entry")

    def memory(self, kind: str, source_id: str):
        return repo.get_worklog_source_memory(self.vid, [(kind, source_id)]).get((kind, source_id))


def pick(issue_id: int, issue_key: str, project_id: int = 42, project_key: str = "PEI") -> Dict[str, Any]:
    return {"issue_id": issue_id, "issue_key": issue_key, "project_id": project_id, "project_key": project_key}


# --------------------------------------------------------------------------
# 1. 搜尋 API
# --------------------------------------------------------------------------


class TestIssueSearch(IssueChoiceCase):
    def test_結果依專案分組(self):
        result = self.search("設定", zplanner=FakeZPlanner(issues=ISSUES))
        self.assertEqual(result["count"], 2)
        groups = {g["project_key"]: [i["issue_key"] for i in g["issues"]] for g in result["groups"]}
        self.assertEqual(groups, {"PEI": ["PEI-140"], "OPS": ["OPS-7"]})
        for group in result["groups"]:
            for issue in group["issues"]:
                self.assertEqual(issue["project_id"], group["project_id"])

    def test_單號也搜得到(self):
        result = self.search("PEI-133", zplanner=FakeZPlanner(issues=ISSUES))
        self.assertEqual([i["issue_id"] for g in result["groups"] for i in g["issues"]], [9])

    def test_空查詢被擋下而且沒有送出去(self):
        """🔴 ZPlanner 對空字串回 0 筆而不是全部；送出去就會得到錯誤的「沒有單」。"""
        for value in ("", "   ", "\t"):
            with self.subTest(q=value):
                client = FakeZPlanner(issues=ISSUES)
                with self.assertRaises(InvalidParameter):
                    self.search(value, zplanner=client)
                self.assertEqual(client.queries, [], "空查詢不准送到 ZPlanner")

    def test_正對照_有關鍵字就真的送出去(self):
        client = FakeZPlanner(issues=ISSUES)
        self.search("  匯出 ", zplanner=client)
        self.assertEqual(client.queries, ["匯出"], "前後空白去掉後送出")

    def test_ZPlanner_不可用時是錯誤不是空結果(self):
        client = FakeZPlanner(ok=False)
        with self.assertRaises(ConfigurationError):
            self.search("PEI", zplanner=client)
        self.assertEqual(client.queries, [])

    def test_查詢失敗照分類往上拋(self):
        with self.assertRaises(ZPlannerUnavailable):
            self.search("PEI", zplanner=FakeZPlanner(error=ZPlannerUnavailable("逾時")))

    def test_認不出_id_的項目要拋而不是少給一筆(self):
        client = FakeZPlanner(issues=[{"key": "PEI-1", "title": "沒有 id 的單"}])
        with self.assertRaises(ZPlannerApiError):
            self.search("PEI", zplanner=client)

    def test_未登入回_401_而且不碰_ZPlanner(self):
        """走真的 HTTP 路由（含 ViewerDep），不是直接呼叫函式——後者會繞過登入檢查。"""
        from fastapi.testclient import TestClient

        client = FakeZPlanner(issues=ISSUES)
        with mock.patch.object(server, "zplanner_client", lambda: client):
            # 不用 `with TestClient(...)`：那會跑 lifespan（啟動採集器），這裡用不到
            response = TestClient(server.app).get("/api/v1/worklogs/issue-search", params={"q": "PEI"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "NOT_AUTHENTICATED")
        self.assertEqual(client.queries, [])

    def test_正對照_有登入的_session_就查得到(self):
        from fastapi.testclient import TestClient

        token = repo.create_session(self.vid)
        client = FakeZPlanner(issues=ISSUES)
        with mock.patch.object(server, "zplanner_client", lambda: client):
            http = TestClient(server.app)
            http.cookies.set(cfg.SESSION_COOKIE_NAME, token)
            response = http.get("/api/v1/worklogs/issue-search", params={"q": "PEI-133"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.queries, ["PEI-133"])

    def test_路由排在單日端點之前(self):
        """寫在 `/worklogs/{work_date}` 後面的話，`issue-search` 會被當成日期。"""
        paths = [getattr(r, "path", "") for r in server.app.routes]
        self.assertLess(
            paths.index("/api/v1/worklogs/issue-search"),
            paths.index("/api/v1/worklogs/{work_date}"),
        )


# --------------------------------------------------------------------------
# 2. 記憶的寫入與讀取
# --------------------------------------------------------------------------


class TestMemoryWrite(IssueChoiceCase):
    def test_選定歸屬就記到那一段的來源上(self):
        self.stream(day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse")))
        target = self.entry_by_label("chatpulse")
        self.put([{"id": target["id"], **pick(20, "OPS-7", 77, "OPS")}])
        mem = self.memory("repo", "/repos/chatpulse")
        self.assertIsNotNone(mem, "repo 段的記憶要記在 repo 的絕對路徑上")
        self.assertEqual((mem["issue_id"], mem["issue_key"], mem["project_id"]), (20, "OPS-7", 77))

        # 再選一次是覆寫，不是並存
        self.put([{"id": target["id"], **pick(10, "PEI-140")}])
        self.assertEqual(self.memory("repo", "/repos/chatpulse")["issue_key"], "PEI-140")

    def test_Space_段記在_space_id_上(self):
        self.stream(day_evidence(chat("m1", "今天幫客戶查帳")))
        target = self.entry_by_label("客服專案群")
        self.put([{"id": target["id"], **pick(9, "PEI-133")}])
        self.assertEqual(self.memory("space", "spaces/AAA")["issue_key"], "PEI-133")

    def test_清除歸屬不會蓋掉上一次的記憶(self):
        self.stream(day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse")))
        target = self.entry_by_label("chatpulse")
        self.put([{"id": target["id"], **pick(20, "OPS-7", 77, "OPS")}])
        self.put([{"id": target["id"], "issue_id": None, "issue_key": None, "project_id": None, "project_key": None}])
        self.assertEqual(self.memory("repo", "/repos/chatpulse")["issue_key"], "OPS-7")

    def test_只改時數不寫記憶(self):
        self.stream(day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse")))
        target = self.entry_by_label("chatpulse")
        self.put([{"id": target["id"], "hours": "2.50"}])
        self.assertIsNone(self.memory("repo", "/repos/chatpulse"))

    def test_跨兩個_repo_的段沒有單一來源所以不記(self):
        """硬挑一個 repo 記的話，記憶會出現在 Viewer 沒意識到的那個 repo 上。"""
        self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 後端", repo_name="cs-backend"),
                commit("c" * 40, "fix: PEI-133 前端", repo_name="chatpulse"),
            )
        )
        target = self.entry_by_label("PEI-133")
        self.put([{"id": target["id"], **pick(9, "PEI-133")}])
        self.assertIsNone(self.memory("repo", "/repos/cs-backend"))
        self.assertIsNone(self.memory("repo", "/repos/chatpulse"))
        # 正對照：歸屬本身確實存進去了，沒記憶不是因為 PUT 沒成功
        self.assertEqual(self.stored()["entries"][0]["issue_key"], "PEI-133")

    def test_被拒絕的_PUT_不留下記憶(self):
        """整批被擋下（一筆都沒有改），記憶也不准動——否則它指向一個沒存成功的選擇。"""
        self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出", repo_name="cs-backend"),
                commit("b" * 40, "整理設定", repo_name="chatpulse"),
            )
        )
        good = self.entry_by_label("chatpulse")
        sent = self.entry_by_label("PEI-133")
        repo.update_worklog_entry_state(
            self.vid, sent["draft_id"], sent["id"], state="submitted", zplanner_worklog_id=9931
        )
        with self.assertRaises(WorklogAlreadySubmitted):
            self.put([{"id": good["id"], **pick(20, "OPS-7", 77, "OPS")}, {"id": sent["id"], "note": "x"}])
        self.assertIsNone(self.memory("repo", "/repos/chatpulse"))

    def test_repository_拒絕不認得的來源種類(self):
        with self.assertRaises(ValueError):
            repo.remember_worklog_source_issue(self.vid, "project", "x", issue_id=1, issue_key="A-1")
        with self.assertRaises(ValueError):
            repo.remember_worklog_source_issue(self.vid, "repo", "  ", issue_id=1, issue_key="A-1")

    def test_記憶是每位_Viewer_各自的(self):
        other = int(repo.upsert_viewer("users/000002", "you@corp.com", "你")["id"])
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/AAA", issue_id=9, issue_key="PEI-133")
        self.assertEqual(repo.get_worklog_source_memory(other, [("space", "spaces/AAA")]), {})


# --------------------------------------------------------------------------
# 3. 記憶只是建議，不預選
# --------------------------------------------------------------------------


class TestMemoryIsOnlyASuggestion(IssueChoiceCase):
    def test_有記憶時排在候選第一項但四欄仍是_NULL(self):
        """🔴 正對照是「有記憶」：記憶確實被讀到（出現在候選），卻沒有進歸屬欄。"""
        repo.remember_worklog_source_issue(
            self.vid, "repo", "/repos/chatpulse",
            issue_id=20, issue_key="OPS-7", project_id=77, project_key="OPS",
        )
        events = self.stream(
            # 關鍵字「設定」會搜到 PEI-140 與 OPS-7——後者正是記憶裡那張
            day_evidence(commit("b" * 40, "調整 設定", repo_name="chatpulse")),
            zplanner=FakeZPlanner(issues=ISSUES),
        )
        self.assertEqual(events[-1]["type"], "done")
        first = events[0]["clusters"][0]["candidates"][0]
        self.assertEqual(first["source"], "last_used")
        self.assertEqual(first["issue_key"], "OPS-7")
        # 同一張單也被搜到時不重複列，標題借過來
        keys = [c["issue_key"] for c in events[0]["clusters"][0]["candidates"]]
        self.assertEqual(keys.count("OPS-7"), 1)
        self.assertEqual(first["title"], "設定備份")

        for entry in events[-1]["entries"]:
            self.assertIsNone(entry["issue_id"])
            self.assertIsNone(entry["issue_key"])
        for entry in self.stored()["entries"]:
            for key in ATTRIBUTION:
                self.assertIsNone(entry[key], f"記憶不准預選：{key} 應為 NULL")

    def test_記憶與_commit_直接寫的單號相同時合併_不降級(self):
        """🔴 強訊號（commit 裡直接寫了單號）不准被「上次記到」（中）吞掉。"""
        repo.remember_worklog_source_issue(
            self.vid, "repo", "/repos/chatpulse",
            issue_id=9, issue_key="PEI-133", project_id=42, project_key="PEI",
        )
        events = self.stream(
            day_evidence(commit("b" * 40, "fix: PEI-133 匯出", repo_name="chatpulse")),
            zplanner=FakeZPlanner(issues=ISSUES),
        )
        cluster = events[0]["clusters"][0]
        same = [c for c in cluster["candidates"] if c["issue_id"] == 9]
        self.assertEqual(len(same), 1, "同一張單合併成一筆，不重複列")
        merged = same[0]
        self.assertIs(merged, cluster["candidates"][0], "仍排在最上面")
        self.assertEqual(merged["source"], "last_used")
        self.assertEqual(merged["confidence"], "strong", "保留強訊號的信心度")
        self.assertEqual(merged["matched_source"], "issue_key")
        # 兩個理由都在：記憶那句提到來源名稱、搜到那句提到單號出處（資料，不是措辭）
        self.assertIn("chatpulse", merged["reason"])
        self.assertIn("commit", merged["reason"])
        self.assertEqual(merged["title"], "匯出效能")
        # 正對照：四欄仍是 NULL
        for key in ATTRIBUTION:
            self.assertIsNone(self.stored()["entries"][0][key])

    def test_記憶沒被搜到時_matched_source_是_null(self):
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/AAA", issue_id=9, issue_key="PEI-133")
        events = self.stream(day_evidence(chat("m1", "今天幫客戶查帳")), zplanner=FakeZPlanner(issues=[]))
        first = events[0]["clusters"][0]["candidates"][0]
        self.assertEqual(first["source"], "last_used")
        self.assertIsNone(first["matched_source"])
        self.assertEqual(first["confidence"], "medium")

    def test_ZPlanner_不可用時記憶照樣是候選_仍不預選(self):
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/AAA", issue_id=9, issue_key="PEI-133")
        events = self.stream(day_evidence(chat("m1", "今天幫客戶查帳")))
        self.assertFalse(events[0]["zplanner"]["available"])
        candidates = events[0]["clusters"][0]["candidates"]
        self.assertEqual([c["source"] for c in candidates], ["last_used"])
        self.assertIsNone(candidates[0]["can_worklog"], "查不到權限就是不知道")
        for entry in self.stored()["entries"]:
            for key in ATTRIBUTION:
                self.assertIsNone(entry[key])

    def test_查詢失敗的段也帶得到記憶(self):
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/AAA", issue_id=9, issue_key="PEI-133")
        events = self.stream(
            day_evidence(chat("m1", "今天幫客戶查帳")),
            zplanner=FakeZPlanner(error=ZPlannerUnavailable("逾時")),
        )
        self.assertEqual(len(events[0]["zplanner"]["errors"]), 1)
        self.assertEqual(events[0]["clusters"][0]["candidates"][0]["source"], "last_used")

    def test_別的來源的記憶不會冒出來(self):
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/BBB", issue_id=9, issue_key="PEI-133")
        events = self.stream(day_evidence(chat("m1", "今天幫客戶查帳")))
        self.assertEqual(events[0]["clusters"][0]["candidates"], [])

    def test_meta_的段帶著來源(self):
        events = self.stream(day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse")))
        self.assertEqual(
            events[0]["clusters"][0]["source"],
            {"kind": "repo", "id": "/repos/chatpulse", "label": "chatpulse"},
        )


# --------------------------------------------------------------------------
# 4. 重產時保留同一段的歸屬
# --------------------------------------------------------------------------


class TestRegenerationKeepsAttribution(IssueChoiceCase):
    def test_同一段多了證據_歸屬照樣保留(self):
        """重產多半是因為那天多了 commit：evidence_refs 不同，但還是同一段。"""
        self.stream(day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse")))
        target = self.entry_by_label("chatpulse")
        self.put([{"id": target["id"], **pick(20, "OPS-7", 77, "OPS")}])

        events = self.stream(
            day_evidence(
                commit("b" * 40, "整理設定", repo_name="chatpulse"),
                commit("d" * 40, "補設定說明", repo_name="chatpulse"),
                chat("m1", "今天幫客戶查帳"),
            )
        )
        self.assertEqual(events[-1]["type"], "done", "沒有東西會丟就不必 force")
        kept = self.entry_by_label("chatpulse")
        self.assertEqual({k: kept[k] for k in ATTRIBUTION}, pick(20, "OPS-7", 77, "OPS"))
        self.assertEqual(len(kept["evidence_refs"]), 2)
        fresh = self.entry_by_label("客服專案群")
        for key in ATTRIBUTION:
            self.assertIsNone(fresh[key], "新冒出來的段照樣留空")

    def test_同名的段但證據完全換了_歸屬不准貼過去(self):
        """🔴 repo 段的 key 是 `repo:<名稱>`、與內容無關（審查發現的錯配）。

        舊的 cs-backend 段含 commit A、B，Viewer 選了 OPS-7；重產時 A、B 改歸到
        issue 段，新的 commit C 落進 cs-backend 段——只看 key 的話 OPS-7 會被原樣
        貼到 C 上，而 C 跟 OPS-7 一點關係都沒有。
        """
        self.stream(
            day_evidence(
                commit("a" * 40, "整理設定", repo_name="cs-backend"),
                commit("b" * 40, "補設定說明", repo_name="cs-backend"),
            )
        )
        self.put([{"id": self.entry_by_label("cs-backend")["id"], **pick(20, "OPS-7", 77, "OPS")}])

        events = self.stream(
            day_evidence(
                commit("a" * 40, "整理設定 PEI-140", repo_name="cs-backend"),
                commit("b" * 40, "補設定說明 PEI-140", repo_name="cs-backend"),
                commit("c" * 40, "其他調整", repo_name="cs-backend"),
            )
        )
        self.assertEqual(events[-1]["type"], "done")
        moved = self.entry_by_label("cs-backend")
        self.assertEqual(moved["evidence_refs"], ["commit:" + "c" * 40])
        for key in ATTRIBUTION:
            self.assertIsNone(moved[key], f"同名段的證據沒有交集，{key} 不准沿用")
        for key in ATTRIBUTION:
            self.assertIsNone(self.entry_by_label("PEI-140")[key])
        self.assertEqual(events[-1]["attributions_cleared"], {"count": 1, "issue_keys": ["OPS-7"]})

    def test_對不上的照規格留空並事後列出_對得上的照樣保留(self):
        self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出", repo_name="cs-backend"),
                commit("b" * 40, "整理設定", repo_name="chatpulse"),
            )
        )
        self.put([
            {"id": self.entry_by_label("PEI-133")["id"], **pick(9, "PEI-133")},
            {"id": self.entry_by_label("chatpulse")["id"], **pick(20, "OPS-7", 77, "OPS")},
        ])
        only_b = day_evidence(commit("b" * 40, "整理設定", repo_name="chatpulse"))
        events = self.stream(only_b)
        self.assertEqual(events[-1]["type"], "done", "不擋、不要求確認")
        self.assertEqual(events[-1]["attributions_cleared"], {"count": 1, "issue_keys": ["PEI-133"]})
        after = self.stored()["entries"]
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["issue_key"], "OPS-7", "對得到同一段的照樣保留")


# --------------------------------------------------------------------------
# 5. 預設專案只比對這一段所屬的 repo
# --------------------------------------------------------------------------


class TestDefaultProjectIsPerRepo(IssueChoiceCase):
    def _candidate(self, events, label: str, issue_key: str) -> Dict[str, Any]:
        cluster = next(c for c in events[0]["clusters"] if c["label"] == label)
        return next(c for c in cluster["candidates"] if c["issue_key"] == issue_key)

    def test_有設預設專案的_repo_才加權_理由寫出_repo_名(self):
        events = self.stream(
            day_evidence(
                commit("a" * 40, "調整 設定", repo_name="cs-backend"),
                commit("b" * 40, "調整 設定", repo_name="chatpulse"),
            ),
            zplanner=FakeZPlanner(issues=ISSUES),
        )
        own = self._candidate(events, "cs-backend", "PEI-140")
        self.assertEqual(own["source"], "default_project")
        self.assertIn("cs-backend", own["reason"])
        # 🔴 chatpulse 沒設預設專案：同一張 PEI 的單不准因為 cs-backend 的設定被加權
        other = self._candidate(events, "chatpulse", "PEI-140")
        self.assertEqual(other["source"], "search")

    def test_純_Chat_的段沒有預設專案加權(self):
        events = self.stream(
            day_evidence(chat("m1", "設定 怎麼調")),
            zplanner=FakeZPlanner(issues=ISSUES),
        )
        sources = {c["source"] for c in events[0]["clusters"][0]["candidates"]}
        self.assertNotIn("default_project", sources)
        self.assertTrue(sources, "正對照：確實有搜到候選，只是沒被加權")


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------


class TestMemorySchema(IssueChoiceCase):
    INTRODUCED_IN = 9

    def test_新資料庫有這張表_而且版本有遞增(self):
        names = {r["name"] for r in db.query_all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("worklog_source_issue_memory", names)
        self.assertGreaterEqual(db.SCHEMA_VERSION, self.INTRODUCED_IN)

    def test_Viewer_刪掉時記憶跟著走(self):
        repo.remember_worklog_source_issue(self.vid, "space", "spaces/AAA", issue_id=9, issue_key="PEI-133")
        db.execute("DELETE FROM viewers WHERE id = ?", (self.vid,))
        self.assertEqual(db.query_all("SELECT * FROM worklog_source_issue_memory"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
