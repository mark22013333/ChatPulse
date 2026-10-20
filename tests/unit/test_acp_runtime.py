import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import shlex
import time
import unittest
from unittest.mock import patch

from core.acp_runtime import AgentRuntime, _SyntheticMcp
from core.agent_runtime import (Cancelled, Completed, CredentialSnapshot, Failed, FailureCode,
                                DockerProbe, HostFixture, RuntimeRejected, SyntheticScope)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "agent_protocol" / "child.py"


def runtime(mode="normal", **kwargs):
    value = AgentRuntime(HostFixture((sys.executable, str(FIXTURE), mode)), **kwargs)
    value.replace_credentials("viewer-a", CredentialSnapshot("viewer-a", "subject-a", 1,
                                                            time.time() + 300, "synthetic-secret-token"))
    return value


class AcpRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_drains_slow_async_callback_on_owner_loop(self):
        app = runtime("chunks")
        events = []
        owner_loop = asyncio.get_running_loop()
        async def emit(event):
            self.assertIs(asyncio.get_running_loop(), owner_loop)
            await asyncio.sleep(0.06)
            events.append(event.text)
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示", emit=emit)
        self.assertIsInstance(result, Completed)
        self.assertEqual("".join(events), result.text)
        self.assertEqual(len(events), 12)

    async def test_sync_callback_rejected_before_claim(self):
        app = runtime()
        lease = app.issue("viewer-a", SyntheticScope())
        with self.assertRaises(RuntimeRejected):
            app.run(lease, "合成提示", emit=lambda event: None)
        self.assertIsInstance(await app.run(lease, "合成提示"), Completed)

    def test_scope_digest_has_unambiguous_encoding(self):
        self.assertNotEqual(SyntheticScope(("a", "b")).digest,
                            SyntheticScope(("a\0b",)).digest)

    async def test_typed_warning_does_not_replace_model_output(self):
        app = runtime("typed_warning")
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Completed)
        self.assertEqual(result.text, "SYNTHETIC_MARKER")

    async def test_valid_core_update_lists_preserve_completion(self):
        app = runtime("core_updates")
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Completed)

    async def test_cancel_during_docker_creation_is_never_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic-docker"
            executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                                  shlex.quote(str(FIXTURE)) + ' docker_create_pending "$@"\n')
            executable.chmod(0o700)
            app = AgentRuntime(DockerProbe(str(executable), "sha256:" + "a" * 64, "unix:///synthetic.sock"))
            app.replace_credentials("viewer-a", CredentialSnapshot("viewer-a", "subject-a", 1,
                                                                   time.time() + 60, "synthetic-secret"))
            lease = app.issue("viewer-a", SyntheticScope())
            cancel_signal = asyncio.Event()
            task = asyncio.create_task(app.run(lease, "合成提示", cancelled=cancel_signal.wait()))
            await asyncio.sleep(0.1)
            cancel_signal.set()
            done, _ = await asyncio.wait({task}, timeout=3)
            self.assertTrue(done)
            result = task.result()
            self.assertIsInstance(result, Cancelled)
            self.assertFalse(result.cleanup.confirmed)

    async def test_normal_real_child(self):
        app = runtime()
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Completed)
        self.assertEqual(result.text, "SYNTHETIC_MARKER")
        self.assertTrue(result.cleanup.confirmed)

    async def test_wire_failures_never_complete(self):
        for mode, code in (("eof", FailureCode.PROCESS), ("error", FailureCode.AGENT),
                           ("empty", FailureCode.EMPTY_RESULT), ("version", FailureCode.VERSION),
                           ("malformed", FailureCode.PROTOCOL), ("nan", FailureCode.PROTOCOL),
                           ("infinity", FailureCode.PROTOCOL), ("typed_update_failure", FailureCode.AGENT),
                           ("typed_result_failure", FailureCode.AGENT), ("session_secret", FailureCode.PROTOCOL),
                           ("bad_plan", FailureCode.PROTOCOL), ("bad_commands", FailureCode.PROTOCOL),
                           ("bad_config", FailureCode.PROTOCOL), ("wrong_id", FailureCode.PROTOCOL),
                           ("wrong_session", FailureCode.PROTOCOL), ("flood", FailureCode.LIMIT),
                           ("stderr", FailureCode.LIMIT), ("unknown_flood", FailureCode.LIMIT)):
            with self.subTest(mode=mode):
                app = runtime(mode)
                result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
                self.assertIsInstance(result, Failed)
                self.assertEqual(result.code, code)
                self.assertTrue(result.cleanup.confirmed)

    async def test_atomic_claim_and_forged_copy(self):
        app = runtime()
        lease = app.issue("viewer-a", SyntheticScope())
        with self.assertRaises(RuntimeRejected):
            app.run(replace(lease), "合成提示")
        first = app.run(lease, "合成提示")
        with self.assertRaises(RuntimeRejected):
            app.run(lease, "合成提示")
        self.assertIsInstance(await first, Completed)
        with self.assertRaises(RuntimeRejected):
            app.run(lease, "合成提示")

    async def test_viewer_binding_refresh_and_subject_change(self):
        app = runtime()
        with self.assertRaises(RuntimeRejected):
            app.issue("viewer-b", SyntheticScope())
        lease = app.issue("viewer-a", SyntheticScope())
        app.replace_credentials("viewer-a", CredentialSnapshot("viewer-a", "subject-a", 2,
                                                               time.time() + 300, "replacement-secret"))
        with self.assertRaises(RuntimeRejected):
            app.run(lease, "合成提示")
        new = app.issue("viewer-a", SyntheticScope())
        self.assertEqual(new.generation, 2)
        with self.assertRaises(RuntimeRejected):
            app.replace_credentials("viewer-a", CredentialSnapshot("viewer-b", "subject-a", 3,
                                                                   time.time() + 300, "replacement-secret"))
        with self.assertRaises(RuntimeRejected):
            app.run(new, "合成提示")

    async def test_cancel_reaps_uncooperative_grandchild(self):
        app = runtime("grandchild")
        lease = app.issue("viewer-a", SyntheticScope())
        observed = asyncio.Event()
        pids = []
        async def emit(event):
            pids.append(int(event.text.strip()))
            observed.set()
        task = asyncio.create_task(app.run(lease, "合成提示", emit=emit))
        await asyncio.wait_for(observed.wait(), 2)
        start = time.monotonic()
        app.cancel(lease)
        result = await task
        self.assertIsInstance(result, Cancelled)
        self.assertTrue(result.cleanup.confirmed)
        self.assertLess(time.monotonic() - start, 3)
        await asyncio.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)

    async def test_slow_callback_does_not_block_external_cancel(self):
        app = runtime("hang")
        lease = app.issue("viewer-a", SyntheticScope())
        entered = asyncio.Event()
        async def emit(event):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(app.run(lease, "合成提示", emit=emit))
        await asyncio.wait_for(entered.wait(), 2)
        start = time.monotonic()
        task.cancel()
        result = await task
        self.assertIsInstance(result, Cancelled)
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(result.cleanup.confirmed)

    async def test_callback_exception_fails(self):
        app = runtime("hang")
        async def emit(event):
            raise ValueError("不能發布")
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示", emit=emit)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, FailureCode.CALLBACK)

    async def test_revoke_blocks_future_lease_until_rebinding(self):
        app = runtime()
        app.revoke_viewer("viewer-a")
        with self.assertRaises(RuntimeRejected):
            app.issue("viewer-a", SyntheticScope())

    async def test_noncooperative_cancel_awaitable_is_bounded_and_unconfirmed(self):
        app = runtime("hang")
        lease = app.issue("viewer-a", SyntheticScope())
        entered, release = asyncio.Event(), asyncio.Event()
        async def cancel_signal():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        task = asyncio.create_task(app.run(lease, "合成提示", cancelled=cancel_signal()))
        await entered.wait()
        app.cancel(lease)
        try:
            done, _ = await asyncio.wait({task}, timeout=3)
            self.assertTrue(done)
            result = task.result()
            self.assertIsInstance(result, Cancelled)
            self.assertFalse(result.cleanup.confirmed)
        finally:
            release.set()
            await task
            await asyncio.sleep(0)

    async def test_broker_start_failure_remains_typed_failure(self):
        app = runtime()
        with patch.object(_SyntheticMcp, "start", side_effect=OSError("合成啟動失敗")):
            result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Failed)
        self.assertTrue(result.cleanup.confirmed)

    async def test_deadline(self):
        app = runtime("hang")
        result = await app.run(app.issue("viewer-a", SyntheticScope(), timeout=0.1), "合成提示")
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, FailureCode.DEADLINE)

    async def test_environment_and_secret_redaction(self):
        with patch.dict(os.environ, {"ADO_PAT": "host-fake-secret", "OPENAI_API_KEY": "host-fake-key"}):
            app = runtime("env")
            result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Completed)
        env = json.loads(result.text)
        self.assertIsNone(env["ado"])
        self.assertIsNone(env["openai"])
        self.assertNotEqual(env["home"], str(Path.home()))
        self.assertTrue(env["codex_home"].startswith(env["cwd"]))
        app = runtime("secret")
        events = []
        async def emit(event):
            events.append(event.text)
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示", emit=emit)
        self.assertNotIn("synthetic-secret-token", repr(result))
        self.assertNotIn("synthetic-secret-token", "".join(events))
        self.assertEqual(result.text, "[已遮蔽]")

    async def test_actual_mcp_http_and_scope_denial(self):
        app = runtime("mcp", evidence={"allowed": "合成證據", "outside": "禁止的合成資料"})
        result = await app.run(app.issue("viewer-a", SyntheticScope(("allowed",))), "合成提示")
        self.assertIsInstance(result, Completed)
        value = json.loads(result.text)
        self.assertFalse(value["allowed"]["isError"])
        self.assertTrue(value["denied"]["isError"])
        self.assertNotIn("禁止的合成資料", result.text)

    async def test_late_event_and_nonzero_exit(self):
        app = runtime("late")
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Completed)
        self.assertNotIn("LATE", result.text)
        app = runtime("nonzero")
        result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
        self.assertIsInstance(result, Failed)
