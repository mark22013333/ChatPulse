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
import threading
import time
import unittest
from datetime import date, timedelta
from typing import Any, Dict, List
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import config as cfg  # noqa: E402
from core import db, repository as repo  # noqa: E402
from core.errors import (  # noqa: E402
    ChatPulseError,
    InvalidParameter,
    WorkRepoNotFound,
    WorklogAlreadySubmitted,
    WorklogDraftNotFound,
    WorklogSubmitInProgress,
    ZPlannerForbidden,
    ZPlannerUnavailable,
    ZPlannerWriteIndeterminate,
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


def zplanner_week(monday: str, per_day_hours) -> Dict[str, Any]:
    """照 `list_weekly_worklogs` 的真實回應形狀（2026-09-13 對正式站實測）造一週。

    `monday` 刻意叫這個名字：ZPlanner **一定**把 `week_start` 正規化到那一週
    的週一，所以測非週一起算時，這裡要給的是它會回的那個週一，不是我們請求
    的那一天。
    """
    start = date.fromisoformat(monday)
    days = []
    for offset, hours in enumerate(per_day_hours):
        days.append(
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "weekday": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[offset],
                "hours": float(hours),
                "entries": [{"id": 900 + offset, "hours": f"{float(hours):.2f}"}]
                if hours
                else [],
            }
        )
    total = float(sum(per_day_hours))
    return {
        "year": start.year,
        "week": start.isocalendar().week,
        "week_start": monday,
        "week_end": (start + timedelta(days=6)).isoformat(),
        "total_hours": total,
        "target_hours": 40.0,
        "completion_pct": round(total / 40.0 * 100, 1),
        "days": days,
    }


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

    def test_非週一起算時不拿_zplanner_那一週的總計來用(self):
        """🔴 ZPlanner 回的那一週與我們請求的七天錯開時，總計一律不抄。

        錯開有兩個獨立的成因，這條守的是兩者共用的下游處置：

        1. **ZPlanner 的 `/api/worklogs/weekly/` 只回本週**，`week_start` 送了
           沒有作用（2026-09-14 實測五種輸入皆回同一週）。所以只要使用者把週
           檢視切到本週以外，必然錯開。
        2. 我們的端點明說 `week_start` 不強制是星期一，所以即使在本週，週日
           起算的七天也會與 ZPlanner 的週一起算錯開——本測試模擬的就是這種。

        錯開之後最危險的不是少一格，是總計：實測請求週日起的 `2026-09-06`，
        七個格子加起來 40.0 小時，表頭卻顯示 44.00 小時、完成率 110%——因為
        那是 ZPlanner 那一週的和，跟我們回的七格不是同一個區間。

        ⚠️ 這份 docstring 原本寫成因是「ZPlanner 會把日期正規化到那一週的週一」。
        那是假陽性（見 `core/zplanner_client.list_weekly_worklogs` 的說明），
        但本測試守的不變式與成因無關，所以斷言本身一條都沒有改。
        """
        client = FakeZPlannerWeekly(weekly=zplanner_week("2026-09-07", [8, 8, 8, 8, 8, 0, 4]))
        result = self.week(zplanner=client, week_start="2026-09-06")
        zp = result["zplanner"]

        self.assertTrue(zp["available"], "逐日資料仍然可用，不是整段報廢")
        # 這條才是真正的不變式：表頭 ＝ 下面七個格子加起來
        grid = sum(
            float(d["zplanner"]["hours"]) for d in result["days"] if d["zplanner"]
        )
        self.assertEqual(zp["total_hours"], f"{grid:.2f}")
        self.assertEqual(zp["total_hours"], "40.00", "不再是修復前的 44.00")
        # ZPlanner 的目標與完成率算的是它那一週，硬套過來就是一個算錯卻
        # 看起來很精確的數字
        self.assertNotIn("target_hours", zp)
        self.assertNotIn("completion_pct", zp, "不再是修復前的 110%")
        self.assertEqual(zp["week_mismatch"]["zplanner_week_start"], "2026-09-07")
        self.assertEqual(zp["week_mismatch"]["requested_week_start"], "2026-09-06")
        # reason 要講得出「哪一段對不上哪一段」，人才知道發生什麼事。
        # **不斷言特定措辭**——文案會隨著我們對成因的理解修正而改寫，把 prose
        # 釘進測試只會讓「更正一個錯誤的說明」看起來像弄壞了功能。
        reason = zp["week_mismatch"]["reason"]
        self.assertIn("2026-09-07", reason, "要講出 ZPlanner 給的是哪一段")
        self.assertIn("2026-09-06", reason, "也要講出使用者請求的是哪一段")
        # 窗口外的 09-13 不准出現在任何地方
        self.assertNotIn("2026-09-13", [d["work_date"] for d in result["days"]])

    def test_週對得起來時照抄目標與完成率(self):
        """正對照：對得起來就要照常帶目標與完成率，不是一律不給。"""
        client = FakeZPlannerWeekly(weekly=zplanner_week("2026-09-07", [8, 8, 8, 8, 8, 0, 4]))
        result = self.week(zplanner=client, week_start="2026-09-07")
        zp = result["zplanner"]
        grid = sum(
            float(d["zplanner"]["hours"]) for d in result["days"] if d["zplanner"]
        )
        self.assertEqual(zp["total_hours"], f"{grid:.2f}")
        self.assertEqual(zp["total_hours"], "44.00")
        self.assertEqual(zp["target_hours"], "40.00")
        self.assertEqual(zp["completion_pct"], 110.0)
        self.assertNotIn("week_mismatch", zp)

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
        with self.assertRaises(WorklogAlreadySubmitted) as ctx:
            self.stream_raw(DATE)
        self.assertIn("correction-request", str(ctx.exception))
        self.assertEqual(ctx.exception.detail["submitted_count"], 1)
        # worklog id 弄丟就是永遠對不回來，force 不給過
        self.assertIs(ctx.exception.detail["force_allowed"], False)
        with self.assertRaises(WorklogAlreadySubmitted):
            self.stream_raw(DATE, force=True)

    def test_refuses_to_wipe_manually_chosen_issues(self):
        """🔴 選專案是這個功能裡最花心力的一步，不准安靜清掉。

        原本的守門只看 `zplanner_worklog_id`，於是「還沒送出、但人已經一筆一筆
        挑好 issue」的草稿重產時會被整組 DELETE，事前沒有任何警告。
        """
        draft = repo.upsert_worklog_draft(self.vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(
            self.vid,
            draft["id"],
            [
                {"hours": "3.50", "issue_id": 501, "issue_key": "PEI-133"},
                {"hours": "1.00", "issue_id": 502, "issue_key": "PEI-140"},
                {"hours": "1.00"},  # 這筆還沒選
            ],
        )
        with self.assertRaises(InvalidParameter) as ctx:
            self.stream_raw(DATE)
        exc = ctx.exception
        # 前端要說得出「你有 N 筆已經選好的專案會被清掉」——數字不能只寫在
        # 中文句子裡，不然前端只能用正則去挖
        self.assertEqual(exc.detail["assigned_count"], 2)
        self.assertEqual(exc.detail["assigned_issue_keys"], ["PEI-133", "PEI-140"])
        self.assertIs(exc.detail["force_allowed"], True)
        self.assertIn("2 筆", exc.message)
        # 擋下來＝真的一筆都沒動
        after = repo.get_worklog_draft(self.vid, DATE)
        self.assertEqual(
            [e["issue_key"] for e in after["entries"]], ["PEI-133", "PEI-140", None]
        )

    def test_detail_reaches_the_http_body(self):
        """`error.detail` 要真的出現在回應主體裡，不是只進 log。"""
        exc = InvalidParameter("有 2 筆已經選好專案了", detail={"assigned_count": 2})
        response = asyncio.run(server.chatpulse_error_handler(mock.Mock(), exc))
        body = json.loads(response.body)
        self.assertEqual(body["error"]["code"], "INVALID_PARAMETER")
        self.assertEqual(body["error"]["detail"], {"assigned_count": 2})
        # 字串 detail 是給 log 看的內部訊息，不該外洩
        plain = asyncio.run(
            server.chatpulse_error_handler(mock.Mock(), InvalidParameter("壞掉了", detail="內部堆疊"))
        )
        self.assertNotIn("detail", json.loads(plain.body)["error"])

    def test_force_true_accepts_wiping_chosen_issues(self):
        """明示 force=true 就放行——擋的是「沒人講一聲」，不是「不准重來」。"""
        draft = repo.upsert_worklog_draft(self.vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(
            self.vid, draft["id"], [{"hours": "3.50", "issue_id": 501, "issue_key": "PEI-133"}]
        )
        events = self.stream(day_evidence(commit("a" * 40, "fix: PEI-133")), force=True)
        self.assertEqual(events[-1]["type"], "done")
        after = repo.get_worklog_draft(self.vid, DATE)
        # ADR-0008：重產出來的一律沒有歸屬，人要重選一次
        self.assertEqual([e["issue_key"] for e in after["entries"]], [None])

    def test_submission_between_the_gate_and_the_write_is_still_refused(self):
        """🔴 TOCTOU：守門通過 → 掃 git／打 ZPlanner（可達數十秒）→ 才寫入。

        那段時間內另一條路徑把工時送出去，整組取代就會把剛拿到的
        `zplanner_worklog_id` **靜默抹掉**——正好是守門要防的事。所以真正的
        判定必須跟 DELETE 在同一個交易裡（`refuse_submitted=True`）。
        """
        draft = repo.upsert_worklog_draft(self.vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(
            self.vid, draft["id"], [{"hours": "3.50", "note": "既有"}]
        )  # ← 守門那一刻沒有任何一筆送出，所以它會放行

        def collect_then_someone_else_submits(**kwargs):
            # 這裡就是那個窗口：正式流程在這裡掃 git／打 ZPlanner
            current = repo.get_worklog_draft(self.vid, DATE)
            repo.replace_worklog_entries(
                self.vid,
                draft["id"],
                [
                    {**e, "state": "submitted", "zplanner_worklog_id": 9931}
                    for e in current["entries"]
                ],
            )
            return day_evidence(commit("a" * 40, "fix: PEI-133"))

        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", collect_then_someone_else_submits
        ), mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            response = server.worklog_draft_stream(
                DATE, server.WorklogDraftRequest(tz_offset=TZ), self.viewer
            )
            events = _parse(asyncio.run(_collect(response.body_iterator)))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "WORKLOG_ALREADY_SUBMITTED")

    def test_被拒絕的重產不會留下痕跡(self):
        """守門擋下來時，`generation_meta` 與 `generated_at` 都不該被動到。

        `upsert_worklog_draft` 排在 `replace_worklog_entries` **前面**，所以
        少了「寫之前再查一次」那一道，一次被拒絕的重產仍然會把證據欄換成
        新證據、entries 卻還是舊的——畫面上兩邊對不起來，而使用者剛剛才
        收到一句「已經送出了，不能重產」。

        被拒絕的操作不該留下任何痕跡。
        """
        draft = repo.upsert_worklog_draft(
            self.vid,
            DATE,
            tz_offset=TZ,
            generation_meta={"type": "meta", "marker": "原本的證據"},
            generated_at="2026-09-01T00:00:00+00:00",
        )
        repo.replace_worklog_entries(
            self.vid, draft["id"], [{"hours": "3.50", "note": "既有"}]
        )
        before = repo.get_worklog_draft(self.vid, DATE)

        def collect_then_someone_else_submits(**kwargs):
            current = repo.get_worklog_draft(self.vid, DATE)
            repo.replace_worklog_entries(
                self.vid,
                draft["id"],
                [
                    {**e, "state": "submitted", "zplanner_worklog_id": 9931}
                    for e in current["entries"]
                ],
            )
            return day_evidence(commit("b" * 40, "fix: PEI-140"))

        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", collect_then_someone_else_submits
        ), mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            response = server.worklog_draft_stream(
                DATE, server.WorklogDraftRequest(tz_offset=TZ), self.viewer
            )
            events = _parse(asyncio.run(_collect(response.body_iterator)))

        self.assertEqual(events[-1]["code"], "WORKLOG_ALREADY_SUBMITTED")

        after = repo.get_worklog_draft(self.vid, DATE)
        # 證據欄的唯一來源沒有被換掉
        self.assertEqual(
            before["generation_meta"], after["generation_meta"], "generation_meta 被動到了"
        )
        self.assertEqual(before["generated_at"], after["generated_at"])
        # entries 也還是原本那筆（連同別人剛送出的 worklog id）
        self.assertEqual(["9931"], [str(e["zplanner_worklog_id"]) for e in after["entries"]])
        after = repo.get_worklog_draft(self.vid, DATE)
        self.assertEqual(
            [e["zplanner_worklog_id"] for e in after["entries"]],
            [9931],
            "遠端 worklog id 是修改／刪除工時的唯一入口，一筆都不准被抹掉",
        )
        self.assertEqual([e["note"] for e in after["entries"]], ["既有"])

    def test_force_does_not_override_a_late_submission(self):
        """`force=true` 能蓋過「已選專案」，但蓋不過「已送出」。"""
        draft = repo.upsert_worklog_draft(self.vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(self.vid, draft["id"], [{"hours": "3.50"}])

        def collect_then_someone_else_submits(**kwargs):
            current = repo.get_worklog_draft(self.vid, DATE)
            repo.replace_worklog_entries(
                self.vid,
                draft["id"],
                [{**e, "zplanner_worklog_id": 9932} for e in current["entries"]],
            )
            return day_evidence(commit("a" * 40, "fix: PEI-133"))

        with mock.patch.object(
            server.worklog_evidence, "collect_day_evidence", collect_then_someone_else_submits
        ), mock.patch.object(server, "zplanner_client", lambda: FakeZPlanner(ok=False)):
            response = server.worklog_draft_stream(
                DATE, server.WorklogDraftRequest(tz_offset=TZ, force=True), self.viewer
            )
            events = _parse(asyncio.run(_collect(response.body_iterator)))
        self.assertEqual(events[-1]["code"], "WORKLOG_ALREADY_SUBMITTED")
        after = repo.get_worklog_draft(self.vid, DATE)
        self.assertEqual([e["zplanner_worklog_id"] for e in after["entries"]], [9932])

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


class TestZplannerWeekParsing(unittest.TestCase):
    """ZPlanner 週回應的解析。

    形狀照 2026-09-13 對正式站的實測——這些欄位的真實樣子與 Notion 筆記
    記的不完全一樣，所以這裡的 fixture 是「實際看到的」而不是「文件說的」。
    """

    def _weekly(self):
        return {
            "year": 2026,
            "week": 37,
            "week_start": "2026-09-07",
            "week_end": "2026-09-13",
            "total_hours": 15.5,
            "target_hours": 40.0,
            "completion_pct": 38.8,
            "days": [
                {
                    "date": "2026-09-07",
                    "weekday": "Mon",
                    "hours": 2.0,
                    "entries": [{"id": 1, "hours": "1.50"}, {"id": 2, "hours": "0.50"}],
                },
                {"date": "2026-09-08", "weekday": "Tue", "hours": 0.0, "entries": []},
            ],
        }

    def test_筆數數的是_entries_的長度不是天數(self):
        # ZPlanner 的 days[] **沒有** worklog_count 欄位。原本寫成
        # `int(row.get("worklog_count") or 1)`，於是那天填 1 筆還是 5 筆都算成 1
        # ——畫面上的「已填 n 筆」會恰好等於「有填工時的天數」，而那個數字
        # 看起來完全合理，不會有人起疑。
        days, err = server._zplanner_week_rows(self._weekly())
        self.assertIsNone(err)
        self.assertEqual(days["2026-09-07"]["worklog_count"], 2)
        self.assertEqual(days["2026-09-08"]["worklog_count"], 0)

    def test_沒有_entries_時退回舊欄位(self):
        data = {"days": [{"date": "2026-09-07", "hours": 1.0, "worklog_count": 3}]}
        days, err = server._zplanner_week_rows(data)
        self.assertIsNone(err)
        self.assertEqual(days["2026-09-07"]["worklog_count"], 3)

    def test_取出目標時數與完成率(self):
        out = server._zplanner_week_target(self._weekly())
        self.assertEqual(out["target_hours"], "40.00")
        self.assertEqual(out["completion_pct"], 38.8)

    def test_取不到就整個不出現而不是補預設值(self):
        # 編一個 40 小時的目標，會讓「完成 38.8%」變成一個看起來精確的假數字
        self.assertEqual(server._zplanner_week_target({"total_hours": 8.0}), {})
        self.assertEqual(server._zplanner_week_target(None), {})
        self.assertEqual(server._zplanner_week_target({"target_hours": "四十"}), {})

    def test_週對得起來就沒有_mismatch(self):
        dates = [f"2026-09-{d:02d}" for d in range(7, 14)]
        self.assertIsNone(server._zplanner_week_mismatch(self._weekly(), dates))

    def test_週錯開時說得出兩邊各是哪一週(self):
        dates = [f"2026-09-{d:02d}" for d in range(6, 13)]  # 週日起
        out = server._zplanner_week_mismatch(self._weekly(), dates)
        self.assertEqual(out["requested_week_start"], "2026-09-06")
        self.assertEqual(out["requested_week_end"], "2026-09-12")
        self.assertEqual(out["zplanner_week_start"], "2026-09-07")
        self.assertEqual(out["zplanner_week_end"], "2026-09-13")

    def test_沒有週欄位就沒有東西可以比對(self):
        # 舊版回應（或裸清單）沒有 week_start：不能憑空判它錯，
        # 這種情況只靠逐日資料的日期自然過濾
        dates = [f"2026-09-{d:02d}" for d in range(7, 14)]
        self.assertIsNone(server._zplanner_week_mismatch({"days": []}, dates))
        self.assertIsNone(server._zplanner_week_mismatch([], dates))

    def test_bool_不算數字(self):
        # `isinstance(True, int)` 在 Python 是 True——不特別擋的話，
        # target_hours 會變成 "1.00"，而那是一個完全編出來的目標
        self.assertEqual(server._zplanner_week_target({"target_hours": True}), {})
        self.assertEqual(server._zplanner_week_target({"completion_pct": False}), {})


# --------------------------------------------------------------------------
# 送出到 ZPlanner（POST /api/v1/worklogs/{date}/submit）
#
# 🔴 這一區與上面的差別：**這裡的每個錯誤都是不可逆的。** 上面那些端點算錯
# 只是畫面難看，這裡算錯會在 ZPlanner 那邊多出或少掉一筆工時，而工時進了月結
# 就刪不掉，只能走 correction-request（要人審核）。
#
# 所以這裡有兩類斷言，**第二類才是重點**：
#   1. 回應長對不對（好驗，但驗不到真正的風險）
#   2. **到底有沒有打出去、打了幾次、失敗前落了多少地**——只有數假 client 的
#      呼叫次數與回頭查 DB 才驗得到。AC-1、AC-5、AC-8、D-9(a) 全部靠第二類。
# --------------------------------------------------------------------------


DATE2 = "2026-09-12"


def exported(issue_key: str, hours: str, *, work_date: str = DATE, note: str = ""):
    """一列 `export_worklogs` 的回傳。

    🔴 **每個值都是字串**——client 刻意不轉型（見 `export_worklogs` 的
    docstring：轉 float 會讓 `2` 與 `2.0` 在比對時錯開）。fixture 也不准偷偷
    用數字，不然就驗不到真正會發生的那種輸入。
    """
    return {
        "date": work_date,
        "user_name": "我",
        "project_code": "PEI",
        "issue_key": issue_key,
        "issue_title": "匯出效能",
        "hours": hours,
        "note": note,
    }


class FakeZPlannerSubmit(FakeZPlanner):
    """會寫入的假 client（duck-typing，不是 `unittest.mock.Mock`）。

    用真的假物件而不是 Mock 是有理由的：Mock 對**任何**屬性都會回一個新的
    Mock，所以「端點呼叫了一支根本不存在的方法」在 Mock 底下會靜默通過，而
    在正式環境是 AttributeError。這裡少一支方法就會當場炸——那正是我們要的。

    `results` 用 issue_id 當鍵，值是例外（拋出來）或 dict（覆蓋預設回應）。
    """

    def __init__(self, *, exported_rows=None, export_error=None, results=None, **kwargs):
        kwargs.setdefault("ok", True)
        super().__init__(**kwargs)
        self._exported = [dict(r) for r in (exported_rows or [])]
        self._export_error = export_error
        self._results = dict(results or {})
        self.export_calls: List[Any] = []
        self.create_calls: List[Dict[str, Any]] = []
        self._next_id = 116800

    def export_worklogs(self, date_from: str, date_to: str):
        self.export_calls.append((date_from, date_to))
        if self._export_error is not None:
            raise self._export_error
        return [dict(r) for r in self._exported]

    def create_worklog(self, issue_id, date, hours, *, note=None, start_time=None):
        self.create_calls.append(
            {"issue_id": issue_id, "date": date, "hours": hours, "note": note}
        )
        outcome = self._results.get(issue_id)
        if isinstance(outcome, Exception):
            raise outcome
        self._next_id += 1
        created = {
            "id": self._next_id,
            "issue_key": f"KEY-{issue_id}",
            "user_name": "我",
            "is_locked": False,
        }
        if isinstance(outcome, dict):
            created.update(outcome)
        return created

    @property
    def created_issue_ids(self) -> List[int]:
        return [c["issue_id"] for c in self.create_calls]


class SubmitCase(EndpointCase):
    """送出端點的共用底座。沿用 `EndpointCase` 的暫存 DB 與 viewer。"""

    def seed(self, *entries: Dict[str, Any], work_date: str = DATE) -> Dict[str, Any]:
        draft = repo.upsert_worklog_draft(
            self.vid, work_date, tz_offset=TZ, total_hours="8.00"
        )
        repo.replace_worklog_entries(self.vid, draft["id"], list(entries))
        return repo.get_worklog_draft(self.vid, work_date)

    def submit(self, *, zplanner=None, work_date: str = DATE, **body) -> Dict[str, Any]:
        client = zplanner if zplanner is not None else FakeZPlannerSubmit()
        self.client = client
        request = server.WorklogSubmitRequest(**body)
        with mock.patch.object(server, "zplanner_client", lambda: client):
            return server.submit_worklog(work_date, request, self.viewer)

    def stored(self, work_date: str = DATE) -> Dict[str, Any]:
        """回頭查 DB。**回應說了什麼不算數，落地了什麼才算。**"""
        return repo.get_worklog_draft(self.vid, work_date)

    def states(self, work_date: str = DATE) -> List[str]:
        return [e["state"] for e in self.stored(work_date)["entries"]]


def entry(**extra) -> Dict[str, Any]:
    """一筆已經選好 issue、還沒送出的草稿 entry。"""
    row = {
        "issue_id": 501,
        "issue_key": "PEI-133",
        "project_id": 42,
        "project_key": "PEI",
        "hours": "2.00",
        "note": "做了匯出效能",
        "state": "pending",
    }
    row.update(extra)
    return row


class TestSubmitUnassignedGate(SubmitCase):
    """AC-1／D-3：有任何一筆沒選 issue 就擋下整批。"""

    def test_未指派時整批擋下且一筆都沒送(self):
        """🔴 「回 400」只是表象，真正要守的是**一筆都沒打出去**。

        部分送出會讓那天的工時停在半送出狀態，而使用者從畫面上看不出哪幾筆
        送了（D-3 的原話）。所以這裡數的是假 client 的呼叫次數——只斷言拋了
        例外的話，「先送出去再拋」一樣會是綠的。
        """
        self.seed(entry(), entry(issue_id=None, issue_key=None, hours="1.50"))
        client = FakeZPlannerSubmit()
        with self.assertRaises(ChatPulseError) as ctx:
            self.submit(zplanner=client)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual([], client.create_calls, "擋下來就是一筆都不准打出去")
        self.assertEqual(["pending", "pending"], self.states(), "DB 也不該被動到")

    def test_全部選好時正常送出(self):
        """正對照：閘不是恆真。少了這條，「永遠擋下」也會讓上一條變綠。"""
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit()
        self.assertEqual(2, result["summary"]["submitted_count"])

    def test_已經是_skipped_的未指派筆不擋(self):
        # 被併成 0 小時的筆本來就不會送出去，把它算進閘裡會讓整天永遠送不出去
        self.seed(entry(), entry(issue_id=None, issue_key=None, hours="0.00", state="skipped"))
        result = self.submit()
        self.assertEqual(1, result["summary"]["submitted_count"])

    def test_帶_entry_ids_時只檢查選中的那幾筆(self):
        """D-8 的連帶條款。

        否則當天別處有一筆沒選專案，`failed` 的那筆就**永遠重打不了**——
        重打是為了救那一筆，結果被另一筆完全無關的筆卡住。
        """
        draft = self.seed(
            entry(state="failed"),
            entry(issue_id=None, issue_key=None, hours="1.50"),
        )
        target = draft["entries"][0]["id"]
        result = self.submit(entry_ids=[target])
        self.assertEqual(1, result["summary"]["submitted_count"])
        self.assertEqual([501], self.client.created_issue_ids)


class TestSubmitSkipsAlreadySent(SubmitCase):
    """AC-2：已經有 `zplanner_worklog_id` 的筆直接跳過。"""

    def test_已送出的筆不重打(self):
        """重打會在 ZPlanner 那邊變成兩筆，而多出來的那筆月結後刪不掉。"""
        self.seed(
            entry(state="submitted", zplanner_worklog_id=99310, zplanner_issue_key="PEI-133"),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        self.submit()
        self.assertEqual([502], self.client.created_issue_ids, "只打沒送過的那筆")

    def test_已送出的筆的_worklog_id_不會被動到(self):
        self.seed(entry(state="submitted", zplanner_worklog_id=99310))
        self.submit()
        self.assertEqual(
            [99310],
            [e["zplanner_worklog_id"] for e in self.stored()["entries"]],
            "遠端 worklog id 是修改／刪除的唯一入口，一筆都不准被蓋掉",
        )


class TestSubmitDeduplication(SubmitCase):
    """AC-3／D-2：送出前查當天，同 issue ＋ 同 hours 已存在就跳過。"""

    def test_遠端已有同_issue_同時數時跳過(self):
        self.seed(entry(hours="2.00"))
        client = FakeZPlannerSubmit(exported_rows=[exported("PEI-133", "2.00")])
        result = self.submit(zplanner=client)
        self.assertEqual([], client.create_calls, "重複的筆一筆都不准打出去")
        self.assertEqual(["skipped"], self.states())
        self.assertEqual(1, result["summary"]["skipped_count"])

    def test_字串刻度不同仍然判得出重複(self):
        """🔴 plan.md 風險段唯一點名的漏判來源。

        export 回的 `hours` 是 CSV **字串**且 client 刻意不轉型，本地存的是
        `format_hours()` 產的兩位小數字串。直接 `==` 比的話，遠端 `"2"`、本地
        `"2.00"` 就漏判 → 那天多出一筆 2 小時的工時 → 月結後刪不掉。
        """
        for remote_hours, local_hours in (
            ("2", "2.00"),
            ("2.0", "2.00"),
            ("2.00", "2"),
            (".5", "0.50"),
            ("0.5", "0.50"),
        ):
            with self.subTest(遠端=remote_hours, 本地=local_hours):
                self.seed(entry(hours=local_hours))
                client = FakeZPlannerSubmit(
                    exported_rows=[exported("PEI-133", remote_hours)]
                )
                self.submit(zplanner=client)
                self.assertEqual([], client.create_calls)
                self.assertEqual(["skipped"], self.states())

    def test_時數不同就照送(self):
        """正對照：去重不是「同一個 issue 就不送」。

        一天在同一個 issue 上填兩筆不同時數是完全正常的事，判成重複會安靜
        吃掉第二筆。少了這條，「一律跳過」也會讓上面那些測試全綠。
        """
        self.seed(entry(hours="1.50"))
        client = FakeZPlannerSubmit(exported_rows=[exported("PEI-133", "2.00")])
        self.submit(zplanner=client)
        self.assertEqual([501], client.created_issue_ids)

    def test_被跳過的筆一定看得見(self):
        """🔴 D-9(b)：AC-3 會誤殺「同一天、同一 issue、同時數」的合法第二筆。

        取捨是寧可漏送也不要重複，**代價是那筆必須列進回報讓人看得見**——
        靜默跳過的話，使用者永遠不知道自己少填了兩小時。
        """
        draft = self.seed(entry(hours="2.00"))
        entry_id = draft["entries"][0]["id"]
        client = FakeZPlannerSubmit(exported_rows=[exported("PEI-133", "2.00")])
        result = self.submit(zplanner=client)

        duplicates = result["summary"]["skipped_duplicates"]
        self.assertEqual(1, len(duplicates))
        # 指得回是本地哪一筆——不然前端畫不出「這張卡被跳過了」
        self.assertEqual(entry_id, duplicates[0]["entry_id"])
        # 也要說得出比對到的是什麼，人才判斷得出這次跳過是對的還是誤殺
        self.assertIn("PEI-133", json.dumps(duplicates[0], ensure_ascii=False))

    def test_跳過的筆不認領遠端的_worklog_id(self):
        """🔴 D-9(b) 的另一半：**不**把比對到的遠端 id 寫進 `zplanner_worklog_id`。

        那筆不是我們建的。認領它會讓 D-2 自承的誤殺變成**永久且看不見**——
        以後重產草稿時守門會說「已經送出了」，而其實我們從來沒送過它。
        """
        self.seed(entry(hours="2.00"))
        client = FakeZPlannerSubmit(exported_rows=[exported("PEI-133", "2.00")])
        self.submit(zplanner=client)
        self.assertEqual(
            [None], [e["zplanner_worklog_id"] for e in self.stored()["entries"]]
        )

    def test_去重只查當天(self):
        # 查整週的話，昨天同一個 issue 同時數的工時會讓今天這筆被誤殺
        self.seed(entry())
        client = FakeZPlannerSubmit()
        self.submit(zplanner=client)
        self.assertEqual([(DATE, DATE)], client.export_calls)

    def test_export_失敗時整批中止(self):
        """🔴 D-9(a)：不得「查不到就當沒重複照送」。

        去重是唯一擋得住重複工時的那道，而重複的代價不可逆（D-2）。查不到
        就照送等於把那道關掉，卻沒有任何人知道它被關掉了。
        """
        self.seed(entry())
        client = FakeZPlannerSubmit(export_error=ZPlannerUnavailable("ZPlanner 連線逾時"))
        with self.assertRaises(ChatPulseError):
            self.submit(zplanner=client)
        self.assertEqual([], client.create_calls, "查不到重複就一筆都不准送")
        self.assertEqual(["pending"], self.states())


class TestSubmitSuccessWriteBack(SubmitCase):
    """AC-4：建立成功後要寫回什麼。"""

    def test_成功的筆寫回_id_與_key_與時間(self):
        self.seed(entry())
        result = self.submit()
        stored = self.stored()["entries"][0]
        self.assertEqual("submitted", stored["state"])
        self.assertEqual(116801, stored["zplanner_worklog_id"])
        self.assertEqual("KEY-501", stored["zplanner_issue_key"])
        self.assertTrue(stored["submitted_at"], "沒有時間就無從對帳")
        self.assertEqual(1, result["summary"]["submitted_count"])

    def test_ZPlanner_說鎖住了就記下來(self):
        """`is_locked` 直接抄 `create_worklog` 回應的欄位。

        全庫至今恆為 0，送出是第一個賦予它語意的地方——鎖住的工時 PATCH 與
        DELETE 都不會生效，唯一途徑是 correction-request。前端要畫得出來。
        """
        self.seed(entry())
        self.submit(zplanner=FakeZPlannerSubmit(results={501: {"is_locked": True}}))
        self.assertIs(True, self.stored()["entries"][0]["is_locked"])

    def test_送出的內容就是本地那筆(self):
        self.seed(entry(hours="3.50", note="改了說明"))
        self.submit()
        call = self.client.create_calls[0]
        self.assertEqual(501, call["issue_id"])
        self.assertEqual(DATE, call["date"])
        self.assertEqual("3.50", call["hours"])
        self.assertEqual("改了說明", call["note"])


class TestSubmitFailureIsolation(SubmitCase):
    """AC-5／AC-6：一筆失敗不准影響其餘各筆。"""

    def test_不確定的筆標_unknown_而且不重試(self):
        """🔴 AC-5。`ZPlannerWriteIndeterminate` ＝ **不知道有沒有寫進去**。

        自動重試可能在 ZPlanner 那邊變成兩筆，而月結後多出來的那筆刪不掉。
        所以這裡數呼叫次數：只斷言 state 的話，「重試三次後標 unknown」也會
        是綠的，而那正好是最貴的那種錯。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        client = FakeZPlannerSubmit(
            results={501: ZPlannerWriteIndeterminate("連線在讀回應時斷掉")}
        )
        result = self.submit(zplanner=client)

        self.assertEqual(1, client.created_issue_ids.count(501), "unknown 一律不重試")
        self.assertEqual(["unknown", "submitted"], self.states(), "其餘各筆照送")
        self.assertEqual(1, result["summary"]["unknown_count"])
        self.assertEqual(1, result["summary"]["submitted_count"])

    def test_信封說失敗的筆標_failed_並留下錯誤(self):
        """AC-6：`code != 200` 是 ZPlanner 應用層親口說「沒做」。"""
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        client = FakeZPlannerSubmit(
            results={501: ZPlannerForbidden("你在該專案沒有填工時的權限")}
        )
        result = self.submit(zplanner=client)

        failed = self.stored()["entries"][0]
        self.assertEqual("failed", failed["state"])
        # 斷言錯誤**碼**而不是訊息措辭——措辭會隨著我們對成因的理解改寫
        self.assertEqual("ZPLANNER_FORBIDDEN", failed["last_error_code"])
        self.assertTrue(failed["last_error_message"], "要留得下訊息，人才知道去找誰")
        self.assertEqual("submitted", self.states()[1], "其餘各筆不受影響")
        self.assertEqual(1, result["summary"]["failed_count"])

    def test_失敗的筆不會留下_worklog_id(self):
        self.seed(entry())
        self.submit(zplanner=FakeZPlannerSubmit(results={501: ZPlannerForbidden("沒權限")}))
        self.assertIsNone(self.stored()["entries"][0]["zplanner_worklog_id"])

    def test_有筆失敗仍然回二百(self):
        """🔴 D-7：只要送出任何一筆就回 HTTP 200。

        4xx 會讓前端以為整批沒送、使用者再按一次 ＝ ZPlanner 重複工時。
        這裡「回 200」在函式層的意思是**不拋例外**——錯誤信封一律只留給整批
        拒絕（AC-1 的 400、AC-7 的 404）。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        result = self.submit(
            zplanner=FakeZPlannerSubmit(results={501: ZPlannerForbidden("沒權限")})
        )
        self.assertEqual(1, result["summary"]["submitted_count"])
        self.assertEqual(1, result["summary"]["failed_count"])

    def test_全部失敗也不拋(self):
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit(
            zplanner=FakeZPlannerSubmit(
                results={
                    501: ZPlannerForbidden("沒權限"),
                    502: ZPlannerForbidden("沒權限"),
                }
            )
        )
        self.assertEqual(2, result["summary"]["failed_count"])
        self.assertEqual(0, result["summary"]["submitted_count"])


class TestSubmitWritesEachEntryImmediately(SubmitCase):
    """🔴 AC-8／D-4：每成功一筆立刻落 DB，不等整批跑完。

    這是整組設計的核心，也是唯一一條「正常路徑上完全看不出來」的規則：整批
    成功時，先寫與後寫的結果一模一樣。差別只在失敗窗口裡——第 N 筆失敗時，
    前面已成功的幾筆若還沒落地就成為**孤兒工時**：ZPlanner 那邊有、本地沒有
    記錄，重送會再建一次，而原本那筆永遠不會被認領。
    """

    def test_跑到一半爆掉時前面成功的筆已經在_DB_裡(self):
        """讓第三筆的**寫入**爆掉，藉此區分「逐筆寫」與「最後一次寫」。

        這裡刻意炸在 repository 而不是 ZPlanner：ZPlanner 的例外會被分類成
        failed／unknown 而不中斷迴圈（AC-5／AC-6 要求的），那樣就測不到
        「整批跑完之前程序就沒了」這種情況。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
            entry(issue_id=503, issue_key="PEI-150", hours="1.00"),
        )
        real = repo.update_worklog_entry_state
        calls = {"n": 0}

        def explode_on_third(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise RuntimeError("寫入時整個程序沒了")
            return real(*args, **kwargs)

        with mock.patch.object(server.repo, "update_worklog_entry_state", explode_on_third):
            try:
                self.submit()
            except Exception:
                pass  # 炸不炸出來不是重點，重點是 DB 裡剩下什麼

        stored = self.stored()["entries"]
        self.assertEqual(
            [116801, 116802],
            [e["zplanner_worklog_id"] for e in stored[:2]],
            "前兩筆在 ZPlanner 已經建好了，本地沒記＝孤兒工時",
        )
        self.assertEqual(["submitted", "submitted"], [e["state"] for e in stored[:2]])

    def test_落地的順序跟得上送出的順序(self):
        """第一筆送出之後、第二筆送出之前，第一筆就要已經在 DB 裡。

        用 client 的 `create_worklog` 當探針去查 DB——這是唯一能在「整批跑完
        之前」觀察到中間狀態的位置。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        seen: List[List[Any]] = []

        class ProbingClient(FakeZPlannerSubmit):
            def create_worklog(inner, issue_id, date, hours, **kwargs):
                seen.append(
                    [e["zplanner_worklog_id"] for e in self.stored()["entries"]]
                )
                return super().create_worklog(issue_id, date, hours, **kwargs)

        self.submit(zplanner=ProbingClient())
        self.assertEqual([None, None], seen[0], "第一筆送出前，兩筆都還沒有 id")
        self.assertEqual(
            116801, seen[1][0], "第二筆送出時，第一筆必須已經落地——否則它是孤兒"
        )


class TestSubmitOrphanReporting(SubmitCase):
    """D-6：repository 回 `False`（影響 0 列）＝ 這筆在送出期間被重產清掉了。"""

    def test_影響零列時不可以當成功(self):
        """🔴 ZPlanner 那邊建好了、本地卻沒有任何一列記得住那個 id。

        當成功回報的話，這筆孤兒工時就再也沒有人會發現——而它每個月都會出現
        在報表上，沒有人知道它從哪來。
        """
        self.seed(entry())
        with mock.patch.object(
            server.repo, "update_worklog_entry_state", lambda *a, **k: False
        ):
            result = self.submit()
        self.assertEqual(
            0, result["summary"]["submitted_count"], "寫不進去就不是送出成功"
        )
        # 🔴 `zplanner_worklog_id` 要原封不動回報出去——那是**唯一**能再找到那筆
        # 工時的線索（本地一列都沒寫進去，下次重送只會再建一筆）
        orphans = result["summary"]["orphans"]
        self.assertEqual(1, len(orphans))
        self.assertEqual(116801, orphans[0]["zplanner_worklog_id"])

    def test_孤兒不算失敗也不算不確定(self):
        """它不是「送出失敗」——ZPlanner 那邊**確實建好了**。

        歸進 failed 會讓人重送而製造重複；歸進 unknown 則是說「不知道有沒有寫
        進去」，但我們知道得很清楚。所以它只能是獨立的一類。
        """
        self.seed(entry())
        with mock.patch.object(
            server.repo, "update_worklog_entry_state", lambda *a, **k: False
        ):
            summary = self.submit()["summary"]
        self.assertEqual(0, summary["failed_count"])
        self.assertEqual(0, summary["unknown_count"])


class TestSubmitRetrySingleEntry(SubmitCase):
    """AC-7／D-8：`entry_ids` 單筆重打，走同一支端點。"""

    def test_只打選中的那一筆(self):
        draft = self.seed(
            entry(state="failed", last_error_code="ZPLANNER_FORBIDDEN"),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50", state="failed"),
        )
        target = draft["entries"][1]["id"]
        self.submit(entry_ids=[target])
        self.assertEqual([502], self.client.created_issue_ids)
        self.assertEqual(["failed", "submitted"], self.states())

    def test_重打成功要清掉上一次的錯誤(self):
        # 留著舊的 last_error_code 會讓畫面上一筆已經成功的工時仍然標紅
        self.seed(entry(state="failed", last_error_code="ZPLANNER_FORBIDDEN",
                        last_error_message="你在該專案沒有這項權限"))
        draft = self.stored()
        self.submit(entry_ids=[draft["entries"][0]["id"]])
        stored = self.stored()["entries"][0]
        self.assertEqual("submitted", stored["state"])
        self.assertIsNone(stored["last_error_code"])

    def test_沒帶_entry_ids_就是整批(self):
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        self.submit()
        self.assertEqual([501, 502], self.client.created_issue_ids)

    def test_那天沒有草稿回四零四(self):
        """AC-7。`WorklogDraftNotFound` 是 404——「這天還沒產草稿」是正常狀態。"""
        client = FakeZPlannerSubmit()
        with self.assertRaises(WorklogDraftNotFound) as ctx:
            self.submit(zplanner=client)
        self.assertEqual(404, ctx.exception.http_status)
        self.assertEqual([], client.create_calls)

    def test_entry_ids_指到別份草稿回四百而不是四零四(self):
        """plan.md 的「未涵蓋」段明訂：這種情況回 400 不是 404。

        404 的意思是「這天沒有草稿」，而這裡草稿明明在——是呼叫端把別天的
        entry id 帶過來了，屬於違反 API 契約，前端該去修的是自己的程式。
        """
        other = self.seed(entry(), work_date=DATE2)
        self.seed(entry(), work_date=DATE)
        client = FakeZPlannerSubmit()
        with self.assertRaises(ChatPulseError) as ctx:
            self.submit(zplanner=client, entry_ids=[other["entries"][0]["id"]])
        self.assertEqual(400, ctx.exception.http_status)
        self.assertNotIsInstance(ctx.exception, WorklogDraftNotFound)
        self.assertEqual([], client.create_calls, "擋下來就是一筆都不准打出去")

    def test_別人的草稿碰不到(self):
        other_vid = int(repo.upsert_viewer("users/000002", "other@corp.com", "別人")["id"])
        other_draft = repo.upsert_worklog_draft(other_vid, DATE, tz_offset=TZ)
        repo.replace_worklog_entries(other_vid, other_draft["id"], [entry()])
        client = FakeZPlannerSubmit()
        with self.assertRaises(ChatPulseError):
            self.submit(zplanner=client)
        self.assertEqual([], client.create_calls)


class TestSubmitDraftStatus(SubmitCase):
    """D-9(c)：`worklog_drafts.status` 的三態。

    ⚠️ **`partial` 在這個 codebase 至今從未被寫入過，這是第一處。**
    """

    def test_全部成功轉_submitted(self):
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit()
        self.assertEqual("submitted", result["status"])
        self.assertEqual("submitted", self.stored()["status"], "回應說了不算，DB 才算")

    def test_成功混跳過也算轉_submitted(self):
        """「每筆都 submitted **或 skipped**」。

        少了 skipped 這一半，只要那天有任何一筆重複，週檢視的
        `totals.days_submitted` 就永遠數不到它。
        """
        self.seed(entry(hours="2.00"), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        client = FakeZPlannerSubmit(exported_rows=[exported("PEI-133", "2")])
        result = self.submit(zplanner=client)
        self.assertEqual(["skipped", "submitted"], self.states())
        self.assertEqual("submitted", result["status"])
        self.assertEqual("submitted", self.stored()["status"])

    def test_有成功有失敗是_partial(self):
        """🔴 partial 必須是獨立狀態（DDL 原話）。

        算成 draft 會讓人以為整天都還沒送，再按一次就把已經成功的那幾筆重複
        填一遍；算成 submitted 則會讓失敗的那幾筆永遠沒人管。
        """
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit(
            zplanner=FakeZPlannerSubmit(results={502: ZPlannerForbidden("沒權限")})
        )
        self.assertEqual("partial", result["status"])
        self.assertEqual("partial", self.stored()["status"])

    def test_不確定的那筆也讓整天停在_partial(self):
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit(
            zplanner=FakeZPlannerSubmit(
                results={502: ZPlannerWriteIndeterminate("連線斷了")}
            )
        )
        self.assertEqual("partial", result["status"])

    def test_全部失敗時狀態不前進(self):
        """標成 partial 會讓畫面顯示「部分已送出」，而 ZPlanner 那邊一筆都沒有
        ——使用者於是不敢重送。"""
        self.seed(entry())
        result = self.submit(
            zplanner=FakeZPlannerSubmit(results={501: ZPlannerForbidden("沒權限")})
        )
        self.assertEqual("draft", result["status"])
        self.assertEqual("draft", self.stored()["status"])


class TestSubmitResponseShape(SubmitCase):
    """回應要帶前端畫得出混合狀態的東西。

    逐筆一交易 ＝ 整批不再有一致快照，跑到一半的草稿是混合狀態（plan.md 的
    風險段）。前端必須畫得出來，所以回應不能只給計數。
    """

    def test_回應帶重讀後的完整_entries(self):
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        result = self.submit(
            zplanner=FakeZPlannerSubmit(results={502: ZPlannerForbidden("沒權限")})
        )
        self.assertEqual(DATE, result["work_date"])
        self.assertEqual(2, len(result["entries"]))
        # 重讀後的 ＝ 與 DB 完全一致，不是送出前那份加工過的
        self.assertEqual(
            [e["state"] for e in self.stored()["entries"]],
            [e["state"] for e in result["entries"]],
        )
        self.assertEqual(116801, result["entries"][0]["zplanner_worklog_id"])

    def test_summary_的四個計數加起來就是全部的筆數(self):
        """不變式：每一筆都恰好落在一個計數裡。

        少了這條，一筆被漏掉（既沒 submitted 也沒 failed）完全看不出來——
        而它在畫面上的樣子是「這張卡什麼都沒發生」。
        """
        self.seed(
            entry(hours="2.00"),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
            entry(issue_id=503, issue_key="PEI-150", hours="1.00"),
            entry(issue_id=504, issue_key="PEI-160", hours="0.50"),
        )
        client = FakeZPlannerSubmit(
            exported_rows=[exported("PEI-133", "2")],
            results={
                503: ZPlannerForbidden("沒權限"),
                504: ZPlannerWriteIndeterminate("連線斷了"),
            },
        )
        summary = self.submit(zplanner=client)["summary"]
        self.assertEqual(1, summary["submitted_count"])
        self.assertEqual(1, summary["skipped_count"])
        self.assertEqual(1, summary["failed_count"])
        self.assertEqual(1, summary["unknown_count"])
        total = sum(
            summary[k]
            for k in ("submitted_count", "skipped_count", "failed_count", "unknown_count")
        )
        self.assertEqual(4, total, "有一筆既沒送出也沒被歸類，它在畫面上會是空白的")

    def test_summary_的鍵名與週檢視一致(self):
        """D-7：鍵名沿用週檢視，兩個畫面才講同一套話。"""
        self.seed(entry())
        summary = self.submit()["summary"]
        for key in (
            "submitted_count",
            "skipped_count",
            "failed_count",
            "unknown_count",
            "skipped_duplicates",
        ):
            self.assertIn(key, summary, f"summary 少了 {key}，前端就畫不出來")


# --------------------------------------------------------------------------
# 並行與時間預算（AC-9／AC-10／AC-11）
#
# 🔴 這三條是 2026-09-14 的 review 才補上的，而前兩條**都被實測重現過**：
# 兩條執行緒相隔 20ms 打同一天的 submit，草稿只有 2 筆，`create_worklog` 卻被
# 呼叫了 4 次；先回的那個請求回報 `submitted=2 failed=0 unknown=0 orphans=0`
# ——ZPlanner 那邊多出來的 2 筆重複工時在它身上**完全隱形**。
#
# 所以這一區的斷言重點一律是「**打出去幾次**」，不是「回了什麼碼」：
# 只驗「第二個請求回 409」的話，一個「先送出去再回 409」的實作照樣是綠的，
# 而那正是要防的那件事。
# --------------------------------------------------------------------------


class FakeZPlannerBlockingSubmit(FakeZPlannerSubmit):
    """第一筆 `create_worklog` 會卡住，直到測試放行。

    用兩個 `threading.Event` 而不是 `time.sleep(固定秒數)`：睡固定秒數的版本
    會在機器忙的時候偶爾先跑完，於是「第二個請求來的時候第一批還在飛」這個
    前提沒成立，測試什麼都沒驗到卻還是綠的——而它驗的正是不可逆的重複工時。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        #: 第一筆送出真的開始了。等到它才代表「這一批確實還在飛」。
        self.first_create_started = threading.Event()
        #: 測試放行，第一筆才會回來。
        self.release = threading.Event()

    def create_worklog(self, issue_id, date, hours, *, note=None, start_time=None):
        blocking = not self.first_create_started.is_set()
        self.first_create_started.set()
        if blocking:
            # timeout 是保險絲：測試若忘了放行，寧可自己失敗也不要掛住整個套件。
            self.release.wait(timeout=10)
        return super().create_worklog(
            issue_id, date, hours, note=note, start_time=start_time
        )


class FakeZPlannerSlowSubmit(FakeZPlannerSubmit):
    """每一筆 `create_worklog` 都慢 `delay` 秒，用來把整批時間預算逼到上限。"""

    def __init__(self, *, delay: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay

    def create_worklog(self, issue_id, date, hours, *, note=None, start_time=None):
        time.sleep(self.delay)
        return super().create_worklog(
            issue_id, date, hours, note=note, start_time=start_time
        )


class TestSubmitConcurrencyGuard(SubmitCase):
    """AC-9／D-10：同一天同時只准有一個送出在跑，第二個立刻回 409。"""

    def submit_unpatched(self, *, work_date: str = DATE, **body) -> Dict[str, Any]:
        """不自己 patch 的送出，給並行測試用。

        ⚠️ `SubmitCase.submit` 內建 `mock.patch.object(server, "zplanner_client", …)`，
        而 `mock.patch` 換掉的是**模組屬性**、不是執行緒區域的東西：兩條執行緒
        各自 start／stop 會互相踩到對方存下來的還原值，留下一個被 patch 過的
        模組給後面的測試。並行測試改成在主執行緒 patch 一次，兩邊都用這支。
        """
        request = server.WorklogSubmitRequest(**body)
        return server.submit_worklog(work_date, request, self.viewer)

    def _run_in_thread(self, outcome: Dict[str, Any], **body):
        """在另一條執行緒跑一次送出，把結果或例外收進 `outcome`。"""

        def runner():
            try:
                outcome["result"] = self.submit_unpatched(**body)
            except BaseException as exc:  # noqa: BLE001 - 要把例外帶回主執行緒斷言
                outcome["error"] = exc

        thread = threading.Thread(target=runner, name="submit-worklog")
        thread.start()
        return thread

    def test_兩條執行緒同時送同一天只會打出一批(self):
        """🔴 **這條是整個修復的核心。**

        斷言的是 `create_worklog` 的**總呼叫次數等於草稿筆數**，不是「第二個
        請求拿到 409」。只驗回應碼的話，一個「先把整批送出去、最後才回 409」
        的實作一樣會是綠的——而 ZPlanner 那邊已經多出一整批重複工時，月結後
        刪不掉（只能走 correction-request）。

        兩個重疊的送出為什麼會重複送：它們**各自**查一次「這天 ZPlanner 已經
        有哪些工時」當去重底稿，而兩份底稿都是在對方寫入之前拍的，於是去重對
        兩邊都說「沒有重複」。
        """
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        client = FakeZPlannerBlockingSubmit()
        outcome: Dict[str, Any] = {}

        with mock.patch.object(server, "zplanner_client", lambda: client):
            first = self._run_in_thread(outcome)
            try:
                self.assertTrue(
                    client.first_create_started.wait(timeout=10),
                    "第一批根本沒開始送，那「第二個請求來的時候還在飛」就沒成立，"
                    "這條測試什麼都沒驗到",
                )
                with self.assertRaises(WorklogSubmitInProgress) as ctx:
                    self.submit_unpatched()
            finally:
                client.release.set()
                first.join(timeout=10)

        self.assertFalse(first.is_alive(), "第一批沒有跑完")
        self.assertIsNone(outcome.get("error"), f"第一批不該失敗：{outcome.get('error')!r}")
        self.assertEqual(409, ctx.exception.http_status)
        self.assertEqual(
            2,
            len(client.create_calls),
            "打出去的次數要等於草稿筆數。變成兩倍就是 ZPlanner 那邊多了一整批"
            "重複工時，而重複工時月結後刪不掉",
        )
        self.assertEqual(["submitted", "submitted"], self.states())

    def test_被擋下的那個請求一筆都沒送(self):
        """409 的語意是「一筆都沒送」，不是「送了一半」。

        與上一條分開寫是因為它們會用不同的方式壞掉：上一條防的是「送兩批」，
        這一條防的是「先送再擋」——那樣第二個請求的使用者會看到一個錯誤，卻
        不知道自己其實已經製造了重複工時。
        """
        self.seed(entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50"))
        client = FakeZPlannerBlockingSubmit()
        outcome: Dict[str, Any] = {}

        with mock.patch.object(server, "zplanner_client", lambda: client):
            first = self._run_in_thread(outcome)
            try:
                self.assertTrue(client.first_create_started.wait(timeout=10))
                # 第二個請求進來的當下，第一批只送出了 0 筆（還卡在第一筆裡）。
                before = len(client.create_calls)
                with self.assertRaises(WorklogSubmitInProgress):
                    self.submit_unpatched()
                self.assertEqual(
                    before, len(client.create_calls), "被擋下的請求不准打出任何一筆"
                )
            finally:
                client.release.set()
                first.join(timeout=10)

    def test_不同的日期互不相干(self):
        """正對照：鎖的粒度是 `(viewer, 日期)`，不是「整個 viewer 一把」。

        少了這條，一個「永遠拒絕併發」的實作也會讓上面兩條變綠——但那會讓
        禮拜一還在送的時候送不了禮拜二，而那兩天在 ZPlanner 那邊根本碰不到
        彼此，沒有任何重複工時的風險。
        """
        self.seed(entry(), work_date=DATE)
        self.seed(
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"), work_date=DATE2
        )
        client = FakeZPlannerBlockingSubmit()
        outcome: Dict[str, Any] = {}

        with mock.patch.object(server, "zplanner_client", lambda: client):
            first = self._run_in_thread(outcome, work_date=DATE)
            try:
                self.assertTrue(client.first_create_started.wait(timeout=10))
                other = self.submit_unpatched(work_date=DATE2)
            finally:
                client.release.set()
                first.join(timeout=10)

        self.assertEqual(1, other["summary"]["submitted_count"])
        self.assertEqual(["submitted"], self.states(DATE2))

    def test_跑完之後鎖要放掉(self):
        """`finally` 漏掉一次，那一天就**永遠**送不出去，只能重啟行程。

        ⚠️ 兩次送出**必須共用同一個假 client**。每個 `FakeZPlannerSubmit` 的
        id 計數器都從 116800 重新起算，所以換一個實例等於讓 ZPlanner 把同一個
        worklog id 發兩次——`idx_worklog_entry_remote`（partial unique index，
        重複送出的最後一道防線）會擋下第二筆，於是它變成孤兒而不是送出成功。
        真的 ZPlanner 不會重發 id，這是 fixture 的限制，不是被測行為。
        """
        draft = self.seed(
            entry(), entry(issue_id=502, issue_key="PEI-140", hours="1.50")
        )
        client = FakeZPlannerSubmit()
        self.submit(zplanner=client, entry_ids=[draft["entries"][0]["id"]])
        result = self.submit(zplanner=client, entry_ids=[draft["entries"][1]["id"]])
        self.assertEqual(2, result["summary"]["submitted_count"])

    def test_整批被擋下之後鎖也要放掉(self):
        """擋下整批是**拋例外**離開的——這條路徑沒還鎖的話，那一天從此送不出去。

        而且症狀會很難查：使用者按了送出、看到「請先選專案」，補好之後再按，
        得到的卻是「正在送出中」，而根本沒有任何東西在送。
        """
        draft = self.seed(entry(), entry(issue_id=None, issue_key=None, hours="1.50"))
        with self.assertRaises(ChatPulseError) as ctx:
            self.submit()
        self.assertEqual(400, ctx.exception.http_status)
        result = self.submit(entry_ids=[draft["entries"][0]["id"]])
        self.assertEqual(1, result["summary"]["submitted_count"])

    def test_正在送出中不是已經送出(self):
        """🔴 兩個都是 409，但使用者的下一步完全不同。

        `WORKLOG_ALREADY_SUBMITTED` ＝ 這份草稿已經送過了（終局狀態，去看結果
        或走 correction-request）；這個 ＝ 正在送、還沒結束（暫時狀態，等一下
        再試）。共用一個碼的話前端只能猜一個，猜錯的那一半會把人導去錯的地方。
        """
        self.seed(entry())
        client = FakeZPlannerBlockingSubmit()
        outcome: Dict[str, Any] = {}

        with mock.patch.object(server, "zplanner_client", lambda: client):
            first = self._run_in_thread(outcome)
            try:
                self.assertTrue(client.first_create_started.wait(timeout=10))
                with self.assertRaises(WorklogSubmitInProgress) as ctx:
                    self.submit_unpatched()
            finally:
                client.release.set()
                first.join(timeout=10)

        self.assertNotIsInstance(ctx.exception, WorklogAlreadySubmitted)
        self.assertEqual("WORKLOG_SUBMIT_IN_PROGRESS", ctx.exception.code)


class TestSubmitTimeBudget(SubmitCase):
    """AC-10／D-11：整批有時間預算，到點停送剩下的、**仍然回 200**。

    為什麼需要預算：client 的 `(5, 20)` 是 connect 與**每次 read** 的上限，
    不是單次請求的總長，更不是整批的總長——對方慢速吐 chunk 時單次請求可以
    無上限地拖，N=10 的一批最壞約 275 秒。
    """

    def test_到點就停送剩下的而且仍然回二百(self):
        """🔴 到點是**不再開始新的一筆**，不是砍掉進行中的那一筆。

        砍掉一個已經發出去的寫入只會得到 `unknown`——「不知道寫進去沒有、不准
        自動重試、要人工去 ZPlanner 對帳」，那比乾脆沒送更糟。所以第一筆（它
        跑完的時間點已經超過預算）必須是 `submitted`，不是 `unknown`。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
            entry(issue_id=503, issue_key="PEI-150", hours="1.00"),
        )
        # 預算 0.4 秒、每筆 0.5 秒：第一筆開始時還在預算內（去重查詢是假的，
        # 只花幾微秒），跑完就超過了，所以第二筆連開始都不會開始。
        client = FakeZPlannerSlowSubmit(delay=0.5)
        with mock.patch.object(server, "WORKLOG_SUBMIT_BUDGET_SECONDS", 0.4):
            result = self.submit(zplanner=client)

        self.assertEqual(
            1, len(client.create_calls), "預算用完之後不准再開始新的一筆"
        )
        self.assertEqual(2, result["summary"]["not_attempted_count"])
        self.assertEqual(
            ["submitted", "pending", "pending"],
            self.states(),
            "進行中的那一筆要讓它跑完（submitted），停送的維持 pending 可重打",
        )
        self.assertEqual("partial", result["status"])

    def test_停送的筆維持_pending_所以重打得回來(self):
        """停送不是失敗，那些筆一筆都沒動過，再送一次就補得回來。"""
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
        )
        client = FakeZPlannerSlowSubmit(delay=0.5)
        with mock.patch.object(server, "WORKLOG_SUBMIT_BUDGET_SECONDS", 0.4):
            self.submit(zplanner=client)
        self.assertEqual(["submitted", "pending"], self.states())

        # 預算恢復正常，第二次把剩下的補完。⚠️ 這裡順便驗到「停送的筆沒有被
        # 寫進 zplanner_worklog_id」——有的話它會被當成送過了而永遠跳過。
        #
        # ⚠️ 共用同一個 client：換一個實例的話 id 計數器會從 116800 重新起算，
        # 等於 ZPlanner 重發同一個 worklog id，被 `idx_worklog_entry_remote`
        # 擋成孤兒（fixture 的限制，不是被測行為）。
        result = self.submit(zplanner=client)
        self.assertEqual(["submitted", "submitted"], self.states())
        self.assertEqual(0, result["summary"]["not_attempted_count"])
        self.assertEqual("submitted", result["status"])

    def test_預算夠的時候全部送完(self):
        """正對照：預算不是恆真的閘。

        少了這條，一個「永遠停送」或「預算算成負的」的實作也會讓上面兩條變綠
        ——而它的症狀是每次送出都只送一筆，得按十次才填得完一天。
        """
        self.seed(
            entry(),
            entry(issue_id=502, issue_key="PEI-140", hours="1.50"),
            entry(issue_id=503, issue_key="PEI-150", hours="1.00"),
        )
        result = self.submit()
        self.assertEqual(3, result["summary"]["submitted_count"])
        self.assertEqual(0, result["summary"]["not_attempted_count"])

    def test_沒有超時的時候計數也要在(self):
        """形狀穩定：`not_attempted_count` 一律都在，即使是 0。

        它是使用者唯一看得出「這批沒跑完」的訊號——停送的筆在 DB 裡是
        `pending`，與「從未送過」長得一模一樣。欄位時有時無的話，前端只能
        用 `undefined` 去猜，而猜錯的方向是「以為全部送完了」。
        """
        self.seed(entry())
        summary = self.submit()["summary"]
        self.assertIn("not_attempted_count", summary)
        self.assertEqual(0, summary["not_attempted_count"])


class TestSubmitOrphanCause(SubmitCase):
    """AC-11：孤兒回報**不預設成因**。"""

    def _orphans(self) -> List[Dict[str, Any]]:
        self.seed(entry())
        with mock.patch.object(
            server.repo, "update_worklog_entry_state", lambda *a, **k: False
        ):
            return self.submit()["summary"]["orphans"]

    def test_列出多個可能成因而不是挑一個當定論(self):
        """🔴 這裡原本寫死成「送出期間草稿被重新產生」。

        而 2026-09-14 實測跑出孤兒的其實是**並行送出**（先跑完的那個把 id 寫
        上去，後到的那個守門條件就不成立了）。寫死一個成因的代價不是話講得不
        夠準，是**人照那句去查重產紀錄，永遠查不到東西**——然後把一筆真實存在
        的遠端工時當成誤報放掉，而它每個月都會出現在報表上。

        這一層分不出是哪一種（UPDATE 只回「影響 0 列」，不回為什麼），所以只能
        全部列出來。斷言的是「有不只一個」而不是措辭——文案會隨著理解修正而
        改寫，把 prose 釘進測試會讓「更正一個錯誤的說明」看起來像弄壞了功能。
        """
        orphans = self._orphans()
        self.assertEqual(1, len(orphans))
        self.assertGreaterEqual(
            len(orphans[0]["possible_causes"]),
            2,
            "只列一個成因等於預設成因——人照著去查另一種成因就永遠查不到",
        )

    def test_孤兒一定帶著遠端的_worklog_id(self):
        """🔴 那是唯一還找得到那筆遠端工時的線索。

        本地一列都沒寫進去，所以除了這個編號之外沒有任何東西指向它；吞掉的話
        只剩人去 ZPlanner 網頁一頁一頁翻。
        """
        orphans = self._orphans()
        self.assertEqual(116801, orphans[0]["zplanner_worklog_id"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
