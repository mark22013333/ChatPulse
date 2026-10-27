import json
import os
from pathlib import Path
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
session_id = f"fixture-{os.getpid()}"
model = None
session_meta = None


def send(frame):
    print(json.dumps({"jsonrpc": "2.0", **frame}, ensure_ascii=False), flush=True)


def update(text, session=None, kind="agent_message_chunk"):
    send({"method": "session/update", "params": {"sessionId": session or session_id,
        "update": {"sessionUpdate": kind, "content": {"type": "text", "text": text}}}})


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        send({"id": request["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        session_meta = request["params"].get("_meta")
        send({"id": request["id"], "result": {"sessionId": session_id}})
    elif method == "session/set_model":
        model = request["params"]["modelId"]
        send({"id": request["id"], "result": {}})
    elif method == "session/prompt":
        if mode == "timeout":
            time.sleep(5)
        elif mode == "eof":
            break
        elif mode == "malformed":
            send({"method": "session/update", "params": "invalid"})
            send({"id": request["id"], "result": {"stopReason": "end_turn"}})
        elif mode == "error":
            update("partial")
            send({"id": request["id"], "error": {"code": -32000, "message": "SYNTHETIC_PRIVATE_ERROR"}})
        elif mode in ("wrongstop", "empty"):
            update(" " if mode == "empty" else "partial")
            send({"id": request["id"], "result": {"stopReason": "max_tokens" if mode == "wrongstop" else "end_turn"}})
        else:
            send({"id": "permission", "method": "session/request_permission", "params": {"sessionId": session_id}})
            permission = json.loads(next(sys.stdin))
            send({"id": "read", "method": "fs/read_text_file", "params": {"sessionId": session_id}})
            fs = json.loads(next(sys.stdin))
            update("FOREIGN", "foreign-session")
            update("THOUGHT", kind="agent_thought_chunk")
            update(json.dumps({"prompt": request["params"]["prompt"][0]["text"], "model": model,
                "cwd": str(Path.cwd()), "pid": os.getpid(), "session": session_id,
                "secret_env": "ADO_PAT" in os.environ or "OPENAI_API_KEY" in os.environ,
                "permission": permission["result"]["outcome"]["outcome"],
                "fs_error": fs["error"]["code"], "meta": session_meta}))
            if mode == "linger":
                time.sleep(5)
            send({"id": request["id"], "result": {"stopReason": "end_turn"}})
            if mode == "nonzero":
                sys.exit(7)
