"""E2E Phase 1：認證、Space、訊息、參數統一、摘要串流、推播、Summary。

對應規格 SPECIFICATION.md 第五節與十三節 Phase 1 驗收條件。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e_lib import (  # noqa: E402
    TEMP_SPACE,
    blocked,
    check,
    client,
    err_shape,
    info,
    is_quota_error,
    read_sse,
    section,
    send_to_temp,
    set_report,
    summary,
)

REPORT = os.environ.get(
    "E2E_REPORT",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad/e2e-phase1.md",
)


def main() -> int:
    set_report(REPORT)
    c = client()

    # ------------------------------------------------------------------
    section("0. 服務健康與未登入行為")
    r = c.get("/api/v1/health")
    h = r.json()
    check("GET /health 回 200", r.status_code == 200, str(r.status_code))
    check("SQLite 為 WAL 模式（九節）", h.get("db") == "wal", h.get("db"))
    check("Gemini 已設定", h.get("gemini_configured") is True)
    check("採集器在跑", h.get("collector_running") is True, h.get("collector_implementation"))
    check(
        "採集器實作為 polling（R-1 實測結論）",
        h.get("collector_implementation") == "polling",
    )

    r = c.get("/api/v1/auth/status")
    st = r.json()
    check("未登入時 authenticated=false", st.get("authenticated") is False)
    check("偵測到既有憑證可匯入", st.get("can_bootstrap") is True)

    # 8.4：受保護端點未登入回 401 NOT_AUTHENTICATED
    for path in ["/api/v1/me", "/api/v1/spaces", "/api/v1/summaries", "/api/v1/mentions"]:
        r = c.get(path)
        e = err_shape(r)
        check(
            f"未登入 GET {path} 回 401 NOT_AUTHENTICATED",
            r.status_code == 401 and e.get("code") == "NOT_AUTHENTICATED",
            f"{r.status_code} {e.get('code')}",
        )

    # ------------------------------------------------------------------
    section("1. 登入（匯入既有憑證）")
    r = c.post("/api/v1/auth/bootstrap")
    check("POST /auth/bootstrap 回 200", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return summary()
    body = r.json()
    viewer = body.get("viewer", {})
    check("回傳 viewer 身分", bool(viewer.get("google_user_id")), viewer.get("google_user_id"))
    check(
        "session cookie 已設定",
        "chatpulse_session" in c.cookies,
        f"cookie keys={list(c.cookies.keys())}",
    )

    r = c.get("/api/v1/me")
    me = r.json()
    check("GET /me 回 200", r.status_code == 200)
    check(
        "回傳 scopes 清單",
        isinstance(me.get("scopes"), list) and len(me["scopes"]) >= 3,
        f"{len(me.get('scopes') or [])} 個",
    )
    check(
        "採集器資訊齊全",
        me.get("collector", {}).get("implementation") == "polling"
        and isinstance(me["collector"].get("interval_seconds"), int),
        str(me.get("collector", {}).get("interval_seconds")),
    )
    # 這位 Viewer 可能已被前幾輪測試改過偏好，對他斷言「預設值」會誤判。
    # 5.5 要驗的是「新 Viewer 拿到的預設」，所以另建一位臨時 Viewer 來看。
    sys.path.insert(0, "/Users/cheng/google-chat-bot")
    from core import db as _db
    from core import repository as _repo

    _db.init_db()
    _probe = _repo.upsert_viewer("users/000000000000000000001", None, "偏好預設值探針")
    _probe_pref = _repo.get_preferences(_probe["id"])
    check(
        "新 Viewer 的 preferences 預設值符合 5.5（limit=50、style=general）",
        _probe_pref["default_limit"] == 50 and _probe_pref["default_style"] == "general",
        str(_probe_pref),
    )
    _db.execute("DELETE FROM viewers WHERE id = ?", (_probe["id"],))
    info(f"目前這位 Viewer 的偏好（可能已被前幾輪改過）：{me['preferences']}")
    info(f"目前身分：{viewer.get('display_name')} / {viewer.get('google_user_id')}")

    # ------------------------------------------------------------------
    section("2. Space 查閱（5.1，缺陷 D-1）")
    r = c.get("/api/v1/spaces", params={"refresh": "true"})
    sp = r.json()
    check("GET /spaces?refresh=true 回 200", r.status_code == 200, r.text[:200])
    total = sp.get("total", 0)
    check(
        "涵蓋全部空間，超過 100 個（D-1 已修復的證據）",
        total > 100,
        f"total={total}",
    )
    info(f"實際取得 {total} 個 Space")
    check(
        "每個 Space 都有 lastActiveTime（採集器預篩所需）",
        all(s.get("lastActiveTime") for s in sp["spaces"]),
    )

    r2 = c.get("/api/v1/spaces")
    check("第二次呼叫命中 5 分鐘快取", r2.json().get("cached") is True, str(r2.json().get("cached")))

    r3 = c.get("/api/v1/spaces", params={"search": "暫存"})
    found = r3.json()
    check(
        "名稱模糊搜尋可用",
        found["count"] >= 1 and found["count"] < total,
        f"命中 {found['count']} / {total}",
    )
    check(
        "搜尋結果含暫存群組",
        any(s["id"] == TEMP_SPACE for s in found["spaces"]),
    )

    # ------------------------------------------------------------------
    section("3. 訊息列表（8.1：space_id 走 query string，不放 path）")
    r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": 5})
    msg = r.json()
    check("GET /messages 回 200", r.status_code == 200, r.text[:200])
    check("回傳 5 則以內", msg.get("count", 0) <= 5, f"count={msg.get('count')}")
    times = [m["time"] for m in msg["messages"]]
    check("訊息由舊到新排序", times == sorted(times), str(times[:3]))
    check(
        "抓到的是最近的訊息（不是最舊的）",
        bool(times) and times[-1] >= "2026-09",
        f"最後一則 {times[-1] if times else 'N/A'}",
    )
    info(f"最新一則：{times[-1] if times else 'N/A'}")

    # ------------------------------------------------------------------
    section("4. 參數統一（5.5）：limit 邊界在各入口行為一致")
    for bad, label in [(0, "0"), (1001, "1001"), ("abc", "非數值"), (-5, "-5")]:
        r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": bad})
        e = err_shape(r)
        check(
            f"GET /messages limit={label} 回 400 INVALID_PARAMETER",
            r.status_code == 400 and e.get("code") == "INVALID_PARAMETER",
            f"{r.status_code} {e.get('code')}: {e.get('message', '')[:60]}",
        )

    for bad, label in [(0, "0"), (1001, "1001"), ("abc", "非數值")]:
        r = c.post(
            "/api/v1/summarize/stream",
            json={"space_id": TEMP_SPACE, "limit": bad, "style": "general"},
        )
        e = err_shape(r)
        check(
            f"POST /summarize/stream limit={label} 回 400 INVALID_PARAMETER",
            r.status_code == 400 and e.get("code") == "INVALID_PARAMETER",
            f"{r.status_code} {e.get('code')}: {e.get('message', '')[:60]}",
        )

    for good in [1, 50, 1000]:
        r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": good})
        check(f"GET /messages limit={good} 可接受", r.status_code == 200, str(r.status_code))

    # style 驗證（D-4 的參數面）
    r = c.post(
        "/api/v1/summarize/stream",
        json={"space_id": TEMP_SPACE, "limit": 10, "style": "nonsense"},
    )
    e = err_shape(r)
    check(
        "非法 style 回 400 INVALID_PARAMETER",
        r.status_code == 400 and e.get("code") == "INVALID_PARAMETER",
        f"{r.status_code} {e.get('code')}",
    )

    # space_id 格式
    r = c.get("/api/v1/messages", params={"space_id": "AAAAxLxqJxY", "limit": 5})
    check(
        "space_id 缺 spaces/ 前綴回 400",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )

    # ------------------------------------------------------------------
    section("5. 摘要風格選項（D-4）")
    r = c.get("/api/v1/styles")
    styles = [s["value"] for s in r.json()["styles"]]
    check(
        "三種風格齊全",
        styles == ["general", "technical", "action_only"],
        str(styles),
    )

    # ------------------------------------------------------------------
    section("6. 單群摘要 SSE 串流（5.2、8.3）")
    res = read_sse(
        c,
        "/api/v1/summarize/stream",
        {"space_id": TEMP_SPACE, "limit": 50, "style": "general"},
    )
    check("串流回 HTTP 200", res["status"] == 200, str(res["status"]))
    quota_blocked = is_quota_error(res)
    if quota_blocked:
        info(
            "Gemini 回 429（配額用盡）。這本身驗證了 8.3／8.4 的錯誤事件路徑："
            "HTTP 仍是 200、先送 meta 再送 error 事件、連線未中斷；"
            "但依賴實際摘要內容的檢查改標為無法驗證。"
        )
    check(
        "標頭含 Cache-Control: no-cache",
        res["headers"].get("cache-control") == "no-cache",
        res["headers"].get("cache-control"),
    )
    check(
        "標頭含 X-Accel-Buffering: no",
        res["headers"].get("x-accel-buffering") == "no",
        res["headers"].get("x-accel-buffering"),
    )
    types = res["types"]
    check("第一個事件是 meta", bool(types) and types[0] == "meta", str(types[:1]))
    if quota_blocked:
        check(
            "配額用盡時仍照 8.3 送出 error 事件（code=GEMINI_QUOTA_EXCEEDED）",
            types[-1] == "error"
            and res["events"][-1].get("code") == "GEMINI_QUOTA_EXCEEDED",
            str(res["events"][-1]),
        )
        blocked("含多個 chunk 事件（逐字串流）", "Gemini 配額用盡")
        blocked("最後一個事件是 done", "Gemini 配額用盡")
    else:
        check("含多個 chunk 事件（逐字串流）", types.count("chunk") > 1, f"{types.count('chunk')} 個")
        check("最後一個事件是 done", bool(types) and types[-1] == "done", str(types[-1:]))
        check("無 error 事件", "error" not in types)
    meta = res["events"][0] if res["events"] else {}
    check(
        "meta 含 space / message_count / style",
        all(k in meta for k in ("space", "message_count", "style")),
        str({k: meta.get(k) for k in ("space", "message_count", "style")}),
    )
    done = res["events"][-1] if res["events"] else {}
    summary_id = done.get("summary_id")
    if quota_blocked:
        blocked("done 帶 summary_id（已寫入資料庫）", "Gemini 配額用盡")
    else:
        check("done 帶 summary_id（已寫入資料庫）", summary_id is not None, str(summary_id))
    general_text = res["text"]
    info(f"摘要長度 {len(general_text)} 字，chunk 數 {types.count('chunk')}")
    info(f"前 120 字：{general_text[:120]!r}")

    # ------------------------------------------------------------------
    section("7. 缺陷 D-4：切換摘要風格會產生不同結果")
    res_action = read_sse(
        c,
        "/api/v1/summarize/stream",
        {"space_id": TEMP_SPACE, "limit": 50, "style": "action_only"},
    )
    action_text = res_action["text"]
    res_tech = read_sse(
        c,
        "/api/v1/summarize/stream",
        {"space_id": TEMP_SPACE, "limit": 50, "style": "technical"},
    )
    tech_text = res_tech["text"]

    D4_LABELS = [
        "action_only 串流成功",
        "action_only 與 general 內容不同",
        "action_only 只輸出待辦章節（無「核心討論主題」）",
        "general 含核心討論主題章節",
        "technical 串流成功",
        "technical 與 general 內容不同",
        "technical 含技術專屬章節（已排除的假設／技術方案）",
    ]
    if quota_blocked or is_quota_error(res_action) or is_quota_error(res_tech):
        for label in D4_LABELS:
            blocked(label, "Gemini 配額用盡，三種風格的實際輸出無法取得")
        info(
            "D-4 需要三次真實的 Gemini 呼叫才驗得出「切換風格會產生不同結果」，"
            "配額用盡時不可用任何替代品宣稱通過。等配額恢復後單獨重跑本套件即可。"
        )
    else:
        check("action_only 串流成功", res_action["types"][-1] == "done", str(res_action["types"][-1:]))
        check(
            "action_only 與 general 內容不同",
            action_text.strip() != general_text.strip(),
            f"general {len(general_text)} 字 vs action_only {len(action_text)} 字",
        )
        check(
            "action_only 只輸出待辦章節（無「核心討論主題」）",
            "核心討論主題" not in action_text,
            f"含「核心討論主題」={'核心討論主題' in action_text}",
        )
        check(
            "general 含核心討論主題章節",
            "核心討論主題" in general_text,
        )
        check("technical 串流成功", res_tech["types"][-1] == "done")
        check(
            "technical 與 general 內容不同",
            tech_text.strip() != general_text.strip(),
            f"technical {len(tech_text)} 字",
        )
        check(
            "technical 含技術專屬章節（已排除的假設／技術方案）",
            ("已排除的假設" in tech_text) or ("技術方案" in tech_text) or ("技術問題" in tech_text),
        )
        info(f"三種風格長度：general={len(general_text)} technical={len(tech_text)} action_only={len(action_text)}")

    # ------------------------------------------------------------------
    section("8. 歷史 Summary（ADR-0002：只看得到自己的）")
    r = c.get("/api/v1/summaries")
    sums = r.json()
    check("GET /summaries 回 200", r.status_code == 200)
    if quota_blocked:
        blocked("剛才三次摘要都已入庫", "Gemini 配額用盡，本輪沒有新摘要產生")
        blocked("含剛才的 summary_id", "Gemini 配額用盡")
        info(f"資料庫既有 {sums['count']} 份歷史 Summary（前幾輪留下的），本輪未新增")
    else:
        check(
            "剛才三次摘要都已入庫",
            sums["count"] >= 3,
            f"count={sums['count']}",
        )
        ids = [s["id"] for s in sums["summaries"]]
        check("含剛才的 summary_id", summary_id in ids, f"{summary_id} in {ids[:5]}")
        styles_stored = {s["style"] for s in sums["summaries"][:3]}
        check(
            "資料庫記錄了不同 style",
            len(styles_stored) >= 2,
            str(styles_stored),
        )

    # ------------------------------------------------------------------
    section("9. 偏好設定往返（九節 preferences）")
    r = c.patch(
        "/api/v1/preferences",
        json={"default_limit": 120, "default_style": "technical", "pinned_space_ids": [TEMP_SPACE]},
    )
    pref = r.json()
    check("PATCH /preferences 回 200", r.status_code == 200, r.text[:200])
    check(
        "寫入值正確",
        pref["default_limit"] == 120
        and pref["default_style"] == "technical"
        and pref["pinned_space_ids"] == [TEMP_SPACE],
        str(pref),
    )
    r = c.get("/api/v1/me")
    check(
        "重新讀取仍是新值",
        r.json()["preferences"]["default_limit"] == 120,
        str(r.json()["preferences"]),
    )
    r = c.patch("/api/v1/preferences", json={"default_limit": 1001})
    check(
        "偏好的 limit 也受 5.5 上限保護",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )

    # ------------------------------------------------------------------
    section("10. 推播回 Google Chat（5.4）——只發到暫存群組")
    import time as _t

    marker = _t.strftime("%H%M%S")
    r = send_to_temp(c, f"[ChatPulse E2E {marker}] Phase 1 推播測試（以本人身分送出）")
    pub = r.json()
    check("POST /publish 回 200", r.status_code == 200, r.text[:200])
    check("回傳 message_id", bool(pub.get("message_id")), pub.get("message_id"))
    check("回傳 thread_name", bool(pub.get("thread_name")), pub.get("thread_name"))
    info(f"已送出：{pub.get('message_id')}")

    r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": 3})
    texts = [m["text"] for m in r.json()["messages"]]
    check(
        "讀回訊息列表可看到剛送出的內容",
        any(marker in t for t in texts),
        f"最後三則含 marker={any(marker in t for t in texts)}",
    )

    # 空內容應被擋
    r = c.post("/api/v1/publish", json={"space_id": TEMP_SPACE, "text": "   "})
    check(
        "空白訊息回 400",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )

    # ------------------------------------------------------------------
    section("11. R-2：token 用量已記錄")
    r = c.get("/api/v1/usage")
    usage = r.json()["usage"]
    check("GET /usage 回 200", r.status_code == 200)
    check("有用量記錄（R-2 的記錄機制可用）", len(usage) >= 1, f"{len(usage)} 列")
    if usage:
        u = usage[0]
        check(
            "記錄了 prompt / output / total tokens",
            u["prompt_tokens"] > 0 and u["output_tokens"] > 0 and u["total_tokens"] > 0,
            f"prompt={u['prompt_tokens']} output={u['output_tokens']} total={u['total_tokens']} calls={u['calls']}",
        )
        info(f"{u['day']} {u['model']}：{u['total_tokens']} tokens／{u['calls']} 次呼叫")

    # ------------------------------------------------------------------
    section("12. 登出後 session 失效")
    r = c.post("/api/v1/auth/logout")
    check("POST /auth/logout 回 200", r.status_code == 200)
    r = c.get("/api/v1/me")
    check(
        "登出後 /me 回 401",
        r.status_code == 401 and err_shape(r).get("code") == "NOT_AUTHENTICATED",
        str(r.status_code),
    )

    return summary()


if __name__ == "__main__":
    sys.exit(main())
