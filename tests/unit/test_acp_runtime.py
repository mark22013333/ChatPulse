import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import shlex
import signal
import subprocess
import time
from types import SimpleNamespace
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

    async def test_cancelled_before_start_recovers_capacity_after_expiry_or_revoke(self):
        for invalidation in ("expiry", "revoke"):
            with self.subTest(invalidation=invalidation):
                app = runtime()
                now = [100]
                with patch("core.acp_runtime.time", SimpleNamespace(monotonic=lambda: now[0], time=time.time)):
                    tasks = []
                    for _ in range(1024):
                        lease = app.issue("viewer-a", SyntheticScope(), timeout=1)
                        task = asyncio.create_task(app.run(lease, "合成提示"))
                        task.cancel()
                        tasks.append(task)
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    self.assertTrue(all(isinstance(result, asyncio.CancelledError) for result in results))
                    with self.assertRaises(RuntimeRejected) as rejected:
                        app.issue("viewer-a", SyntheticScope())
                    self.assertEqual(rejected.exception.code, FailureCode.LIMIT)
                    if invalidation == "expiry":
                        now[0] = 102
                    else:
                        app.revoke_viewer("viewer-a")
                        app.replace_credentials("viewer-a", CredentialSnapshot(
                            "viewer-a", "subject-a", 2, time.time() + 300, "replacement-secret"))
                    result = await app.run(app.issue("viewer-a", SyntheticScope()), "合成提示")
                self.assertIsInstance(result, Completed)
                self.assertTrue(result.cleanup.confirmed)

    async def test_invalidated_claim_never_starts_broker_or_process_after_reclamation(self):
        for docker in (False, True):
            for invalidation in ("expiry", "revoke"):
                with self.subTest(docker=docker, invalidation=invalidation):
                    app = runtime()
                    if docker:
                        app = AgentRuntime(DockerProbe("/synthetic/docker", "sha256:" + "a" * 64,
                                                       "unix:///synthetic.sock"))
                        app.replace_credentials("viewer-a", CredentialSnapshot(
                            "viewer-a", "subject-a", 1, time.time() + 300, "synthetic-secret"))
                    now = [100]
                    with patch("core.acp_runtime.time", SimpleNamespace(monotonic=lambda: now[0], time=time.time)):
                        lease = app.issue("viewer-a", SyntheticScope(), timeout=1)
                        pending = app.run(lease, "合成提示")
                        if invalidation == "expiry":
                            now[0] = 102
                        else:
                            app.revoke_viewer("viewer-a")
                            app.replace_credentials("viewer-a", CredentialSnapshot(
                                "viewer-a", "subject-a", 2, time.time() + 300, "replacement-secret"))
                        app.issue("viewer-a", SyntheticScope())
                        with patch.object(_SyntheticMcp, "start") as broker_start, \
                                patch("core.acp_runtime.asyncio.create_subprocess_exec") as spawn:
                            result = await pending
                        broker_start.assert_not_awaited()
                        spawn.assert_not_awaited()
                    if invalidation == "expiry":
                        self.assertIsInstance(result, Failed)
                        self.assertEqual(result.code, FailureCode.DEADLINE)
                    else:
                        self.assertIsInstance(result, Cancelled)
                    self.assertTrue(result.cleanup.confirmed)
                    with self.assertRaises(RuntimeRejected):
                        app.run(lease, "合成提示")

    async def test_active_revoked_run_keeps_capacity_until_supervisor_cleanup(self):
        app = runtime("hang")
        app.replace_credentials("viewer-b", CredentialSnapshot(
            "viewer-b", "subject-b", 1, time.time() + 300, "synthetic-secret-b"))
        ready, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cleanup = app._cleanup
        async def emit(event):
            ready.set()
        async def delayed_cleanup(*args):
            cleaning.set()
            await release.wait()
            return await cleanup(*args)
        with patch.object(app, "_cleanup", side_effect=delayed_cleanup):
            task = asyncio.create_task(app.run(app.issue("viewer-a", SyntheticScope()),
                                              "合成提示", emit=emit))
            try:
                await asyncio.wait_for(ready.wait(), 2)
                app.revoke_viewer("viewer-a")
                await asyncio.wait_for(cleaning.wait(), 2)
                for _ in range(1023):
                    app.issue("viewer-b", SyntheticScope())
                with self.assertRaises(RuntimeRejected) as rejected:
                    app.issue("viewer-b", SyntheticScope())
                self.assertEqual(rejected.exception.code, FailureCode.LIMIT)
            finally:
                release.set()
                result = await task
        self.assertIsInstance(result, Cancelled)
        self.assertTrue(result.cleanup.confirmed)
        app.issue("viewer-b", SyntheticScope())

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
        self.assertIsInstance(result, Completed)
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


class AcpExitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processes = []
        self.child = None
        self.real_killpg = os.killpg
        spawn = asyncio.create_subprocess_exec

        async def observed_spawn(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            self.processes.append(process)
            if kwargs.get("stdin") == asyncio.subprocess.PIPE:
                self.child = process
            return process

        self.spawn_patch = patch("core.acp_runtime.asyncio.create_subprocess_exec", observed_spawn)
        self.spawn_patch.start()
        self.addCleanup(self.spawn_patch.stop)

    async def asyncTearDown(self):
        for process in self.processes:
            if process.returncode is None:
                try:
                    self.real_killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(process.wait(), 2)

    def wait_for_process_exit(self, pid):
        deadline = time.monotonic() + 2
        while True:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=2)
            if status.returncode == 1 or status.stdout.strip().startswith("Z"):
                break
            if status.returncode != 0 or time.monotonic() >= deadline:
                raise AssertionError("合成子程序未在期限內自然退出")

    def release_before_signal(self, marker, pid, sig):
        marker.touch()
        self.wait_for_process_exit(pid)
        return self.real_killpg(pid, sig)

    async def run_app(self, app, **kwargs):
        return await asyncio.wait_for(app.run(app.issue("viewer-a", SyntheticScope()),
                                             "合成提示", **kwargs), 8)

    async def test_natural_exit_after_cleanup_snapshot(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "release"
                app = runtime()
                app._launcher = HostFixture((sys.executable, str(FIXTURE), "release_exit",
                                             str(marker), str(exit_code)))
                with patch("core.acp_runtime.os.killpg", side_effect=lambda pid, sig:
                           self.release_before_signal(marker, pid, sig)):
                    result = await self.run_app(app)
                self.assertEqual(self.child.returncode, exit_code)
                self.assertTrue(result.cleanup.confirmed)
                if exit_code:
                    self.assertIsInstance(result, Failed)
                    self.assertEqual(result.code, FailureCode.PROCESS)
                else:
                    self.assertIsInstance(result, Completed)

    async def test_natural_nonzero_survives_successful_group_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "release"
            app = runtime()
            app._launcher = HostFixture((sys.executable, str(FIXTURE), "release_exit",
                                         str(marker), "7", "sibling"))
            delivered = []

            def killpg(pid, sig):
                value = self.release_before_signal(marker, pid, sig)
                delivered.append(pid)
                return value

            try:
                with patch("core.acp_runtime.os.killpg", side_effect=killpg):
                    result = await self.run_app(app)
                self.assertEqual(delivered, [self.child.pid])
                self.assertEqual(self.child.returncode, 7)
                self.assertTrue(result.cleanup.confirmed)
                self.assertIsInstance(result, Failed)
                self.assertEqual(result.code, FailureCode.PROCESS)
            finally:
                if self.child is not None:
                    try:
                        self.real_killpg(self.child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                sibling = marker.with_suffix(".sibling")
                if sibling.exists():
                    self.wait_for_process_exit(int(sibling.read_text()))

    async def test_lingering_child_killed_by_runtime_still_completes(self):
        result = await self.run_app(runtime("linger"))
        self.assertEqual(self.child.returncode, -signal.SIGKILL)
        self.assertIsInstance(result, Completed)
        self.assertTrue(result.cleanup.confirmed)

    async def test_observed_external_sigkill_is_not_cleanup_success(self):
        async def emit(event):
            self.real_killpg(self.child.pid, signal.SIGKILL)
            await asyncio.wait_for(self.child.wait(), 2)

        result = await self.run_app(runtime("linger"), emit=emit)
        self.assertEqual(self.child.returncode, -signal.SIGKILL)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, FailureCode.PROCESS)
        self.assertTrue(result.cleanup.confirmed)

    async def test_docker_attach_exit_before_and_after_removal(self):
        for exit_before_removal in (True, False):
            with self.subTest(exit_before_removal=exit_before_removal), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "release"
                executable = Path(directory) / "synthetic-docker"
                executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                                      shlex.quote(str(FIXTURE)) + " docker_exit " +
                                      shlex.quote(str(marker)) + ' "$@"\n')
                executable.chmod(0o700)
                app = AgentRuntime(DockerProbe(str(executable), "sha256:" + "a" * 64,
                                               "unix:///synthetic.sock"))
                app.replace_credentials("viewer-a", CredentialSnapshot(
                    "viewer-a", "subject-a", 1, time.time() + 60, "synthetic-secret"))

                async def emit(event):
                    if exit_before_removal:
                        marker.touch()
                        await asyncio.wait_for(self.child.wait(), 2)

                result = await self.run_app(app, emit=emit)
                self.assertEqual(self.child.returncode, 7)
                self.assertTrue(result.cleanup.confirmed)
                if exit_before_removal:
                    self.assertIsInstance(result, Failed)
                    self.assertEqual(result.code, FailureCode.PROCESS)
                else:
                    self.assertIsInstance(result, Completed)

    async def test_callback_failure_keeps_priority_over_nonzero_exit(self):
        async def emit(event):
            await asyncio.wait_for(self.child.wait(), 2)
            raise ValueError("合成 callback 失敗")

        result = await self.run_app(runtime("nonzero"), emit=emit)
        self.assertEqual(self.child.returncode, 7)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, FailureCode.CALLBACK)
        self.assertTrue(result.cleanup.confirmed)

    async def test_cancel_keeps_priority_over_nonzero_exit(self):
        app = runtime("nonzero")
        lease = app.issue("viewer-a", SyntheticScope())

        async def emit(event):
            await asyncio.wait_for(self.child.wait(), 2)
            app.cancel(lease)

        result = await asyncio.wait_for(app.run(lease, "合成提示", emit=emit), 8)
        self.assertEqual(self.child.returncode, 7)
        self.assertIsInstance(result, Cancelled)
        self.assertTrue(result.cleanup.confirmed)

    async def test_unconfirmed_cleanup_does_not_complete(self):
        with patch("core.acp_runtime.os.killpg", side_effect=PermissionError("合成訊號遭拒")):
            result = await self.run_app(runtime("linger"))
        self.assertIsNone(self.child.returncode)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, FailureCode.CLEANUP)
        self.assertFalse(result.cleanup.confirmed)
