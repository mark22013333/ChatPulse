"""E2E 測試共用工具。

對**真實執行中的服務**與**真實 Google API** 發請求，不使用 mock。
發訊息的目標一律限制在暫存群組（TEMP_SPACE），這是本次任務的硬性限制。
"""

import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

BASE = os.environ.get("CHATPULSE_BASE", "http://127.0.0.1:8000")
# 唯一允許發送訊息的目標。任何其他 Space 只能讀。
TEMP_SPACE = "spaces/AAAAxLxqJxY"

_results: List[Dict[str, Any]] = []
_report_path: Optional[str] = None


def set_report(path: str) -> None:
    global _report_path
    _report_path = path
    with open(path, "w") as f:
        f.write(f"# E2E 測試報告\n\n開始時間：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")


def _append(line: str) -> None:
    if _report_path:
        with open(_report_path, "a") as f:
            f.write(line + "\n")


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    _append(f"\n## {title}\n")


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    icon = "✅" if ok else "❌"
    print(f"  {icon} [{mark}] {label}" + (f" — {detail}" if detail else ""))
    _append(f"- {icon} **{mark}** {label}" + (f" — {detail}" if detail else ""))
    _results.append({"label": label, "ok": ok, "detail": detail})
    return ok


def blocked(label: str, detail: str = "") -> None:
    """外部因素導致無法驗證（例如 Gemini 配額用盡）。

    刻意與 PASS／FAIL 分開計：把它算成通過是虛報，算成失敗又會讓真正的
    程式缺陷淹沒在噪音裡。summary() 會單獨列出，且 exit code 不是 0。
    """
    print(f"  ⏸️  [BLOCKED] {label}" + (f" — {detail}" if detail else ""))
    _append(f"- ⏸️ **BLOCKED（無法驗證）** {label}" + (f" — {detail}" if detail else ""))
    _results.append({"label": label, "ok": None, "detail": detail})


def is_quota_error(sse_result: Dict[str, Any]) -> bool:
    """判斷一次 SSE 呼叫是否因 Gemini 配額用盡而中止。"""
    for ev in sse_result.get("events") or []:
        if ev.get("type") == "error" and ev.get("code") == "GEMINI_QUOTA_EXCEEDED":
            return True
    return False


def info(text: str) -> None:
    print(f"      · {text}")
    _append(f"    - {text}")


def summary() -> int:
    total = len(_results)
    failed = [r for r in _results if r["ok"] is False]
    blocked_items = [r for r in _results if r["ok"] is None]
    passed = total - len(failed) - len(blocked_items)
    line = f"合計 {total} 項：通過 {passed}，失敗 {len(failed)}，無法驗證 {len(blocked_items)}"
    print(f"\n{'=' * 72}")
    print(line)
    _append(f"\n## 合計\n\n{line}\n")
    for r in failed:
        print(f"  ❌ {r['label']} — {r['detail']}")
        _append(f"- ❌ {r['label']} — {r['detail']}")
    for r in blocked_items:
        print(f"  ⏸️  {r['label']} — {r['detail']}")
        _append(f"- ⏸️ {r['label']} — {r['detail']}")
    if failed:
        return 1
    if blocked_items:
        return 2  # 與「全綠」區分開，不可被當成通過
    return 0


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=600.0, follow_redirects=False)


def err_shape(resp: httpx.Response) -> Dict[str, Any]:
    """取出 8.4 格式的錯誤物件；格式不符時回空 dict。"""
    try:
        body = resp.json()
    except Exception:
        return {}
    return body.get("error") or {}


def read_sse(
    c: httpx.Client,
    path: str,
    payload: Dict[str, Any],
    *,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """消費一個 SSE 端點，回傳事件統計。

    刻意用「累積 buffer 再按 \\n\\n 切」的方式解析，而不是逐行讀——
    這樣才會真的驗到 frame 跨 chunk 邊界的情況（前端也必須這樣做）。
    """
    events: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    raw_bytes = 0
    buffer = ""
    status = None
    headers: Dict[str, str] = {}

    with c.stream("POST", path, json=payload) as resp:
        status = resp.status_code
        headers = dict(resp.headers)
        if status != 200:
            body = resp.read().decode("utf-8", "replace")
            return {
                "status": status,
                "headers": headers,
                "events": [],
                "text": "",
                "raw_body": body,
            }
        for chunk in resp.iter_bytes():
            raw_bytes += len(chunk)
            buffer += chunk.decode("utf-8", "replace")
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                frame = frame.strip()
                if not frame.startswith("data: "):
                    continue
                try:
                    ev = json.loads(frame[6:])
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                if on_event:
                    on_event(ev)
                if ev.get("type") == "chunk":
                    text_parts.append(ev.get("text", ""))

    return {
        "status": status,
        "headers": headers,
        "events": events,
        "text": "".join(text_parts),
        "raw_bytes": raw_bytes,
        "types": [e.get("type") for e in events],
    }


def guard_space(space_id: str) -> None:
    """守門：確保任何發訊操作的目標只能是暫存群組。"""
    if space_id != TEMP_SPACE:
        raise AssertionError(
            f"E2E 測試只允許向 {TEMP_SPACE} 發送訊息，收到 {space_id!r}——已中止"
        )


def send_to_temp(c: httpx.Client, text: str, thread_name: Optional[str] = None) -> httpx.Response:
    guard_space(TEMP_SPACE)
    return c.post(
        "/api/v1/publish",
        json={"space_id": TEMP_SPACE, "text": text, "thread_name": thread_name},
    )
