"""E2E：AI 供應商選擇。

對應規格 SPECIFICATION.md 3.2（AI）、十二節 R-4（Gemini 免費層每日 20 次）。

這一套刻意把「不需要真的呼叫 AI」與「需要真的呼叫」分開：前者可以無限重跑，
後者受配額限制。R-4 的教訓就是——把兩者混在一起，配額一滿整套就全紅，
分不出「功能壞了」和「額度用完了」。

實跑的部分固定用 `claude_cli`（吃本機 Claude Code 訂閱），不動 Gemini 的每日額度。
"""

import os
import sys

sys.path.insert(0, "/Users/cheng/google-chat-bot")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as cfg  # noqa: E402
from core import providers  # noqa: E402
from core.errors import InvalidParameter  # noqa: E402
from e2e_lib import (  # noqa: E402
    TEMP_SPACE,
    blocked,
    check,
    client,
    err_shape,
    info,
    read_sse,
    section,
    set_report,
    summary,
)

REPORT = os.environ.get(
    "E2E_REPORT",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad/e2e-providers.md",
)


def main() -> int:
    set_report(REPORT)

    # ------------------------------------------------------------------
    section("1. 註冊表與名稱解析（不呼叫 AI）")
    described = providers.describe_all()
    names = [d["name"] for d in described]
    check(
        "三個供應商都在註冊表中",
        set(names) == {"gemini", "claude_api", "claude_cli"},
        str(names),
    )
    for d in described:
        info(f"{d['name']:11} available={d['available']} model={d['model']}")
        check(
            f"{d['name']} 有說得出下一步的狀態說明",
            bool(d["reason"]) and len(d["reason"]) > 5,
            d["reason"][:60],
        )

    for bad in ["gpt4", "openai", "", "Claude Code"]:
        try:
            providers.resolve(bad if bad else "nonexistent-xyz")
            check(f"非法供應商名稱 {bad!r} 應被拒", False, "沒有拋錯")
        except InvalidParameter as exc:
            check(
                f"非法供應商名稱 {bad!r} 回 INVALID_PARAMETER",
                "可用值" in exc.message,
                exc.message[:70],
            )

    # ------------------------------------------------------------------
    section("2. 別名解析：claude 應依憑證有無挑不同實作")
    available_map = {d["name"]: d["available"] for d in described}

    resolved = providers.resolve_name("claude")
    if available_map.get("claude_api"):
        expected = "claude_api"
    else:
        expected = "claude_cli"
    check(
        f"別名 claude 解析為 {expected}",
        resolved == expected,
        f"實際 {resolved}（claude_api 可用={available_map.get('claude_api')}）",
    )

    # 正對照：塞一把假金鑰進環境，別名應改選 API。
    # 只驗「選擇邏輯」，不會真的拿這把假金鑰去打 API。
    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-not-a-real-key-selection-test"
    try:
        with_key = providers.resolve_name("claude")
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved
    check(
        "有 ANTHROPIC_API_KEY 時別名 claude 改選 claude_api（正對照）",
        with_key == "claude_api",
        f"實際 {with_key}",
    )
    check(
        "移除金鑰後又退回原本的選擇（環境有還原）",
        providers.resolve_name("claude") == resolved,
    )

    # ------------------------------------------------------------------
    section("3. 實跑：claude_cli 產生文字並記錄用量")
    if not available_map.get("claude_cli"):
        blocked("claude_cli 實跑", "本機沒有可用的 Claude Code CLI")
    else:
        rec = []
        p = providers.resolve(
            "claude_cli", usage_recorder=lambda *a: rec.append(a)
        )
        text = p.generate("請只回覆這四個字：供應商可用", operation="provider_test")
        check("非串流產生文字", "供應商可用" in text, repr(text[:40]))
        check(
            "用量有記錄，且模型名是這個供應商自己回報的",
            bool(rec) and rec[0][1] == p.model,
            str(rec[:1]),
        )

        rec2 = []
        p2 = providers.resolve(
            "claude_cli", usage_recorder=lambda *a: rec2.append(a)
        )
        chunks = list(
            p2.stream_text("請用繁體中文寫兩句話說明什麼是快取。", operation="provider_test")
        )
        check("串流回傳多段文字", len(chunks) > 1, f"{len(chunks)} 段")
        check(
            "串流內容組得起來且是中文",
            len("".join(chunks)) > 10,
            repr("".join(chunks)[:50]),
        )
        check("串流結束後也有記錄用量", bool(rec2), str(rec2[:1]))

    # ------------------------------------------------------------------
    section("4. API：/api/v1/providers 與 /health")
    c = client()
    r = c.get("/api/v1/providers")
    body = r.json()
    check("GET /providers 回 200", r.status_code == 200, str(r.status_code))
    check("不需登入即可查詢", "providers" in body)
    check(
        "回傳的供應商清單與註冊表一致",
        {p["name"] for p in body["providers"]} == set(names),
        str([p["name"] for p in body["providers"]]),
    )
    check("回傳目前的預設值", body.get("default") == providers.default_name(), body.get("default"))

    h = c.get("/api/v1/health").json()
    check(
        "/health 同時回報預設與實際生效的供應商",
        "ai_provider_default" in h and "ai_provider_active" in h,
        f"default={h.get('ai_provider_default')} active={h.get('ai_provider_active')}",
    )

    # ------------------------------------------------------------------
    section("5. API：摘要端點的供應商選擇")
    r = c.post("/api/v1/auth/bootstrap")
    if r.status_code != 200:
        blocked("摘要端點的供應商選擇", f"無法登入（{r.status_code}）")
        return summary()

    # 非法值必須擋在進串流之前（與 limit／style 的處理一致）
    r = c.post(
        "/api/v1/summarize/stream",
        json={"space_id": TEMP_SPACE, "limit": 10, "provider": "gpt4"},
    )
    check(
        "非法 provider 回 400 INVALID_PARAMETER（不是串流中才失敗）",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        f"{r.status_code} {err_shape(r).get('code')}",
    )

    if not available_map.get("claude_cli"):
        blocked("指定 provider 跑摘要", "本機沒有可用的 Claude Code CLI")
    else:
        res = read_sse(
            c,
            "/api/v1/summarize/stream",
            {
                "space_id": TEMP_SPACE,
                "limit": 10,
                "style": "action_only",
                "provider": "claude_cli",
            },
        )
        check("串流回 200", res["status"] == 200, str(res["status"]))
        meta = res["events"][0] if res["events"] else {}
        check(
            "meta 回報實際使用的供應商與模型",
            meta.get("provider") == "claude_cli" and meta.get("model", "").startswith("claude-cli:"),
            f"provider={meta.get('provider')} model={meta.get('model')}",
        )
        check("有產出內容", len(res["text"]) > 20, f"{len(res['text'])} 字")
        check("最後事件是 done", res["types"][-1] == "done", str(res["types"][-1:]))
        info(f"用 claude_cli 產出 {len(res['text'])} 字，chunk 數 {res['types'].count('chunk')}")

        # 用量要記在這個供應商的模型名下，而不是記到 Gemini 頭上
        usage = c.get("/api/v1/usage").json()["usage"]
        models = {u["model"] for u in usage}
        check(
            "token_usage 記錄了 claude 的模型名",
            any(m.startswith("claude-cli:") for m in models),
            str(sorted(models)),
        )

    # ------------------------------------------------------------------
    section("6. 偏好：default_provider 往返")
    r = c.patch("/api/v1/preferences", json={"default_provider": "claude_cli"})
    check(
        "PATCH preferences 可存 default_provider",
        r.status_code == 200 and r.json().get("default_provider") == "claude_cli",
        r.text[:120],
    )
    me = c.get("/api/v1/me").json()
    check(
        "/me 讀得回 default_provider",
        me["preferences"].get("default_provider") == "claude_cli",
        str(me["preferences"].get("default_provider")),
    )
    check(
        "/me 帶 ai 供應商清單",
        "ai" in me and len(me["ai"].get("providers") or []) == 3,
        str(len((me.get("ai") or {}).get("providers") or [])),
    )
    r = c.patch("/api/v1/preferences", json={"default_provider": "gpt4"})
    check(
        "偏好存入非法供應商會被擋",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )
    # 還原，避免影響其他測試
    c.patch("/api/v1/preferences", json={"default_provider": ""})

    return summary()


if __name__ == "__main__":
    sys.exit(main())
