#!/usr/bin/env python3
"""預設只執行合成子程序；不讀取主機登入或呼叫真實服務。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile
import uuid
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.acp_runtime import AgentRuntime
from core.agent_runtime import (Cancelled, Completed, CredentialSnapshot, DockerProbe,
                                Failed, HostFixture, RuntimeRejected, SyntheticScope)


def item(status, detail, **extra):
    return {"status": status, "detail": detail, **extra}


def new_runtime(mode="normal", launcher=None, evidence=None):
    fixture = ROOT / "tests/fixtures/agent_protocol/child.py"
    runtime = AgentRuntime(launcher or HostFixture((sys.executable, str(fixture), mode)), evidence=evidence)
    runtime.replace_credentials("synthetic-viewer", CredentialSnapshot(
        "synthetic-viewer", "synthetic-subject", 1, time.time() + 120, "synthetic-only-secret"))
    return runtime


def endpoint(executable):
    # context 只用來解析可信 CLI 設定；實際執行及清理從此固定同一個 unix endpoint。
    result = subprocess.run([executable, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
                            capture_output=True, timeout=5, check=True)
    value = json.loads(result.stdout)
    if not isinstance(value, str) or not value.startswith("unix:///") or "\x00" in value:
        raise ValueError("需要本機 unix Docker 引擎")
    return value


async def probe(args):
    pins = json.loads((ROOT / "scripts/agent-runtime/package.json").read_text())["dependencies"]
    report = {"mode": "offline_synthetic", "versions": item("unverified", "鎖定版本尚未在容器內執行", pins=pins),
              "auth": item("pass", "僅驗證合成 CredentialSnapshot，未讀取主機登入"),
              "siwc": item("blocked", "尚無正式 SIWC registration、Viewer binding 與 token 更新證據"),
              "egress": item("blocked", "正式來源所需的受控網路出口尚未驗證")}
    app = new_runtime()
    lease = app.issue("synthetic-viewer", SyntheticScope())
    try:
        app.issue("another-viewer", SyntheticScope())
        report["binding"] = item("failed", "其他 Viewer 未遭拒絕")
    except RuntimeRejected:
        app.replace_credentials("synthetic-viewer", CredentialSnapshot(
            "synthetic-viewer", "synthetic-subject", 2, time.time() + 120, "synthetic-refreshed-secret"))
        try:
            app.run(lease, "合成提示")
            report["binding"] = item("failed", "舊 generation 未遭拒絕")
        except RuntimeRejected:
            report["binding"] = item("pass", "其他 Viewer 與舊 generation 在啟動程序前遭拒絕")
    result = await app.run(app.issue("synthetic-viewer", SyntheticScope()), "合成提示")
    report["inference"] = item("pass" if isinstance(result, Completed) and result.text == "SYNTHETIC_MARKER" and result.cleanup.confirmed else "failed",
                               "真 stdio 合成子程序；不是模型推論")
    app = new_runtime("hang")
    lease = app.issue("synthetic-viewer", SyntheticScope())
    task = asyncio.create_task(app.run(lease, "合成提示"))
    await asyncio.sleep(0.1)
    app.cancel(lease)
    result = await task
    report["cancel"] = item("pass" if isinstance(result, Cancelled) and result.cleanup.confirmed else "failed", "合成子程序撤銷與回收")
    app = new_runtime("mcp", evidence={"allowed": "合成證據", "outside": "不允許讀取"})
    result = await app.run(app.issue("synthetic-viewer", SyntheticScope(("allowed",))), "合成提示")
    valid = isinstance(result, Completed) and result.cleanup.confirmed
    if valid:
        data = json.loads(result.text)
        valid = data["allowed"]["isError"] is False and data["denied"]["isError"] is True
    report["mcp"] = item("pass" if valid else "failed", "host fixture 真 HTTP MCP wire 與 evidence ID 範圍拒絕；容器 MCP 尚未接通")
    report["isolation"] = item("unverified", "host fixture 不提供讀取隔離證據")
    if args.image_id:
        executable = args.docker_executable or shutil.which("docker")
        try:
            if not executable or not Path(executable).is_absolute():
                raise ValueError("找不到可信 Docker CLI")
            launcher = DockerProbe(executable, args.image_id, endpoint(executable))
            report["mode"] = "offline_pinned_container"
            app = new_runtime(launcher=launcher)
            result = await app.run(app.issue("synthetic-viewer", SyntheticScope(), timeout=25), "SYNTHETIC_INFERENCE")
            valid = isinstance(result, Completed) and result.text == "SYNTHETIC_PINNED_ADAPTER_OK" and result.cleanup.confirmed
            report["pinned_inference"] = item("pass" if valid else "failed", "固定 image、真 adapter、容器內合成 Responses", outcome=result.state.value,
                                            code=result.code.value if isinstance(result, Failed) else None, cleanup=result.cleanup.confirmed, image_id=args.image_id, synthetic_text=result.text if isinstance(result, Completed) else None)
            version_launcher = replace(launcher, fixture_mode="versions")
            version_app = new_runtime(launcher=version_launcher)
            version_name = "chatpulse-acp-versions-" + uuid.uuid4().hex
            try:
                version_result = subprocess.run(version_app._docker_command(version_name), capture_output=True,
                                                env={"PATH": "/usr/bin:/bin"}, timeout=8, check=True)
                versions = json.loads(version_result.stdout)
                matched = versions.get("adapter") == pins["@agentclientprotocol/codex-acp"] and versions.get("codex") == pins["@openai/codex"]
                report["versions"] = item("pass" if matched else "failed", "容器內實際套件版本與 lockfile pin 比對", actual=versions, pins=pins)
            finally:
                subprocess.run([executable, "--host", launcher.engine_endpoint, "rm", "-f", version_name],
                               env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=3)
            report["isolation"] = item("unverified", "使用 network none、唯讀、非 root、tmpfs 與資源限制；尚缺內建工具攻擊正反對照")
            app = new_runtime(launcher=replace(launcher, fixture_mode="cancel"))
            lease = app.issue("synthetic-viewer", SyntheticScope(), timeout=25)
            task = asyncio.create_task(app.run(lease, "SYNTHETIC_CANCEL"))
            await asyncio.sleep(1)
            start = time.monotonic()
            app.cancel(lease)
            result = await task
            elapsed = time.monotonic() - start
            report["pinned_cancel"] = item("pass" if isinstance(result, Cancelled) and result.cleanup.confirmed and elapsed < 3 else "failed", "固定 image 的取消與容器移除", cleanup=result.cleanup.confirmed, elapsed_seconds=round(elapsed, 3))
            app = new_runtime(launcher=replace(launcher, fixture_mode="http400"))
            result = await app.run(app.issue("synthetic-viewer", SyntheticScope(), timeout=25), "SYNTHETIC_HTTP_FAILURE")
            report["pinned_http_failure"] = item("pass" if isinstance(result, Failed) and result.cleanup.confirmed else "failed", "合成模型 HTTP 400 不得變成成功", outcome=result.state.value, synthetic_text=result.text if isinstance(result, Completed) else None)
            with tempfile.TemporaryDirectory(prefix="chatpulse-pr02-canary-") as directory:
                canary = Path(directory).resolve() / "secret.txt"
                canary.write_text("SYNTHETIC_HOST_SECRET")
                app = new_runtime(launcher=replace(launcher, fixture_mode="canary", canary_path=str(canary)))
                result = await app.run(app.issue("synthetic-viewer", SyntheticScope(), timeout=25), "SYNTHETIC_ISOLATION")
                expected = ("HOST_UNAVAILABLE", "SYMLINK_UNAVAILABLE", "ADO_PAT_ABSENT", "SOCKET_ABSENT", "CHATPULSE_INSIDE_CANARY")
                forbidden = ("HOST_READABLE", "SYMLINK_READABLE", "ADO_PAT_PRESENT", "SOCKET_PRESENT", "SYNTHETIC_HOST_SECRET")
                valid = (isinstance(result, Completed) and result.cleanup.confirmed and
                         all(marker in result.text for marker in expected) and
                         all(marker not in result.text for marker in forbidden) and
                         canary.read_text() == "SYNTHETIC_HOST_SECRET")
                report["isolation"] = item("pass" if valid else "failed", "同 launcher 真 adapter 內建 shell 工具讀取 canary、符號連結、環境與 socket 正反對照", image_id=args.image_id,
                                           outcome=result.state.value, cleanup=result.cleanup.confirmed,
                                           code=result.code.value if isinstance(result, Failed) else None,
                                           synthetic_text=result.text if isinstance(result, Completed) else None,
                                           checks={marker: isinstance(result, Completed) and marker in result.text for marker in expected})
            report["container_mcp"] = item("unverified", "network none 容器尚未接通 host MCP；不以 host wire 測試代替")
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeRejected) as error:
            report["pinned_inference"] = item("blocked", f"容器診斷無法啟動（{type(error).__name__}）")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", help="明確指定已建置的 sha256 image ID；仍只呼叫容器內假模型")
    parser.add_argument("--docker-executable", help="可信 Docker CLI 的絕對路徑")
    args = parser.parse_args()
    if os.name != "posix":
        report = {key: item("blocked", "此版本僅支援 POSIX 與本機 Unix Docker endpoint；Windows launcher 尚未提供")
                  for key in ("versions", "auth", "binding", "inference", "cancel", "isolation", "mcp", "siwc", "egress")}
        report["mode"] = "unsupported_platform"
    else:
        report = asyncio.run(probe(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(value.get("status") == "failed" for value in report.values() if isinstance(value, dict)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
