import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

from core.acp_runtime import _Acp, _Delivery
from core.agent_runtime import (AgentEvent, Cancelled, CleanupReceipt, Completed, DockerProbe,
                                Failed, FailureCode, HostFixture, RuntimeRejected)
from scripts import probe_agent_runtime as probe


MISSING_PARAMS = object()
CLEAN = CleanupReceipt(True, "docker")
ISOLATED = "HOST_UNAVAILABLE SYMLINK_UNAVAILABLE ADO_PAT_ABSENT SOCKET_ABSENT CHATPULSE_INSIDE_CANARY"
MOUNTED = "HOST_READABLE SYMLINK_READABLE SYNTHETIC_HOST_SECRET ADO_PAT_ABSENT SOCKET_ABSENT CHATPULSE_INSIDE_CANARY"


class ProbeVerdictTests(unittest.TestCase):
    def test_http400_requires_agent_failure_and_both_model_facts(self):
        facts = probe._ProbeFacts("http400")
        result = Failed("run", FailureCode.AGENT, CLEAN)
        for stages in (set(), {"request_received"}, {"http400_sent"}):
            facts.stages = stages
            self.assertFalse(probe._http_failure_valid(result, facts))
        facts.stages = {"request_received", "http400_sent"}
        self.assertTrue(probe._http_failure_valid(result, facts))
        for code in (FailureCode.PROCESS, FailureCode.PROTOCOL, FailureCode.DEADLINE):
            self.assertFalse(probe._http_failure_valid(replace(result, code=code), facts))
        self.assertFalse(probe._http_failure_valid(replace(result, cleanup=CleanupReceipt(False, "docker")), facts))

    def test_isolation_requires_readable_mounted_control(self):
        normal = Completed("run", "session", ISOLATED, CLEAN)
        control = Completed("control", "session", MOUNTED, CLEAN)
        self.assertTrue(probe._isolation_valid(control, normal))
        self.assertFalse(probe._isolated_canary_valid(control))
        for unreadable in (normal, replace(control, text="HOST_READABLE"),
                           Failed("control", FailureCode.AGENT, CLEAN)):
            self.assertFalse(probe._isolation_valid(unreadable, normal))
        self.assertFalse(probe._isolation_valid(control, replace(normal, text=ISOLATED + " SYNTHETIC_HOST_SECRET")))

    def test_only_private_control_adds_exact_readonly_file_mount(self):
        path = "/private/tmp/chatpulse-synthetic/secret.txt"
        launcher = DockerProbe("/synthetic/docker", "sha256:" + "a" * 64, "unix:///synthetic.sock", "canary", path)
        normal = probe.new_runtime(launcher=launcher)._docker_command("normal", create=True)
        control = probe.new_runtime(launcher=launcher, runtime_type=probe._MountedControlRuntime)._docker_command("control", create=True)
        mount = control.index("--mount")
        self.assertEqual(control[mount + 1], f"type=bind,source={path},target={path},readonly")
        self.assertEqual(control[mount + 2], launcher.image_id)
        self.assertNotIn("--mount", normal)
        self.assertEqual(tuple(value for value in control[:mount] if value != "control"),
                         tuple(value for value in normal[:normal.index(launcher.image_id)] if value != "normal"))


class ProbeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_request_and_reaps_without_readiness(self):
        class App:
            def __init__(self, fact):
                self.fact = fact
                self.cancelled = asyncio.Event()
                self.finished = False

            def issue(self, *args, **kwargs):
                return "lease"

            def cancel(self, lease):
                self.cancelled.set()

            async def run(self, lease, prompt, emit):
                try:
                    if self.fact:
                        await emit(AgentEvent("run", "probe", json.dumps({"mode": "cancel", "stage": "request_received"})))
                    await self.cancelled.wait()
                    return Cancelled("run", CLEAN)
                finally:
                    self.finished = True

        for received in (False, True):
            app = App(received)
            report = await probe._cancel_after_request(app, ready_timeout=0.02)
            self.assertEqual(report["status"], "pass" if received else "failed")
            self.assertEqual(report["request_received"], received)
            self.assertTrue(app.finished)

    async def rpc_facts(self, params, *, host=False, emit=True):
        launcher = DockerProbe("/synthetic/docker", "sha256:" + "a" * 64, "unix:///synthetic.sock", "http400")
        if host:
            launcher = HostFixture((sys.executable,))
        app = probe.new_runtime(launcher=launcher)
        lease = app.issue("synthetic-viewer", probe.SyntheticScope())
        run = app._runs[lease.run_id]
        notifications = [{"jsonrpc": "2.0", "method": "_chatpulse/probe",
                          **({} if fact is MISSING_PARAMS else {"params": fact})} for fact in params]
        messages = notifications + [{"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "合成失敗"}}]
        script = "import sys\nsys.stdin.readline()\n" + "\n".join(f"print({json.dumps(json.dumps(value))}, flush=True)" for value in messages)
        child = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        events = []
        async def callback(event):
            await asyncio.sleep(0.01)
            events.append(event)
        delivery = _Delivery(callback if emit else None, 64, lambda: app._authorized(run))
        protocol = _Acp(child, run, app._limits, delivery, app, str(Path.cwd()))
        try:
            with self.assertRaises(RuntimeRejected) as raised:
                await asyncio.wait_for(protocol.rpc("synthetic", {}), 2)
            return raised.exception.code, events, protocol.events
        finally:
            await delivery.close()
            if child.returncode is None:
                child.kill()
            await child.communicate()

    async def test_probe_facts_delivered_before_terminal_error(self):
        facts = [{"mode": "http400", "stage": stage} for stage in ("request_received", "http400_sent")]
        code, events, count = await self.rpc_facts(facts)
        self.assertEqual(code, FailureCode.AGENT)
        self.assertEqual([json.loads(event.text) for event in events], facts)
        self.assertTrue(all(event.kind == "probe" for event in events))
        self.assertEqual(count, 3)
        code, events, count = await self.rpc_facts(facts, emit=False)
        self.assertEqual((code, events, count), (FailureCode.AGENT, [], 3))

    async def test_private_facts_reject_malformed_or_foreign_mode(self):
        for fact in (MISSING_PARAMS, None, {"mode": "cancel", "stage": "request_received"},
                     {"mode": "http400", "stage": "arbitrary"},
                     {"mode": "http400", "stage": "request_received", "secret": "synthetic"},
                     {"mode": "http400"}, [], {"mode": "http400", "stage": {}}):
            with self.subTest(fact=fact):
                code, events, _ = await self.rpc_facts([fact])
                self.assertEqual(code, FailureCode.PROTOCOL)
                self.assertEqual(events, [])

    async def test_host_fixture_does_not_publish_private_facts(self):
        code, events, count = await self.rpc_facts([{"mode": "http400", "stage": "request_received"}], host=True)
        self.assertEqual((code, events, count), (FailureCode.AGENT, [], 2))
