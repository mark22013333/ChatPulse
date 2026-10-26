import asyncio
from contextlib import ExitStack
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from core import config, db, repository as repo
from core.errors import InvalidParameter, ReplyDispatchUnknown, ReplyReviewRequired
from core.reply_evidence import BlockReason
from core.providers.base import AIProvider
from dashboard.api import reply_pipeline, server
from dashboard.api.reply_pipeline import ReplyPipeline
from dashboard.api.source_broker import Limits, RunControl, RunStopped
from test_project_context import ProjectContextFixture, CHAT
from test_draft_stream_wiring import _drain


class ScriptedProvider(AIProvider):
    model = "fake-model"
    model_id = "fake-model"
    name = "fake"
    def __init__(self):
        self.prompts = []
        self.text = "正式環境已部署版本 7。"
        self.relation, self.scope, self.currency = "supported", "match", "current"
        self.claim_kind = "status"
        self.gap = False
        self.on_assess = None
        self.plan = {"queries": []}
        self._usage_recorder = Mock()

    def available(self):
        return True, "合成供應商"

    def stream_text(self, prompt, **kwargs):
        yield self.generate(prompt, **kwargs)

    def generate(self, prompt, *, operation, **kw):
        payload = json.loads(prompt) if operation != "draft_reply_polish" else {"text": prompt}
        self.prompts.append((operation, payload))
        self._record_usage(operation, 1, 1, 2)
        refs = [e["id"] for e in payload.get("evidence", []) if e["source_id"] == "pm"]
        if operation == "reply_private_analysis":
            return json.dumps(self.plan)
        if operation == "reply_fact_pack":
            facts = [{"id": "f1", "text": self.text, "kind": self.claim_kind,
                      "evidence_ids": refs[:1], "relation": self.relation,
                      "scope": self.scope, "currency": self.currency}] if refs and not self.gap else []
            return json.dumps({"facts": facts, "gaps": ["部署證據不足"] if not facts else []})
        if operation == "reply_draft":
            return json.dumps({"text": self.text})
        if operation == "draft_reply_polish":
            return "<polished>正式環境保證已部署版本 7。</polished>"
        if operation == "reply_assess":
            if self.on_assess:
                self.on_assess(payload)
            text = payload["text"]
            claims = [] if self.gap else [{"id": "c1", "text": text, "kind": self.claim_kind,
                         "relation": "insufficient" if "保證" in text else self.relation,
                         "reference_validity": "valid" if refs else "unresolved", "currency": self.currency,
                         "scope": self.scope, "reason": "合成模型評估", "evidence_ids": refs[:1]}]
            return json.dumps({"claims": claims, "segments": [{"start": 0, "end": len(text),
                               "kind": "gap" if self.gap else "claim", "claim_id": None if self.gap else "c1"}],
                               "gaps": ["部署證據不足"] if self.gap else []})
        raise AssertionError(operation)


class FakeEvidenceClient:
    def __init__(self):
        self.reads = []
        self.messages = {"spaces/target": "請問正式環境部署了嗎？", "spaces/pm": "production 版本 7 部署成功，已確認症狀解除。"}
        self.send_message = Mock(return_value={"name": "spaces/target/messages/sent", "createTime": "now"})
        self.attachments = False
    def read_reply_message(self, name):
        self.reads.append(name)
        return {"name": name, "text": self.messages["spaces/target"], "createTime": "2026-10-01T00:00:00Z",
                "thread": {"name": "spaces/target/threads/q"}}
    def read_reply_recent(self, space_id, limit, *, thread_name=None):
        self.reads.append(space_id)
        return [{"name": space_id + "/messages/evidence", "text": self.messages[space_id],
                 "createTime": "2026-10-01T00:00:00Z", "thread": {"name": thread_name or space_id + "/threads/other"},
                 "attachment": [{"name": "fake"}] if self.attachments else []}]


class PipelineFixture(ProjectContextFixture):
    def setUp(self):
        super().setUp()
        repo.set_project_context_opt_in(self.a, True)
        self.viewer = {"id": self.a, "google_user_id": "users/fake-a", "display_name": "甲"}
        self.client, self.provider = FakeEvidenceClient(), ScriptedProvider()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, name, value in ((config, "PROJECT_CONTEXT_ENABLED", True), (config, "REPLY_EVIDENCE_ENABLED", True),
                                    (server, "get_client", Mock(return_value=self.client)),
                                    (server, "get_provider", Mock(return_value=self.provider)),
                                    (server, "resolve_model", lambda *a, **k: ("fake", None)),
                                    (server.providers, "resolve_name", lambda *a: "fake"),
                                    (server, "name_resolver_for", lambda *a: lambda uid: "假成員"),
                                    (server, "space_display_name", lambda *a: "合成 Space")):
            self.stack.enter_context(patch.object(target, name, value))
    def selection(self, use="reply_context"):
        return {**self.select(use), "environment": "production"}
    def stream(self, selection=None, **kw):
        req = server.DraftRequest(project_selection=server.ProjectSelection(**(selection or self.selection())), **kw)
        return [json.loads(e.split("data: ", 1)[1]) for e in asyncio.run(_drain(server.draft_stream(self.mid, req, self.viewer).body_iterator))]
    def complete(self, **kw):
        events = self.stream(**kw)
        self.assertFalse([e for e in events if e["type"] == "error"], events)
        return next(e for e in events if e["type"] == "done")
    def send_request(self, done, **changes):
        return server.ReplyRequest(**{**{k: done[k] for k in ("draft_id", "scope_digest", "receipt_id", "review_revision")},
                                      "text": done["reply"], **changes})
    def pipeline(self, done):
        _, _, scope = repo.evidence_draft(self.a, self.mid, done["draft_id"], done["scope_digest"])
        return ReplyPipeline(self.a, self.mention, scope, self.client, self.provider)


class PipelineApiTest(PipelineFixture, unittest.TestCase):
    def test_real_endpoints_prepare_review_send_exact_and_duplicate(self):
        recorded_threads = []
        self.provider._usage_recorder.side_effect = lambda *args: recorded_threads.append(threading.get_ident())
        done = self.complete()
        self.assertEqual(done["assessment"]["status"], "evaluated")
        self.assertTrue(done["receipt_id"])
        stored = server.get_stored_draft(self.mid, self.viewer)
        self.assertEqual(stored["generation_config"]["assessment"], done["assessment"])
        text = "  正式環境已部署版本 7。\r\n"
        review = server.review_draft(self.mid, server.DraftReviewRequest(draft_id=done["draft_id"], text=text, scope_digest=done["scope_digest"]), self.viewer)
        self.assertTrue(review["receipt_id"])
        self.assertGreater(review["review_revision"], done["review_revision"])
        request = self.send_request({**done, **review, "reply": text})
        first = server.reply_to_mention(self.mid, request, self.viewer)
        second = server.reply_to_mention(self.mid, request, self.viewer)
        self.assertEqual(first["message_id"], second["message_id"])
        self.client.send_message.assert_called_once_with("spaces/target", text, thread_name=self.mention["thread_name"], single_attempt=True)
        self.assertTrue(recorded_threads)
        self.assertEqual(set(recorded_threads), {threading.get_ident()})

    def revoke_during(self, entered, release, revoke):
        async def run():
            request = server.DraftRequest(project_selection=server.ProjectSelection(**self.selection()))
            stream = server.draft_stream(self.mid, request, self.viewer).body_iterator
            task = asyncio.create_task(_drain(stream))
            try:
                async def wait_started():
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_started(), 3)
                revoke()
                release.set()
                return [json.loads(event.split("data: ", 1)[1]) for event in await task]
            finally:
                release.set()
                if not task.done():
                    task.cancel()
        return asyncio.run(run())

    def test_revocation_after_model_stops_further_calls_and_usage(self):
        original = self.provider.generate
        for setting in ('opt_in', 'PROJECT_CONTEXT_ENABLED', 'REPLY_EVIDENCE_ENABLED'):
            with self.subTest(setting=setting):
                repo.set_project_context_opt_in(self.a, True)
                self.provider.prompts.clear()
                self.provider._usage_recorder.reset_mock()
                entered, release = threading.Event(), threading.Event()
                def generate(prompt, **kwargs):
                    entered.set()
                    release.wait(3)
                    return original(prompt, **kwargs)
                self.provider.generate = generate
                def revoke():
                    if setting == 'opt_in':
                        repo.set_project_context_opt_in(self.a, False)
                    else:
                        setattr(config, setting, False)
                with patch.object(config, 'PROJECT_CONTEXT_ENABLED', True), patch.object(config, 'REPLY_EVIDENCE_ENABLED', True):
                    events = self.revoke_during(entered, release, revoke)
                self.assertEqual(events[-1]['code'], 'SCOPE_STALE')
                self.assertEqual([operation for operation, _ in self.provider.prompts], ['reply_private_analysis'])
                self.provider._usage_recorder.assert_not_called()
                self.assertIsNone(repo.latest_draft(self.mid))
                self.client.send_message.assert_not_called()

    def test_revocation_after_source_stops_reads_before_any_model(self):
        original = self.client.read_reply_message
        entered, release = threading.Event(), threading.Event()
        def read(name):
            entered.set()
            release.wait(3)
            return original(name)
        self.client.read_reply_message = read
        events = self.revoke_during(entered, release, lambda: repo.set_project_context_opt_in(self.a, False))
        self.assertEqual(events[-1]['code'], 'SCOPE_STALE')
        self.assertEqual(self.client.reads, [self.mention['message_name']])
        self.assertFalse(self.provider.prompts)
        self.assertIsNone(repo.latest_draft(self.mid))
        self.client.send_message.assert_not_called()

    def test_private_and_metadata_never_enter_public_prompt_or_database(self):
        self.client.messages["spaces/pm"] = "SECRET_PRIVATE_FACT"
        self.provider.gap, self.provider.text = True, "目前還缺正式環境部署證據。"
        done = self.complete(selection=self.selection("analysis_only"))
        self.assertTrue(done["receipt_id"])
        for operation, payload in self.provider.prompts:
            if operation == "reply_private_analysis":
                self.assertIn("SECRET_PRIVATE_FACT", json.dumps(payload))
            else:
                self.assertNotIn("SECRET_PRIVATE_FACT", json.dumps(payload))
        stored = server.get_stored_draft(self.mid, self.viewer)
        self.assertNotIn("SECRET_PRIVATE_FACT", json.dumps(stored))
        self.client.reads.clear()
        self.provider.prompts.clear()
        self.complete(selection=self.selection("metadata_only"))
        self.assertNotIn("spaces/pm", self.client.reads)
        self.assertNotIn("PM 共用", json.dumps(self.provider.prompts, ensure_ascii=False))

    def test_outgoing_draft_has_target_question_and_bounded_context(self):
        self.complete()
        draft_payload = next(payload for operation, payload in self.provider.prompts if operation == "reply_draft")
        self.assertEqual(draft_payload["fact_pack"]["question"], self.client.messages["spaces/target"])
        self.provider.prompts.clear()
        self.client.messages = {key: "假" * 20_000 for key in self.client.messages}
        events = self.stream()
        self.assertEqual(events[-1]["code"], "CONTEXT_BUDGET")
        self.assertFalse([event for event in events if event["type"] in ("chunk", "done")])
        self.assertFalse(self.provider.prompts)
        self.client.send_message.assert_not_called()

    def test_target_reader_is_bound_to_thread_and_rejects_other_threads(self):
        original = self.client.read_reply_recent
        observed = []
        def recent(space_id, limit, *, thread_name=None):
            observed.append((space_id, thread_name))
            rows = original(space_id, limit, thread_name=thread_name)
            if space_id == "spaces/target":
                rows[0]["thread"]["name"] = "spaces/target/threads/unrelated"
            return rows
        self.client.read_reply_recent = recent
        done = self.complete()
        self.assertIn(("spaces/target", self.mention["thread_name"]), observed)
        self.assertIsNone(done["receipt_id"])
        self.assertEqual(done["assessment"]["stop_reason"], "source_error")
        self.client.send_message.assert_not_called()

    def test_scripted_counterexamples_block_and_positive_passes(self):
        cases = [("PR merged", "正式環境已部署", "status", "insufficient", "match", "current"),
                 ("Done", "症狀已排除", "status", "insufficient", "match", "current"),
                 ("相關函式", "根因就是 timeout", "cause", "insufficient", "match", "current"),
                 ("UAT 已部署", "production 已部署", "status", "supported", "mismatch", "current"),
                 ("部署後已回滾", "現在已部署", "status", "contradicted", "match", "superseded")]
        for evidence, text, kind, relation, scope, currency in cases:
            with self.subTest(evidence=evidence):
                self.client.messages["spaces/pm"] = evidence
                self.provider.text, self.provider.claim_kind, self.provider.relation = text, kind, relation
                self.provider.scope, self.provider.currency = scope, currency
                done = self.complete()
                self.assertIsNone(done["receipt_id"])
                with self.assertRaises(ReplyReviewRequired):
                    server.reply_to_mention(self.mid, self.send_request(done), self.viewer)
        self.client.send_message.assert_not_called()

    def test_forged_expired_cross_owner_and_changed_text_zero_send(self):
        done = self.complete()
        for changes in ({"text": done["reply"] + " "}, {"receipt_id": "forged"}, {"review_revision": 9}):
            with self.assertRaises(ReplyReviewRequired):
                server.reply_to_mention(self.mid, self.send_request(done, **changes), self.viewer)
        with self.assertRaises(ReplyReviewRequired):
            repo.reply_receipt(self.b, self.mid, done["draft_id"], done["scope_digest"], done["receipt_id"], done["review_revision"], done["reply"])
        db.execute("UPDATE reply_reviews SET expires_at=0 WHERE draft_id=?", (done["draft_id"],))
        with self.assertRaises(ReplyReviewRequired):
            server.reply_to_mention(self.mid, self.send_request(done), self.viewer)
        self.client.send_message.assert_not_called()
        self.assertIsNone(server.get_stored_draft(self.mid, self.viewer)["generation_config"]["receipt_id"])

    def test_replay_change_requires_review_and_unknown_never_retries(self):
        done = self.complete()
        self.client.messages["spaces/pm"] += " 目前已回滾。"
        with self.assertRaises(ReplyReviewRequired):
            server.reply_to_mention(self.mid, self.send_request(done), self.viewer)
        self.client.send_message.assert_not_called()
        done = self.complete()
        self.client.send_message.side_effect = TimeoutError()
        for _ in range(2):
            with self.assertRaises(ReplyDispatchUnknown):
                server.reply_to_mention(self.mid, self.send_request(done), self.viewer)
        self.assertEqual(self.client.send_message.call_count, 1)
        self.assertEqual(db.query_one("SELECT state FROM reply_dispatches WHERE draft_id=?", (done["draft_id"],))["state"], "unknown")

    def test_feature_off_rejects_analysis_and_legacy_receipt_cannot_upgrade(self):
        with patch.object(config, "REPLY_EVIDENCE_ENABLED", False):
            req = server.DraftRequest(project_selection=server.ProjectSelection(**self.selection("analysis_only")))
            with self.assertRaises(InvalidParameter):
                server.draft_stream(self.mid, req, self.viewer)
        scope = self.admit(self.selection()).scope
        legacy = repo.create_project_draft(self.a, self.mid, "舊稿", {"meta": {"project_scope": scope}, "project_status": "complete"})
        with self.assertRaises(ReplyReviewRequired):
            server.reply_to_mention(self.mid, server.ReplyRequest(text="舊稿", draft_id=legacy["draft_id"], scope_digest=scope["digest"]), self.viewer)

    def test_failed_and_truncated_sources_no_receipt(self):
        original = self.client.read_reply_recent
        self.provider.gap, self.provider.text = True, "資料不足，仍需確認。"
        self.client.read_reply_recent = lambda *a: (_ for _ in ()).throw(ValueError("fake error"))
        done = self.complete()
        self.assertIsNone(done["receipt_id"])
        self.assertEqual(done["assessment"]["stop_reason"], "source_error")
        self.client.read_reply_recent = original
        self.client.messages["spaces/pm"] = "x" * 100001
        done = self.complete()
        self.assertIsNone(done["receipt_id"])
        self.assertEqual(done["assessment"]["stop_reason"], "partial")

    def test_schema_error_has_no_chunk_or_saved_draft(self):
        self.provider.plan = {"queries": [], "extra": "private"}
        events = self.stream()
        self.assertFalse([e for e in events if e["type"] in ("chunk", "done")])
        self.assertEqual(events[-1]["code"], "SCHEMA_ERROR")
        self.assertIsNone(repo.latest_draft(self.mid))

    def test_images_explicitly_not_assessed(self):
        self.client.attachments = True
        done = self.complete()
        self.assertIn("圖片", "".join(done["assessment"]["gaps"]))

    def test_review_later_started_wins_even_if_first_finishes_last(self):
        done = self.complete()
        first_entered, release = threading.Event(), threading.Event()
        def block(payload):
            if payload["text"] == "較早文字":
                first_entered.set()
                release.wait(3)
        self.provider.on_assess = block
        async def race():
            first = asyncio.create_task(self.pipeline(done).review(done["draft_id"], "較早文字", done["scope_digest"]))
            while not first_entered.is_set():
                await asyncio.sleep(0.01)
            second = await self.pipeline(done).review(done["draft_id"], "較新文字", done["scope_digest"])
            release.set()
            with self.assertRaises(ReplyReviewRequired):
                await first
            return second
        second = asyncio.run(race())
        stored = server.get_stored_draft(self.mid, self.viewer)
        self.assertEqual(stored["content_md"], "較新文字")
        self.assertEqual(stored["generation_config"]["receipt_id"], second["receipt_id"])

    def test_cancel_and_deadline_drop_late_results(self):
        entered, release = threading.Event(), threading.Event()
        original = self.provider.generate
        def blocked(prompt, **kwargs):
            entered.set()
            release.wait(3)
            return original(prompt, **kwargs)
        self.provider.generate = blocked
        scope = self.admit(self.selection()).scope
        async def cancelled():
            pipeline = ReplyPipeline(self.a, self.mention, scope, self.client, self.provider)
            events = []
            async def drain():
                async for event in pipeline.prepare(provider_name="fake", options=server.reply_profiles.ReplyGenerationOptions(), reply_meta={}):
                    events.append(event)
            task = asyncio.create_task(drain())
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            await asyncio.sleep(0.1)
            return events
        events = asyncio.run(cancelled())
        self.assertFalse([e for e in events if e["type"] in ("chunk", "done")])
        self.assertIsNone(repo.latest_draft(self.mid))
        entered.clear(); release.clear()
        control_factory = lambda limits=None: RunControl(Limits(seconds=0.03, call_seconds=0.03))
        with patch.object(reply_pipeline, "RunControl", control_factory):
            events = self.stream()
        release.set()
        time.sleep(0.1)
        self.assertEqual(events[-1]["code"], "DEADLINE")
        self.assertIsNone(repo.latest_draft(self.mid))
        self.provider._usage_recorder.assert_not_called()


class PipelineBudgetTest(unittest.TestCase):
    def test_limits_and_budget_are_shared(self):
        for limits in ({"seconds": 91}, {"reads": 13}, {"followups": 4}, {"models": 11}, {"reads": 1.5}):
            with self.assertRaises(ValueError):
                Limits(**limits)
        control = RunControl(Limits(reads=1, models=1))
        control.consume("reads")
        with self.assertRaises(RunStopped) as error:
            control.consume("reads")
        self.assertEqual(error.exception.reason, BlockReason.READ_BUDGET)
        control.consume("model_calls")
        with self.assertRaises(RunStopped):
            control.consume("model_calls")
