import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
if mode == "docker_create_pending":
    if sys.argv[4] == "create":
        time.sleep(60)
    sys.exit(0)
if mode == "docker_exit":
    release = Path(sys.argv[2])
    action = sys.argv[5]
    if action == "create":
        print("a" * 64, flush=True)
        sys.exit(0)
    if action == "rm":
        release.touch()
        pid = int(release.with_suffix(".pid").read_text())
        deadline = time.monotonic() + 2
        while True:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=2)
            if status.returncode == 1 or status.stdout.strip().startswith("Z"):
                sys.exit(0)
            if status.returncode != 0 or time.monotonic() >= deadline:
                sys.exit(98)
    if action == "container":
        sys.exit(0)
    if action != "start":
        sys.exit(2)
    release.with_suffix(".pid").write_text(str(os.getpid()))
    mode = "release_exit"
    sys.argv = [sys.argv[0], mode, str(release), "7"]
session = os.environ["CHATPULSE_SYNTHETIC_TOKEN"] if mode == "session_secret" else "synthetic-session"
mcp = None


def send(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


def chunk(text, sid=session):
    send({"method": "session/update", "params": {"sessionId": sid,
          "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})


def call(method, params, identifier=1):
    body = json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}).encode()
    request = urllib.request.Request(mcp["url"], data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


for line in sys.stdin:
    message = json.loads(line)
    method = message["method"]
    identifier = message.get("id")
    if method == "initialize":
        send({"id": identifier, "result": {"protocolVersion": 2 if mode == "version" else 1,
                                            "agentCapabilities": {"mcpCapabilities": {"http": True}}}})
    elif method == "session/new":
        mcp = next(iter(message["params"]["mcpServers"]), None)
        send({"id": identifier, "result": {"sessionId": session}})
    elif method == "session/prompt":
        if mode == "eof":
            sys.exit(0)
        if mode == "error":
            send({"id": identifier, "error": {"code": -32603, "message": os.getenv("CHATPULSE_SYNTHETIC_TOKEN")}})
            continue
        if mode == "malformed":
            print("{", flush=True)
            continue
        if mode in ("nan", "infinity"):
            print('{"jsonrpc":"2.0","id":' + str(identifier) + ',"result":{"stopReason":"end_turn","bad":' + ("NaN" if mode == "nan" else "Infinity") + "}}", flush=True)
            continue
        if mode == "flood":
            print("x" * 200000, flush=True)
            continue
        if mode == "stderr":
            sys.stderr.write("x" * 400000)
            sys.stderr.flush()
            time.sleep(10)
        if mode == "unknown_flood":
            for _ in range(5000):
                send({"method": "telemetry/ping", "params": {}})
            continue
        if mode == "hang" or mode == "grandchild":
            if mode == "grandchild":
                child = subprocess.Popen([sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"])
                chunk(str(child.pid) + "\n")
            else:
                chunk("READY\n")
            time.sleep(60)
        failure = {"jetbrains": {"air": {"version": 1, "sessionFailure": {
            "id": "synthetic-failure", "revision": 1, "category": "provider_error",
            "severity": "warning" if mode == "typed_warning" else "error", "title": "合成診斷", "actions": []}}}}
        if mode in ("typed_update_failure", "typed_warning"):
            chunk("WARNING_TEXT\n") if mode == "typed_update_failure" else None
            send({"method": "session/update", "params": {"sessionId": session,
                  "update": {"sessionUpdate": "session_info_update", "_meta": failure}}})
        if mode == "core_updates":
            for update in (
                {"sessionUpdate": "plan", "entries": [{"content": "合成步驟", "priority": "high", "status": "pending"}]},
                {"sessionUpdate": "available_commands_update", "availableCommands": [{"name": "test", "description": "合成命令", "input": {"hint": "合成參數"}}]},
                {"sessionUpdate": "config_option_update", "configOptions": [
                    {"id": "flag", "name": "Flag", "type": "boolean", "currentValue": False},
                    {"id": "choice", "name": "Choice", "type": "select", "currentValue": "a", "options": [
                        {"group": "g", "name": "Group", "options": [{"value": "a", "name": "A"}]}]}]},
            ):
                send({"method": "session/update", "params": {"sessionId": session, "update": update}})
        if mode.startswith("bad_"):
            kind, key = {"bad_plan": ("plan", "entries"), "bad_commands": ("available_commands_update", "availableCommands"),
                         "bad_config": ("config_option_update", "configOptions")}[mode]
            send({"method": "session/update", "params": {"sessionId": session,
                  "update": {"sessionUpdate": kind, key: ["not-an-entry"]}}})
        if mode == "chunks":
            for index in range(12):
                chunk(f"chunk-{index}\n")
        elif mode == "mcp":
            initialized = call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}})
            listed = call("tools/list", {})
            yes = call("tools/call", {"name": "read_evidence", "arguments": {"evidence_id": "allowed"}})
            no = call("tools/call", {"name": "read_evidence", "arguments": {"evidence_id": "outside"}})
            chunk(json.dumps({"init": initialized["result"]["protocolVersion"], "tool": listed["result"]["tools"][0]["name"], "allowed": yes["result"], "denied": no["result"]}))
        elif mode == "env":
            chunk(json.dumps({"ado": os.getenv("ADO_PAT"), "openai": os.getenv("OPENAI_API_KEY"), "home": os.getenv("HOME"), "cwd": os.getcwd(), "codex_home": os.getenv("CODEX_HOME")}))
        elif mode == "secret":
            secret = os.environ["CHATPULSE_SYNTHETIC_TOKEN"]
            chunk(secret[:4])
            chunk(secret[4:])
        elif mode != "empty":
            chunk("SYNTHETIC_MARKER", "other-session" if mode == "wrong_session" else session)
        send({"id": identifier + 10 if mode == "wrong_id" else identifier, "result": {"stopReason": "end_turn", **({"_meta": failure} if mode == "typed_result_failure" else {})}})
        if mode == "late":
            chunk("LATE_SHOULD_NOT_APPEAR")
        if mode == "nonzero":
            sys.exit(7)
        if mode == "release_exit":
            release = Path(sys.argv[2])
            if len(sys.argv) > 4:
                sibling = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                release.with_suffix(".sibling").write_text(str(sibling.pid))
            deadline = time.monotonic() + 5
            while not release.exists():
                if time.monotonic() >= deadline:
                    sys.exit(99)
                time.sleep(0.005)
            sys.exit(int(sys.argv[3]))
        if mode == "linger":
            time.sleep(60)
