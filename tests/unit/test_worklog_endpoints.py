"""工時草稿的兩支端點：週檢視與草稿 SSE（ADR-0008）。

守的重點不是「有沒有回東西」，是**降級行為**與**那條硬規則**：

  1. **ZPlanner 不可用時不准把整支端點弄掉。** 沒設 ZPLANNER_BASE_URL／APIKEY
     的人仍然該看得到自己的草稿——他只是暫時挑不了候選 issue。
  2. `done` 的 `entries[]` 裡 **`issue_id`／`issue_key` 一律 null**（ADR-0008
     的唯一硬規則：系統只給候選，選擇權在 Viewer）。
  3. **`total_hours` 省略時不分配**：使用者還沒決定總時數之前，不該看到憑空
     冒出來的數字。
  4. 沒有任何 commit／發言 → `error` 事件 `NO_EVIDENCE`，**不是**一份空草稿。

證據蒐集本身（git／Chat）在 `test_worklog_evidence.py` 驗；這裡把
`collect_day_evidence` 換成假的，驗的是**接線**——同樣的坑見
`test_draft_stream_wiring.py`：core 全綠不代表 server 有把參數接上去。

⚠️ 全部使用暫存檔資料庫，絕不碰 `data/chatpulse.db`；ZPlanner 一律是假的
client，不會發出任何真的請求（更不會碰寫入端點）。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_endpoints*.py'
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
    InvalidParameter,
    WorkRepoNotFound,
    ZPlannerUnavailable,
)
from core.worklog_evidence import DayEvidence, Evidence, extract_terms  # noqa: E402
from dashboard.api import server  # noqa: E402

DATE = "2026-09-13"
TZ = "+08:00"


# --------------------------------------------------------------------------
# 假資料
# --------------------------------------------------------------------------


def commit(sha: str, subject: str, *, repo_name: str = "cs-backend", weight: float = 1.0):
    return Evidence(
        kind="commit",
        ref=f"commit:{sha}",
        occurred_at=f"{DATE}T10:00:00+08:00",
        title=subject,
        source_label=repo_name,
        terms=tuple(extract_terms(subject)),
        weight_input=weight,
        sha=sha,
        short_sha=sha[:7],
        author_email="me@corp.com",
    )


def chat(mid: str, text: str, *, weight: float = 1.0):
    return Evidence(
        kind="chat",
        ref=f"chat:spaces/AAA/messages/{mid}",
        occurred_at=f"{DATE}T11:00:00+08:00",
        title=text[:80],
        source_label="客服專案群",
        terms=tuple(extract_terms(text)),
        weight_input=weight,
        space_id="spaces/AAA",
        message_name=f"spaces/AAA/messages/{mid}",
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
    """唯讀的假 client。**沒有** `list_weekly_worklogs`——與正式 client 一致。"""

    def __init__(
        self,
        *,
        ok: bool = True,
        reason: str = "使用 ZPLANNER_APIKEY",
        issues: Any = None,
        members: Any = None,
        permissions: Any = None,
        error: Exception = None,
    ):
        self._ok = ok
        self._reason = reason
        self._issues = issues if issues is not None else []
        self._members = members if members is not None else []
        self._permissions = permissions if permissions is not None else []
        self._error = error
        self.queries: List[str] = []

    def available(self):
        return self._ok, self._reason

    def list_worklogable_issues(self, q: str):
        if not (q or "").strip():
            raise InvalidParameter("查詢可填工時的 issue 時必須給關鍵字")
        self.queries.append(q)
        if self._error is not None:
            raise self._error
        return [i for i in self._issues if q.lower() in json.dumps(i, ensure_ascii=False).lower()]

    def list_project_members(self, project_id: int):
        return self._members

    def list_project_role_permissions(self, project_id: int):
        return self._permissions


class FakeZPlannerWeekly(FakeZPlanner):
    """補上週工時端點的版本（client 之後長出這支時就是這個形狀）。"""

    def __init__(self, *, weekly: Any = None, weekly_error: Exception = None, **kwargs):
        super().__init__(**kwargs)
        self._weekly = weekly if weekly is not None else []
        self._weekly_error = weekly_error
        self.weekly_calls: List[str] = []

    def list_weekly_worklogs(self, week_start: str):
        self.weekly_calls.append(week_start)
        if self._weekly_error is not None:
            raise self._weekly_error
        return self._weekly


# --------------------------------------------------------------------------
# 共用底座
# --------------------------------------------------------------------------


class EndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="chatpulse-worklog-api-")
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
        self.viewer = {
            "id": self.vid,
            "google_user_id": "users/000001",
            "email": "me@corp.com",
        }
        self.repo_id = repo.create_work_repo(
            self.vid,
            name="cs-backend",
            repo_path="/repos/cs-backend",
            author_emails=["me@corp.com"],
            default_zplanner_project_id=42,
            default_zplanner_project_key="PEI",
        )

    def _restore(self) -> None:
        db.close_connection()
        for p in reversed(self._patches):
            p.stop()
        self._db_log.setLevel(self._db_log_level)
        self._tmp.cleanup()

    def stream(self, evidence: DayEvidence, *, zplanner=None, **body) -> List[Dict[str, Any]]:
        """跑一次草稿 SSE，回傳解析過的事件清單。"""
        request = server.WorklogDraftRequest(tz_offset=TZ, **body)
        client = zplanner if zplanner is not None else FakeZPlanner(ok=False, reason="未設定 ZPLANNER_BASE_URL")
        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", lambda **kw: evidence
        ), mock.patch.object(server, "zplanner_client", lambda: client):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            frames = asyncio.run(_collect(response.body_iterator))
        return _parse(frames)

    def week(self, *, zplanner=None, week_start: str = "2026-09-07") -> Dict[str, Any]:
        client = zplanner if zplanner is not None else FakeZPlanner(ok=False, reason="未設定 ZPLANNER_BASE_URL")
        with mock.patch.object(server, "zplanner_client", lambda: client):
            return server.get_worklog_week(week_start=week_start, viewer=self.viewer)


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


# --------------------------------------------------------------------------
# 週檢視
# --------------------------------------------------------------------------


class TestWeekEndpoint(EndpointCase):
    def test_seven_days_even_when_empty(self):
        """七天一律回滿——「沒產草稿」與「不在回應裡」必須是同一件事。"""
        result = self.week()
        self.assertEqual(result["week_start"], "2026-09-07")
        self.assertEqual(result["week_end"], "2026-09-13")
        self.assertEqual(len(result["days"]), 7)
        self.assertEqual([d["weekday"] for d in result["days"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(all(d["draft"] is None for d in result["days"]))

    def test_local_drafts_survive_zplanner_being_down(self):
        """🔴 驗收條件：ZPlanner 完全不可用時，本地草稿照常回。"""
        draft = repo.upsert_worklog_draft(
            self.vid, "2026-09-11", tz_offset=TZ, total_hours="8.00"
        )
        repo.replace_worklog_entries(
            self.vid,
            draft["id"],
            [{"hours": "3.50", "issue_id": 9}, {"hours": "4.50", "issue_id": None}],
        )
        result = self.week(zplanner=FakeZPlanner(ok=False, reason="未設定 ZPLANNER_APIKEY"))

        self.assertFalse(result["zplanner"]["available"])
        self.assertIn("ZPLANNER_APIKEY", result["zplanner"]["reason"])
        day = next(d for d in result["days"] if d["work_date"] == "2026-09-11")
        self.assertEqual(day["draft"]["hours"], "8.00")
        self.assertEqual(day["draft"]["entry_count"], 2)
        self.assertEqual(day["draft"]["unassigned_count"], 1)
        self.assertEqual(result["totals"]["draft_hours"], "8.00")
        self.assertEqual(result["totals"]["days_with_draft"], 1)

    def test_missing_weekly_method_is_a_reason_not_a_crash(self):
        """正式 client 目前沒有週工時端點。降級要說得出原因，不是靜默空白。"""
        result = self.week(zplanner=FakeZPlanner(ok=True))
        self.assertFalse(result["zplanner"]["available"])
        self.assertIn("週工時端點", result["zplanner"]["reason"])

    def test_merges_zplanner_days(self):
        client = FakeZPlannerWeekly(
            weekly=[
                {"date": "2026-09-08", "hours": "3.5"},
                {"date": "2026-09-08", "hours": "4.5"},
                {"date": "2026-09-09", "hours": 2},
            ]
        )
        result = self.week(zplanner=client)
        self.assertEqual(client.weekly_calls, ["2026-09-07"])
        self.assertTrue(result["zplanner"]["available"])
        self.assertEqual(result["zplanner"]["total_hours"], "10.00")
        self.assertEqual(result["zplanner"]["worklog_count"], 3)
        by_date = {d["work_date"]: d for d in result["days"]}
        self.assertEqual(by_date["2026-09-08"]["zplanner"]["hours"], "8.00")
        self.assertEqual(by_date["2026-09-09"]["zplanner"]["hours"], "2.00")
        self.assertIsNone(by_date["2026-09-10"]["zplanner"])
        self.assertNotIn("days", result["zplanner"], "逐日的帳掛在 days[] 上，不重複一份")

    def test_zplanner_error_degrades_with_reason(self):
        result = self.week(
            zplanner=FakeZPlannerWeekly(weekly_error=ZPlannerUnavailable("ZPlanner 連線逾時"))
        )
        self.assertFalse(result["zplanner"]["available"])
        self.assertEqual(result["zplanner"]["reason"], "ZPlanner 連線逾時")
        self.assertEqual(len(result["days"]), 7, "本地那半邊仍然要完整")

    def test_unrecognised_shape_is_not_silently_empty(self):
        """認不出來要說「看不懂」，不能長得像「那週沒填工時」。"""
        result = self.week(zplanner=FakeZPlannerWeekly(weekly={"總時數": 8}))
        self.assertFalse(result["zplanner"]["available"])
        self.assertIn("形狀不認得", result["zplanner"]["reason"])

    def test_bad_week_start_is_rejected(self):
        for bad in ("", "2026/09/08", "第三十七週", "2026-13-01"):
            with self.assertRaises(InvalidParameter):
                self.week(week_start=bad)

    def test_other_viewers_drafts_are_invisible(self):
        other = int(repo.upsert_viewer("users/000002", "other@corp.com", "別人")["id"])
        repo.upsert_worklog_draft(other, "2026-09-11", tz_offset=TZ, total_hours="8.00")
        result = self.week()
        self.assertTrue(all(d["draft"] is None for d in result["days"]))


# --------------------------------------------------------------------------
# 草稿 SSE
# --------------------------------------------------------------------------


class TestDraftStreamEvents(EndpointCase):
    def test_no_evidence_emits_error_event(self):
        events = self.stream(day_evidence())
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertEqual(events[0]["code"], "NO_EVIDENCE")
        self.assertIsNone(repo.get_worklog_draft(self.vid, DATE), "空的一天不落地")

    def test_event_order_is_meta_chunks_done(self):
        events = self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出逾時"),
                commit("b" * 40, "整理設定檔", repo_name="chatpulse"),
                chat("m1", "跟 PM 對了一下規格"),
            )
        )
        self.assertEqual(events[0]["type"], "meta")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual([e["type"] for e in events[1:-1]], ["chunk", "chunk", "chunk"])
        # 事件型別只有四種，一個都沒多
        self.assertLessEqual({e["type"] for e in events}, {"meta", "chunk", "done", "error"})

    def test_meta_carries_everything_the_evidence_column_needs(self):
        events = self.stream(
            day_evidence(commit("a" * 40, "fix: PEI-133 匯出逾時"), chat("m1", "對規格"))
        )
        meta = events[0]
        for key in (
            "work_date", "tz_offset", "since", "until", "repos", "repos_skipped",
            "spaces", "commits", "chat", "clusters", "zplanner", "provider", "model",
        ):
            self.assertIn(key, meta, f"meta 少了 {key}，前端就永遠顯示不出來")
        self.assertEqual(meta["commits"][0]["sha"], "a" * 40)
        self.assertEqual(meta["chat"][0]["message_name"], "spaces/AAA/messages/m1")
        self.assertEqual(meta["note_source"], "commit_subject", "這一階段不接 AI，要講清楚")
        self.assertIsNone(meta["model"])

    def test_clusters_are_in_meta_with_reason_and_refs(self):
        events = self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出逾時"),
                commit("b" * 40, "整理設定檔", repo_name="chatpulse"),
            )
        )
        clusters = events[0]["clusters"]
        self.assertEqual(len(clusters), 2)
        self.assertEqual(clusters[0]["label"], "PEI-133")
        self.assertIn("PEI-133", clusters[0]["reason"])
        self.assertEqual(clusters[0]["evidence_refs"], ["commit:" + "a" * 40])
        # 每個 ref 都指得回 meta 的陣列——指不到的東西不准進證據欄
        available = {c["ref"] for c in events[0]["commits"]} | {
            c["ref"] for c in events[0]["chat"]
        }
        for cluster in clusters:
            for ref in cluster["evidence_refs"]:
                self.assertIn(ref, available)

    def test_one_chunk_per_cluster_with_note_from_commit_subjects(self):
        events = self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出逾時"),
                commit("b" * 40, "PEI-133 加上重試"),
            )
        )
        chunks = [e for e in events if e["type"] == "chunk"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["cluster_key"], "issue:PEI-133")
        self.assertEqual(chunks[0]["text"], "fix: PEI-133 匯出逾時；PEI-133 加上重試")

    def test_note_is_truncated(self):
        long_subject = "修" * 200
        events = self.stream(day_evidence(commit("a" * 40, long_subject)))
        text = [e for e in events if e["type"] == "chunk"][0]["text"]
        self.assertLessEqual(len(text), server.WORKLOG_NOTE_MAX_CHARS)
        self.assertTrue(text.endswith("…"))


class TestDraftStreamAllocation(EndpointCase):
    def test_total_hours_omitted_leaves_hours_null(self):
        """🔴 驗收條件：沒決定總時數之前，不該看到憑空冒出來的數字。"""
        events = self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出"),
                commit("b" * 40, "整理設定", repo_name="chatpulse"),
            )
        )
        done = events[-1]
        self.assertIsNone(done["allocation"])
        self.assertTrue(all(e["hours"] is None for e in done["entries"]))
        self.assertTrue(all(e["allocated_hours"] is None for e in done["entries"]))
        # 落地的那一份也要是 null，不是 0
        stored = repo.get_worklog_draft(self.vid, DATE)
        self.assertIsNone(stored["total_hours"])
        self.assertTrue(all(e["hours"] is None for e in stored["entries"]))

    def test_total_hours_allocates_and_sums_back(self):
        events = self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133 匯出", weight=3.0),
                commit("b" * 40, "整理設定", repo_name="chatpulse", weight=1.0),
            ),
            total_hours=8.0,
        )
        done = events[-1]
        self.assertEqual(done["allocation"]["total_hours"], "8.00")
        hours = [float(e["hours"]) for e in done["entries"]]
        self.assertAlmostEqual(sum(hours), 8.0)
        self.assertTrue(all(h % 0.5 == 0 for h in hours), "每筆都要是 0.5 的倍數")
        self.assertEqual(repo.get_worklog_draft(self.vid, DATE)["total_hours"], "8.00")

    def test_rounding_flag_survives_to_the_entry(self):
        """餘數落在誰身上必須看得見（UI 的「≈ 湊整 +0.5」）。"""
        events = self.stream(
            day_evidence(
                commit("a" * 40, "PEI-133 一", weight=1.0),
                commit("b" * 40, "PEI-077 二", weight=1.0),
                commit("c" * 40, "PEI-901 三", weight=1.0),
            ),
            total_hours=7.0,
        )
        done = events[-1]
        self.assertEqual(len(done["entries"]), 3)
        self.assertTrue(any(e["rounded_up"] for e in done["entries"]))


class TestAdr0008HardRule(EndpointCase):
    """系統只給候選，選擇權在 Viewer。"""

    def test_done_entries_never_carry_an_issue(self):
        client = FakeZPlanner(
            issues=[{"id": 9, "key": "PEI-133", "title": "匯出效能", "project_id": 42}],
            members=[{"user_email": "me@corp.com", "role": "developer"}],
            permissions=[{"role": "developer", "can_worklog": True}],
        )
        events = self.stream(
            day_evidence(commit("a" * 40, "fix: PEI-133 匯出")),
            zplanner=client,
            total_hours=8.0,
        )
        done = events[-1]
        for entry in done["entries"]:
            self.assertIsNone(entry["issue_id"], "ADR-0008：產生時不得預選 issue")
            self.assertIsNone(entry["issue_key"])
        # 連落地的那一份都要是空的——下一次讀回來也不能突然有歸屬
        for entry in repo.get_worklog_draft(self.vid, DATE)["entries"]:
            self.assertIsNone(entry["issue_id"])
            self.assertIsNone(entry["issue_key"])
            self.assertIsNone(entry["project_id"])
        # 但候選要在，而且看得到是哪一張
        self.assertEqual(done["entries"][0]["candidates"][0]["issue_key"], "PEI-133")

    def test_candidate_confidence_comes_with_a_readable_reason(self):
        client = FakeZPlanner(
            issues=[
                {"id": 9, "key": "PEI-133", "title": "匯出效能", "project_id": 42},
                {"id": 10, "key": "PEI-901", "title": "PEI-133 的後續", "project_id": 42},
            ],
            members=[{"user_email": "me@corp.com", "role": "developer"}],
            permissions=[{"role": "developer", "can_worklog": True}],
        )
        events = self.stream(day_evidence(commit("a" * 40, "fix: PEI-133 匯出")), zplanner=client)
        candidates = events[0]["clusters"][0]["candidates"]
        exact = next(c for c in candidates if c["issue_key"] == "PEI-133")
        self.assertEqual(exact["confidence"], "strong")
        self.assertEqual(exact["source"], "issue_key")
        self.assertIn("PEI-133", exact["reason"])
        self.assertTrue(exact["can_worklog"])
        self.assertTrue(exact["permission_checked"])
        self.assertEqual(client.queries, ["PEI-133"], "q 必填，而且用最強的訊號去查")

    def test_permission_unknown_is_not_permission_granted(self):
        """🔴 查不到權限時標 false，**不要假裝有權限**。"""
        client = FakeZPlanner(
            issues=[{"id": 9, "key": "PEI-133", "project_id": 42}],
            members=[{"user_email": "someone-else@corp.com", "role": "developer"}],
            permissions=[{"role": "developer", "can_worklog": True}],
        )
        events = self.stream(day_evidence(commit("a" * 40, "fix: PEI-133")), zplanner=client)
        candidate = events[0]["clusters"][0]["candidates"][0]
        self.assertIsNone(candidate["can_worklog"], "不知道就是不知道，不是 True 也不是 False")
        self.assertFalse(candidate["permission_checked"])
        self.assertIn("me@corp.com", candidate["permission_reason"])

    def test_meta_permission_checked_is_false_without_an_authoritative_email(self):
        client = FakeZPlanner(
            issues=[{"id": 9, "key": "PEI-133", "project_id": 42}],
            members=[{"user_email": "me@corp.com", "role": "developer"}],
            permissions=[{"role": "developer", "can_worklog": True}],
        )
        meta = self.stream(day_evidence(commit("a" * 40, "fix: PEI-133")), zplanner=client)[0]
        self.assertFalse(meta["zplanner"]["permission_checked"])
        self.assertEqual(meta["zplanner"]["permission_email"], "me@corp.com")
        self.assertIn("zplanner_email", meta["zplanner"]["permission_note"])
        self.assertEqual(meta["zplanner"]["queried_terms"], ["PEI-133"])
        self.assertEqual(meta["zplanner"]["worklogable_count"], 1)


class TestDraftStreamDegradation(EndpointCase):
    def test_zplanner_down_still_produces_a_draft(self):
        events = self.stream(
            day_evidence(commit("a" * 40, "fix: PEI-133 匯出")),
            zplanner=FakeZPlanner(ok=False, reason="未設定 ZPLANNER_BASE_URL"),
        )
        self.assertEqual(events[-1]["type"], "done")
        self.assertFalse(events[0]["zplanner"]["available"])
        self.assertIn("ZPLANNER_BASE_URL", events[0]["zplanner"]["reason"])
        self.assertEqual(events[0]["clusters"][0]["candidates"], [])
        self.assertIsNotNone(repo.get_worklog_draft(self.vid, DATE))

    def test_candidate_query_failure_is_recorded_not_fatal(self):
        client = FakeZPlanner(error=ZPlannerUnavailable("ZPlanner 連線逾時"))
        events = self.stream(day_evidence(commit("a" * 40, "fix: PEI-133")), zplanner=client)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[0]["zplanner"]["errors"][0]["reason"], "ZPlanner 連線逾時")

    def test_unexpected_failure_becomes_an_error_event(self):
        request = server.WorklogDraftRequest(tz_offset=TZ)
        def boom(**kwargs):
            raise RuntimeError("git 爆了")
        with mock.patch.object(server.worklog_evidence, "collect_day_evidence", boom), \
             mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)), \
             mock.patch.object(server.log, "exception", lambda *a, **k: None):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            events = _parse(asyncio.run(_collect(response.body_iterator)))
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["code"], "INTERNAL_ERROR")


class TestDraftStreamGuards(EndpointCase):
    """全部擋在 SSE 開始之前——串流一旦開始就是 HTTP 200。"""

    def test_bad_date(self):
        with self.assertRaises(InvalidParameter):
            self.stream_raw("2026/09/13")

    def test_bad_tz_offset(self):
        with self.assertRaises(InvalidParameter):
            self.stream_raw(DATE, tz_offset="Asia/Taipei")

    def test_bad_space_id(self):
        with self.assertRaises(InvalidParameter):
            self.stream_raw(DATE, space_ids=["AAA"])

    def test_unknown_repo_id(self):
        with self.assertRaises(WorkRepoNotFound):
            self.stream_raw(DATE, repo_ids=[self.repo_id, 9999])

    def test_refuses_to_regenerate_over_submitted_entries(self):
        """重產會整組取代 entries，連帶弄丟 `zplanner_worklog_id`。"""
        draft = repo.upsert_worklog_draft(self.vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(
            self.vid,
            draft["id"],
            [{"hours": "3.50", "state": "submitted", "zplanner_worklog_id": 9931}],
        )
        with self.assertRaises(InvalidParameter) as ctx:
            self.stream_raw(DATE)
        self.assertIn("correction-request", str(ctx.exception))

    def test_regenerating_an_untouched_draft_replaces_entries(self):
        evidence = day_evidence(commit("a" * 40, "fix: PEI-133"))
        self.stream(evidence)
        first = repo.get_worklog_draft(self.vid, DATE)
        self.stream(
            day_evidence(
                commit("a" * 40, "fix: PEI-133"),
                commit("b" * 40, "整理設定", repo_name="chatpulse"),
            )
        )
        second = repo.get_worklog_draft(self.vid, DATE)
        self.assertEqual(first["id"], second["id"], "一天一份")
        self.assertEqual(len(second["entries"]), 2)
        self.assertEqual([e["seq"] for e in second["entries"]], [1, 2])

    def stream_raw(self, date: str, **body):
        request = server.WorklogDraftRequest(tz_offset=body.pop("tz_offset", TZ), **body)
        with mock.patch.object(
            server.worklog_evidence,
            "collect_day_evidence",
            lambda **kw: day_evidence(commit("a" * 40, "fix: PEI-133")),
        ), mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            return server.worklog_draft_stream(date, request, self.viewer)


class TestEvidenceWiring(EndpointCase):
    """server 有沒有把該傳的東西傳進 `collect_day_evidence`（同 test_draft_stream_wiring）。"""

    def test_passes_repos_tz_and_identity(self):
        captured: Dict[str, Any] = {}

        def spy(**kwargs):
            captured.update(kwargs)
            return day_evidence(commit("a" * 40, "fix: PEI-133"))

        request = server.WorklogDraftRequest(tz_offset="+0800")
        with mock.patch.object(server.worklog_evidence, "collect_day_evidence", spy), \
             mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            asyncio.run(_collect(response.body_iterator))

        self.assertEqual(captured["work_date"], DATE)
        self.assertEqual(captured["tz_offset"], "+08:00", "偏移要先正規化再往下傳")
        self.assertEqual(captured["google_user_id"], "users/000001")
        self.assertEqual([r["id"] for r in captured["work_repos"]], [self.repo_id])
        # 沒有釘選、當天也沒有 Mention → 不掃任何 Space（絕不掃全部 436 個）
        self.assertEqual(list(captured["space_ids"]), [])
        self.assertIsNone(captured["chat_client"])

    def test_disabled_repos_are_not_scanned(self):
        repo.update_work_repo(self.vid, self.repo_id, enabled=False)
        captured: Dict[str, Any] = {}

        def spy(**kwargs):
            captured.update(kwargs)
            return day_evidence()

        request = server.WorklogDraftRequest(tz_offset=TZ)
        with mock.patch.object(server.worklog_evidence, "collect_day_evidence", spy), \
             mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            response = server.worklog_draft_stream(DATE, request, self.viewer)
            asyncio.run(_collect(response.body_iterator))
        self.assertEqual(captured["work_repos"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
