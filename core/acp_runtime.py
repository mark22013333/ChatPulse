from __future__ import annotations

import asyncio
import json
import inspect
import math
import os
import re
import signal
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Mapping

from core.agent_runtime import (
    AgentEvent, Cancelled, CleanupReceipt, Completed, CredentialSnapshot,
    DockerProbe, EventCallback, Failed, FailureCode, HostFixture, RunOutcome,
    RunState, RuntimeLease, RuntimeLimits, RuntimeRejected, SyntheticScope,
)


@dataclass
class _Run:
    lease: RuntimeLease
    scope: SyntheticScope
    credential: CredentialSnapshot = field(repr=False)
    state: RunState | None = None
    revoked: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False


class _Delivery:
    def __init__(self, emit: EventCallback | None, capacity: int, authorized):
        self.authorized = authorized
        self.queue = asyncio.Queue(capacity)
        self.failed = asyncio.Event()
        self.closed = False
        self.task = asyncio.create_task(self._consume(emit)) if emit else None

    async def _consume(self, emit):
        try:
            while True:
                event = await self.queue.get()
                try:
                    if self.authorized():
                        await emit(event)
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed.set()

    def publish(self, event):
        if self.task and not self.closed:
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                raise RuntimeRejected(FailureCode.LIMIT) from None

    async def finish(self):
        if self.task is not None:
            await self.queue.join()

    async def close(self):
        self.closed = True
        if self.task is None:
            return True
        self.task.cancel()
        done, _ = await asyncio.wait({self.task}, timeout=0.1)
        return bool(done)


class AgentRuntime:
    def __init__(self, launcher: HostFixture | DockerProbe, *,
                 evidence: Mapping[str, str] | None = None,
                 limits: RuntimeLimits | None = None):
        if os.name != "posix":
            raise RuntimeRejected(FailureCode.ISOLATION_BLOCKED)
        if isinstance(launcher, DockerProbe) and not re.fullmatch(r"sha256:[a-f0-9]{64}", launcher.image_id):
            raise RuntimeRejected(FailureCode.ISOLATION_BLOCKED)
        if isinstance(launcher, DockerProbe) and (not launcher.engine_endpoint.startswith("unix:///") or
                "\x00" in launcher.engine_endpoint):
            raise RuntimeRejected(FailureCode.ISOLATION_BLOCKED)
        if isinstance(launcher, DockerProbe) and (launcher.fixture_mode not in ("marker", "http400", "cancel", "canary", "versions") or
                not launcher.canary_path.startswith("/") or "\x00" in launcher.canary_path):
            raise RuntimeRejected(FailureCode.ISOLATION_BLOCKED)
        self._launcher = launcher
        self._evidence = dict(evidence or {})
        if any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 16384
               for k, v in self._evidence.items()):
            raise RuntimeRejected(FailureCode.LIMIT)
        self._limits = limits or RuntimeLimits()
        self._bindings: dict[str, CredentialSnapshot] = {}
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()

    def replace_credentials(self, viewer_id: str, snapshot: CredentialSnapshot) -> None:
        """更新必須來自可信入口。換 owner／subject 會撤銷舊 binding 並拒絕更新。"""
        with self._lock:
            previous = self._bindings.get(viewer_id)
            if (snapshot.viewer_id != viewer_id or not viewer_id or
                    not snapshot.external_subject or type(snapshot.generation) is not int or
                    snapshot.generation < 1 or not isinstance(snapshot.secret, str) or
                    not snapshot.secret or not math.isfinite(snapshot.expires_at) or
                    snapshot.expires_at <= time.time() or
                    (previous and (previous.external_subject != snapshot.external_subject or
                                   snapshot.generation <= previous.generation))):
                self.revoke_viewer(viewer_id)
                self._bindings.pop(viewer_id, None)
                raise RuntimeRejected(FailureCode.AUTH)
            self.revoke_viewer(viewer_id)
            self._bindings[viewer_id] = snapshot

    def revoke_viewer(self, viewer_id: str) -> None:
        with self._lock:
            self._bindings.pop(viewer_id, None)
            for run in self._runs.values():
                if run.lease.viewer_id == viewer_id:
                    self.cancel(run.lease)

    def issue(self, viewer_id: str, scope: SyntheticScope, *, timeout: float = 30) -> RuntimeLease:
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise RuntimeRejected(FailureCode.DEADLINE)
        with self._lock:
            for key, previous in tuple(self._runs.items()):
                if previous.state is None and not self._authorized(previous):
                    self._runs.pop(key)
            if len(self._runs) >= 1024:
                raise RuntimeRejected(FailureCode.LIMIT)
            binding = self._bindings.get(viewer_id)
            if binding is None or binding.expires_at <= time.time():
                raise RuntimeRejected(FailureCode.AUTH)
            if not set(scope.evidence_ids).issubset(self._evidence):
                raise RuntimeRejected(FailureCode.LEASE)
            lease = RuntimeLease(viewer_id, binding.external_subject, binding.generation,
                                 uuid.uuid4().hex, scope.digest,
                                 time.monotonic() + min(timeout, binding.expires_at - time.time()))
            self._runs[lease.run_id] = _Run(lease, scope, binding)
            return lease

    def _authorized(self, run: _Run) -> bool:
        current = self._bindings.get(run.lease.viewer_id)
        return (not run.revoked.is_set() and current is run.credential and
                time.monotonic() < run.lease.deadline and current.expires_at > time.time())

    def cancel(self, lease: RuntimeLease) -> None:
        with self._lock:
            run = self._runs.get(lease.run_id)
            if run is not None and run.lease is lease and run.state in (None, RunState.RUNNING):
                run.cancel_requested = True
                run.revoked.set()

    def run(self, lease: RuntimeLease, prompt: str, *, emit: EventCallback | None = None,
            cancelled: Awaitable[None] | None = None) -> Awaitable[RunOutcome]:
        """同步 claim 後回傳 awaitable，重複呼叫在第一個 await 前即遭拒絕。"""
        if emit is not None and not (inspect.iscoroutinefunction(emit) or
                inspect.iscoroutinefunction(getattr(emit, "__call__", None))):
            raise RuntimeRejected(FailureCode.CALLBACK)
        with self._lock:
            run = self._runs.get(lease.run_id)
            if run is None or run.lease is not lease or run.state is not None or not self._authorized(run):
                raise RuntimeRejected(FailureCode.LEASE)
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > self._limits.prompt_chars:
                raise RuntimeRejected(FailureCode.LIMIT)
            run.state = RunState.RUNNING
        return self._guard(run, prompt, emit, cancelled)

    async def _guard(self, run, prompt, emit, cancelled):
        owner = asyncio.create_task(self._supervise(run, prompt, emit, cancelled))
        while True:
            try:
                return await asyncio.shield(owner)
            except asyncio.CancelledError:
                self.cancel(run.lease)

    async def _supervise(self, run, prompt, emit, cancelled):
        delivery = _Delivery(emit, self._limits.callback_queue, lambda: self._authorized(run))
        process = None
        creation = None
        creation_work = None
        container_attempted = False
        container_id = None
        session = None
        protocol = None
        broker = None
        code = None
        text = ""
        work = None
        cancellation = None
        cancellation_clean = True
        stderr = None
        container = "chatpulse-acp-" + run.lease.run_id
        directory = tempfile.TemporaryDirectory(prefix="chatpulse-acp-")
        if cancelled is not None:
            async def wait_cancel():
                await cancelled
                self.cancel(run.lease)
            cancellation = asyncio.create_task(wait_cancel())
        try:
            root = Path(directory.name).resolve()
            (root / "home" / ".codex").mkdir(parents=True)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"),
                   "CODEX_HOME": str(root / "home" / ".codex"), "TMPDIR": str(root),
                   "LANG": "C.UTF-8"}
            if isinstance(self._launcher, HostFixture):
                env["CHATPULSE_SYNTHETIC_TOKEN"] = run.credential.secret
                command = self._launcher.command
                broker = _SyntheticMcp(self, run)
                await broker.start()
                mcp = [broker.descriptor()]
                cwd = str(root)
            else:
                if run.scope.evidence_ids:
                    raise RuntimeRejected(FailureCode.ISOLATION_BLOCKED)
                container_attempted = True
                creation = await asyncio.create_subprocess_exec(
                    *self._docker_command(container, create=True), cwd=str(root), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True)
                creation_work = asyncio.create_task(creation.communicate())
                while not creation_work.done():
                    if run.cancel_requested or not self._authorized(run):
                        raise RuntimeRejected(FailureCode.DEADLINE)
                    await asyncio.wait({creation_work}, timeout=0.01)
                output, _ = creation_work.result()
                if creation.returncode != 0 or not re.fullmatch(rb"[a-f0-9]{64}\n?", output):
                    raise RuntimeRejected(FailureCode.PROCESS)
                container_id = output.decode().strip()
                if run.cancel_requested or not self._authorized(run):
                    raise RuntimeRejected(FailureCode.DEADLINE)
                command = (self._launcher.executable, "--host", self._launcher.engine_endpoint,
                           "start", "--attach", "--interactive", container_id)
                mcp = []
                cwd = str(root)
            if run.cancel_requested or not self._authorized(run):
                raise RuntimeRejected(FailureCode.DEADLINE)
            process = await asyncio.create_subprocess_exec(
                *command, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, limit=self._limits.line_bytes + 1,
            )
            protocol = _Acp(process, run, self._limits, delivery, self,
                            "/tmp/chatpulse-fixture/workspace" if isinstance(self._launcher, DockerProbe) else cwd)
            stderr = asyncio.create_task(protocol.drain_stderr())
            work = asyncio.create_task(protocol.drive(prompt, mcp))
            while True:
                if run.cancel_requested:
                    break
                if delivery.failed.is_set():
                    code = FailureCode.CALLBACK
                    break
                if not self._authorized(run):
                    code = FailureCode.DEADLINE
                    break
                if stderr.done():
                    stderr.result()
                if work.done():
                    session, text = work.result()
                    break
                await asyncio.wait({work, stderr} if not stderr.done() else {work}, timeout=0.01,
                                   return_when=asyncio.FIRST_COMPLETED)
        except RuntimeRejected as exc:
            code = exc.code
        except (Exception, asyncio.CancelledError):
            code = FailureCode.PROCESS
        finally:
            with self._lock:
                run.revoked.set()
            delivery_clean = await delivery.close()
            for task in (work, cancellation):
                if task is not None:
                    task.cancel()
            if cancellation is not None:
                done, _ = await asyncio.wait({cancellation}, timeout=0.1)
                cancellation_clean = bool(done)
                if cancellation_clean:
                    try:
                        cancellation.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        code = code or FailureCode.PROCESS
            if broker is not None:
                await broker.close()
            if stderr is not None and not stderr.done():
                stderr.cancel()
            for task in (work, stderr):
                if task is not None:
                    try:
                        await task
                    except (Exception, asyncio.CancelledError):
                        pass
            if creation is not None and creation.returncode is None:
                try:
                    os.killpg(creation.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if creation_work is not None:
                try:
                    await asyncio.wait_for(creation_work, 0.1)
                except (Exception, asyncio.CancelledError):
                    pass
            cleanup = await self._cleanup(process, protocol, container_id or container,
                                          run.cancel_requested or code is not None,
                                          container_attempted, container_id is not None)
            if protocol is not None and protocol.exit_failed:
                code = code or FailureCode.PROCESS
            if not delivery_clean or not cancellation_clean:
                cleanup = CleanupReceipt(False, cleanup.containment)
            if delivery.failed.is_set():
                code = FailureCode.CALLBACK
            if stderr is not None:
                if stderr.done() and not stderr.cancelled():
                    try:
                        stderr.result()
                    except RuntimeRejected as exc:
                        code = exc.code
                    except Exception:
                        code = FailureCode.PROCESS
                else:
                    stderr.cancel()
            for task in (work, stderr):
                if task is not None:
                    try:
                        await task
                    except (Exception, asyncio.CancelledError):
                        pass
            directory.cleanup()
        with self._lock:
            if run.cancel_requested:
                outcome = Cancelled(run.lease.run_id, cleanup)
            elif code is not None or not cleanup.confirmed:
                outcome = Failed(run.lease.run_id, code or FailureCode.CLEANUP, cleanup)
            else:
                outcome = Completed(run.lease.run_id, session, text, cleanup)
            run.state = outcome.state
            self._runs.pop(run.lease.run_id, None)
        return outcome

    def _docker_command(self, container, *, create=False):
        launcher = self._launcher
        return (launcher.executable, "--host", launcher.engine_endpoint, "create" if create else "run",
                "--pull", "never", "--rm", "-i", "--name", container,
                "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=128m",
                "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt",
                "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
                "--cpus", "1", launcher.image_id, launcher.fixture_mode, launcher.canary_path)

    async def _cleanup(self, process, protocol, container, abort, container_attempted=False, container_known=False):
        containment = "host_fixture" if isinstance(self._launcher, HostFixture) else "docker"
        if process is None and not container_attempted:
            return CleanupReceipt(True, containment)
        deadline = time.monotonic() + 2.5
        if protocol is not None and protocol.session is not None and abort:
            try:
                await asyncio.wait_for(protocol.notify("session/cancel", {"sessionId": protocol.session}), 0.1)
            except (Exception, asyncio.CancelledError):
                pass
        if process is not None and process.stdin:
            process.stdin.close()
        async def discard(reader):
            while await reader.read(4096):
                pass
        drains = [asyncio.create_task(discard(reader)) for reader in (process.stdout, process.stderr)] if process else []
        await asyncio.sleep(0.05)
        if protocol is not None:
            protocol.exit_failed = process is not None and process.returncode is not None and process.returncode != 0
        controllers = []
        if containment == "docker":
            try:
                remover = await asyncio.create_subprocess_exec(
                    self._launcher.executable, "--host", self._launcher.engine_endpoint, "rm", "-f", container,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    env={"PATH": "/usr/bin:/bin"}, start_new_session=True)
                controllers.append(remover)
                await asyncio.wait_for(remover.wait(), max(0.01, deadline - time.monotonic()))
                confirmed = False
                while time.monotonic() < deadline:
                    check = await asyncio.create_subprocess_exec(
                        self._launcher.executable, "--host", self._launcher.engine_endpoint,
                        "container", "ls", "--all", "--filter", f"id={container}" if container_known else f"name=^{container}$",
                        "--format", "{{.ID}}", stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
                    controllers.append(check)
                    output, _ = await asyncio.wait_for(check.communicate(), max(0.01, deadline - time.monotonic()))
                    if check.returncode != 0:
                        break
                    if not output.strip():
                        confirmed = container_known
                        break
                    await asyncio.sleep(0.03)
            except (Exception, asyncio.CancelledError):
                confirmed = False
            finally:
                for controller in controllers:
                    if controller.returncode is None:
                        try:
                            controller.kill()
                            await asyncio.wait_for(controller.wait(), 0.1)
                        except (ProcessLookupError, asyncio.TimeoutError):
                            confirmed = False
        else:
            confirmed = True
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                confirmed = False
            try:
                await asyncio.wait_for(process.wait(), max(0.01, deadline - time.monotonic()))
            except (Exception, asyncio.CancelledError):
                confirmed = False
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        return CleanupReceipt(confirmed, containment)


class _Acp:
    def __init__(self, process, run, limits, delivery, runtime, cwd):
        self.cwd = cwd
        self.process = process
        self.run = run
        self.limits = limits
        self.delivery = delivery
        self.runtime = runtime
        self.session = None
        self.request_id = 0
        self.output_bytes = 0
        self.events = 0
        self.text_size = 0
        self.parts = []
        self.pending_text = ""
        self.failed_tool = False
        self.exit_failed = False

    async def send(self, message):
        data = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode() + b"\n"
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def notify(self, method, params):
        await self.send({"method": method, "params": params})

    async def drain_stderr(self):
        size = 0
        while data := await self.process.stderr.read(4096):
            size += len(data)
            if size > self.limits.stderr_bytes:
                raise RuntimeRejected(FailureCode.LIMIT)

    async def read(self):
        try:
            line = await self.process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            raise RuntimeRejected(FailureCode.LIMIT) from None
        if not line:
            raise RuntimeRejected(FailureCode.PROCESS)
        self.output_bytes += len(line)
        self.events += 1
        if (len(line) > self.limits.line_bytes or self.output_bytes > self.limits.stdout_bytes or
                self.events > self.limits.events):
            raise RuntimeRejected(FailureCode.LIMIT)
        try:
            def invalid_constant(value):
                raise ValueError("非法 JSON 常數")
            value = json.loads(line.decode("utf-8"), parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise RuntimeRejected(FailureCode.PROTOCOL) from None
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise RuntimeRejected(FailureCode.PROTOCOL)
        return value

    async def rpc(self, method, params):
        self.request_id += 1
        identifier = self.request_id
        await self.send({"id": identifier, "method": method, "params": params})
        while True:
            message = await self.read()
            if not self.runtime._authorized(self.run):
                raise RuntimeRejected(FailureCode.LEASE)
            if "method" in message:
                if not isinstance(message["method"], str) or not isinstance(message.get("params", {}), dict):
                    raise RuntimeRejected(FailureCode.PROTOCOL)
                if "id" in message:
                    await self.reject_request(message)
                elif message["method"] == "session/update":
                    self.update(message.get("params"))
                elif message["method"].startswith("session/"):
                    raise RuntimeRejected(FailureCode.PROTOCOL)
                continue
            if type(message.get("id")) is not int or message["id"] != identifier:
                raise RuntimeRejected(FailureCode.PROTOCOL)
            if ("result" in message) == ("error" in message):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            if "error" in message:
                error = message["error"]
                if not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str):
                    raise RuntimeRejected(FailureCode.PROTOCOL)
                raise RuntimeRejected(FailureCode.AGENT)
            if not isinstance(message["result"], dict):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            self.check_failure(message["result"])
            return message["result"]

    async def reject_request(self, message):
        identifier = message["id"]
        if type(identifier) not in (str, int):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        params = message.get("params", {})
        if params.get("sessionId") != self.session or self.session is None:
            raise RuntimeRejected(FailureCode.PROTOCOL)
        if message["method"] == "session/request_permission":
            if not isinstance(params.get("options"), list) or not isinstance(params.get("toolCall"), dict):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            await self.send({"id": identifier, "result": {"outcome": {"outcome": "cancelled"}}})
        else:
            await self.send({"id": identifier, "error": {"code": -32601, "message": "此執行未允許工具存取"}})

    def update(self, params):
        if not isinstance(params, dict) or self.session is None or params.get("sessionId") != self.session:
            raise RuntimeRejected(FailureCode.PROTOCOL)
        update = params.get("update")
        if not isinstance(update, dict) or not isinstance(update.get("sessionUpdate"), str):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        kind = update["sessionUpdate"]
        if kind in ("agent_message_chunk", "agent_thought_chunk", "user_message_chunk"):
            content = update.get("content")
            if not isinstance(content, dict) or content.get("type") != "text" or not isinstance(content.get("text"), str):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            chunk = content["text"]
            self.text_size += len(chunk)
            if self.text_size > self.limits.text_chars:
                raise RuntimeRejected(FailureCode.LIMIT)
            # 秘密可能跨 chunk 被回送，先暫存；完成前不發布未檢查文字。
            if kind == "agent_message_chunk":
                self.parts.append(chunk)
                self.pending_text += chunk
                self.pending_text = self.pending_text.replace(self.run.credential.secret, "[已遮蔽]")
                secret = self.run.credential.secret
                hold = 0
                for size in range(1, min(len(secret), len(self.pending_text) + 1)):
                    if self.pending_text.endswith(secret[:size]):
                        hold = size
                safe = self.pending_text[:-hold] if hold else self.pending_text
                self.pending_text = self.pending_text[-hold:] if hold else ""
                if safe:
                    self.delivery.publish(AgentEvent(self.run.lease.run_id, "text", safe))
        elif kind in ("tool_call", "tool_call_update"):
            if not isinstance(update.get("toolCallId"), str):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            status = update.get("status")
            if status is not None and status not in ("pending", "in_progress", "completed", "failed"):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            if status == "failed":
                self.failed_tool = True
        elif kind == "plan":
            entries = update.get("entries")
            if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) or not isinstance(entry.get("content"), str) or
                    entry.get("priority") not in ("high", "medium", "low") or
                    entry.get("status") not in ("pending", "in_progress", "completed") for entry in entries):
                raise RuntimeRejected(FailureCode.PROTOCOL)
        elif kind == "available_commands_update":
            commands = update.get("availableCommands")
            if not isinstance(commands, list) or any(
                    not isinstance(command, dict) or not isinstance(command.get("name"), str) or
                    not isinstance(command.get("description"), str) or
                    (command.get("input") is not None and (not isinstance(command["input"], dict) or
                     not isinstance(command["input"].get("hint"), str))) for command in commands):
                raise RuntimeRejected(FailureCode.PROTOCOL)
        elif kind == "current_mode_update":
            if not isinstance(update.get("currentModeId"), str):
                raise RuntimeRejected(FailureCode.PROTOCOL)
        elif kind == "config_option_update":
            self.validate_config_options(update.get("configOptions"))
        elif kind == "usage_update":
            if any(type(update.get(key)) is not int or update[key] < 0 for key in ("used", "size")):
                raise RuntimeRejected(FailureCode.PROTOCOL)
        elif kind == "session_info_update":
            self.check_failure(update)
        else:
            raise RuntimeRejected(FailureCode.PROTOCOL)

    def validate_config_options(self, options):
        if not isinstance(options, list):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        for option in options:
            if not isinstance(option, dict) or any(not isinstance(option.get(key), str) for key in ("id", "name")):
                raise RuntimeRejected(FailureCode.PROTOCOL)
            if option.get("type") == "boolean":
                if type(option.get("currentValue")) is not bool:
                    raise RuntimeRejected(FailureCode.PROTOCOL)
            elif option.get("type") == "select":
                if not isinstance(option.get("currentValue"), str) or not isinstance(option.get("options"), list):
                    raise RuntimeRejected(FailureCode.PROTOCOL)
                for choice in option["options"]:
                    if not isinstance(choice, dict):
                        raise RuntimeRejected(FailureCode.PROTOCOL)
                    if "group" in choice:
                        if (any(not isinstance(choice.get(key), str) for key in ("group", "name")) or
                                not isinstance(choice.get("options"), list)):
                            raise RuntimeRejected(FailureCode.PROTOCOL)
                        values = choice["options"]
                    else:
                        values = [choice]
                    if any(not isinstance(value, dict) or any(not isinstance(value.get(key), str)
                           for key in ("value", "name")) for value in values):
                        raise RuntimeRejected(FailureCode.PROTOCOL)
            else:
                raise RuntimeRejected(FailureCode.PROTOCOL)

    def check_failure(self, payload):
        meta = payload.get("_meta", {})
        if not isinstance(meta, dict):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        jetbrains = meta.get("jetbrains", {})
        if not isinstance(jetbrains, dict):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        air = jetbrains.get("air", {})
        if not isinstance(air, dict):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        if "sessionFailure" not in air:
            return
        failure = air["sessionFailure"]
        if type(air.get("version")) is not int or air["version"] != 1:
            raise RuntimeRejected(FailureCode.VERSION)
        if (not isinstance(failure, dict) or failure.get("severity") not in ("warning", "error") or
                not isinstance(failure.get("id"), str) or not failure["id"] or
                type(failure.get("revision")) is not int or failure["revision"] < 1 or
                not isinstance(failure.get("category"), str) or
                not isinstance(failure.get("title"), str) or not isinstance(failure.get("actions"), list)):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        if failure["severity"] == "error":
            raise RuntimeRejected(FailureCode.AGENT)

    async def drive(self, prompt, mcp):
        initialized = await self.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {"_meta": {"jetbrains": {"air": {"version": 1, "capabilities": ["sessionFailure"]}}}},
                                                   "clientInfo": {"name": "chatpulse-probe", "version": "1"}})
        if type(initialized.get("protocolVersion")) is not int or initialized["protocolVersion"] != 1:
            raise RuntimeRejected(FailureCode.VERSION)
        if not isinstance(initialized.get("agentCapabilities"), dict):
            raise RuntimeRejected(FailureCode.PROTOCOL)
        result = await self.rpc("session/new", {"cwd": self.cwd, "mcpServers": mcp})
        session = result.get("sessionId")
        if not isinstance(session, str) or not session or len(session) > 256:
            raise RuntimeRejected(FailureCode.PROTOCOL)
        if self.run.credential.secret in session:
            raise RuntimeRejected(FailureCode.PROTOCOL)
        if "configOptions" in result:
            self.validate_config_options(result["configOptions"])
        self.session = session
        result = await self.rpc("session/prompt", {"sessionId": session,
                                                  "prompt": [{"type": "text", "text": prompt}]})
        if result.get("stopReason") != "end_turn":
            raise RuntimeRejected(FailureCode.AGENT)
        if self.failed_tool:
            raise RuntimeRejected(FailureCode.AGENT)
        text = "".join(self.parts)
        if not text.strip():
            raise RuntimeRejected(FailureCode.EMPTY_RESULT)
        text = text.replace(self.run.credential.secret, "[已遮蔽]")
        if self.pending_text:
            self.delivery.publish(AgentEvent(self.run.lease.run_id, "text", self.pending_text))
        await self.delivery.finish()
        return self.session, text

class _SyntheticMcp:
    def __init__(self, runtime, run):
        self.runtime = runtime
        self.run = run
        self.path = "/" + uuid.uuid4().hex
        self.server = None
        self.tasks = set()
        self.calls = 0

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=16384)

    def descriptor(self):
        port = self.server.sockets[0].getsockname()[1]
        return {"type": "http", "name": "synthetic-evidence",
                "url": f"http://127.0.0.1:{port}{self.path}", "headers": []}

    async def close(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if len(self.tasks) > 4:
                return
            async with asyncio.timeout(0.5):
                head = await reader.readuntil(b"\r\n\r\n")
                if len(head) > 8192:
                    return
                lines = head.decode("ascii").split("\r\n")
                if lines[0] != f"POST {self.path} HTTP/1.1":
                    return
                headers = {}
                for line in lines[1:]:
                    if line:
                        key, value = line.split(":", 1)
                        if key.lower() in headers:
                            return
                        headers[key.lower()] = value.strip()
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 16384 or "transfer-encoding" in headers:
                    return
                payload = json.loads(await reader.readexactly(length))
                self.calls += 1
                if self.calls > self.runtime._limits.events:
                    self.runtime.cancel(self.run.lease)
                    return
                response = self.respond(payload)
                if response is None:
                    writer.write(b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                else:
                    data = json.dumps(response, ensure_ascii=False).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data)
                await writer.drain()
        except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()
            self.tasks.discard(task)

    def respond(self, payload):
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "請求格式錯誤"}}
        identifier = payload.get("id")
        method = payload.get("method")
        response = {"jsonrpc": "2.0", "id": identifier}
        with self.runtime._lock:
            if not self.runtime._authorized(self.run):
                return {**response, "error": {"code": -32001, "message": "能力已撤銷"}}
            if method == "notifications/initialized" and identifier is None:
                return None
            if type(identifier) not in (int, str):
                return {**response, "error": {"code": -32600, "message": "請求識別錯誤"}}
            params = payload.get("params", {})
            if not isinstance(params, dict):
                return {**response, "error": {"code": -32602, "message": "參數格式錯誤"}}
            if method == "initialize":
                if params.get("protocolVersion") not in ("2024-11-05", "2025-03-26", "2025-06-18"):
                    return {**response, "error": {"code": -32602, "message": "不支援此協定版本"}}
                result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "chatpulse-synthetic", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "read_evidence", "description": "讀取本次允許的合成證據",
                                     "inputSchema": {"type": "object", "properties": {"evidence_id": {"type": "string"}},
                                                     "required": ["evidence_id"], "additionalProperties": False}}]}
            elif method == "tools/call":
                params = payload.get("params", {})
                args = params.get("arguments", {})
                allowed = (params.get("name") == "read_evidence" and isinstance(args, dict) and
                           set(args) == {"evidence_id"} and isinstance(args.get("evidence_id"), str) and
                           args["evidence_id"] in self.run.scope.evidence_ids)
                result = {"isError": not allowed, "content": [{"type": "text", "text":
                          self.runtime._evidence[args["evidence_id"]] if allowed else "未允許此證據"}]}
            else:
                return {**response, "error": {"code": -32601, "message": "未允許此方法"}}
        return {**response, "result": result}
