"""E2E：AI 供應商選擇。

對應規格 SPECIFICATION.md 3.2（AI）、十二節 R-4（Gemini 免費層每日 20 次）。

這一套刻意把「不需要真的呼叫 AI」與「需要真的呼叫」分開：前者可以無限重跑，
後者受配額限制。R-4 的教訓就是——把兩者混在一起，配額一滿整套就全紅，
分不出「功能壞了」和「額度用完了」。

實跑的部分固定用 `claude_cli`（吃本機 Claude Code 訂閱），不動 Gemini 的每日額度。
"""

import os
import sys

# 專案根＝tests/e2e 往上兩層。不寫死絕對路徑，同事 clone 到別的位置也要能跑
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(E2E_DIR)))
sys.path.insert(0, E2E_DIR)

from core import config as cfg  # noqa: E402
from core import providers  # noqa: E402
from core.errors import InvalidParameter  # noqa: E402
from core.providers.claude_cli import ClaudeCLIProvider  # noqa: E402
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
    os.path.join(E2E_DIR, "reports", "e2e-providers.md"),
)


def main() -> int:
    set_report(REPORT)

    # ------------------------------------------------------------------
    section("1. 註冊表與名稱解析（不呼叫 AI）")
    described = providers.describe_all()
    names = [d["name"] for d in described]
    check(
        "兩個供應商都在註冊表中",
        set(names) == {"gemini", "claude_cli"},
        str(names),
    )
    # 迴歸斷言：claude_api 供應商已於 2026-09-05 移除（見 SPECIFICATION.md 3.2）。
    # 它曾是合法值，所以要主動驗「現在不再是」——否則哪天有人把檔案加回來、
    # 或某份設定裡還留著這個名字，會安靜地變成「未知供應商」以外的行為。
    check(
        "claude_api 已不在註冊表中",
        "claude_api" not in names,
        str(names),
    )
    for d in described:
        info(f"{d['name']:11} available={d['available']} model={d['model']}")
        check(
            f"{d['name']} 有說得出下一步的狀態說明",
            bool(d["reason"]) and len(d["reason"]) > 5,
            d["reason"][:60],
        )

    for bad in ["gpt4", "openai", "", "Claude Code", "claude_api"]:
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
    section("2. 別名解析：claude／auto 應挑第一個現在可用的實作")
    available_map = {d["name"]: d["available"] for d in described}

    resolved = providers.resolve_name("claude")
    check(
        "別名 claude 解析為 claude_cli",
        resolved == "claude_cli",
        f"實際 {resolved}",
    )
    check(
        "別名 auto 解析為 claude_cli（它排在 gemini 前面）",
        providers.resolve_name("auto") == "claude_cli",
        f"實際 {providers.resolve_name('auto')}",
    )

    # 正對照：把 claude_cli 暫時弄成「不可用」，驗別名解析真的是「挑可用的」，
    # 而不是因為底下只剩一個實作就退化成寫死。
    # 這一組取代了 claude_api 移除前的「塞假 ANTHROPIC_API_KEY」對照。
    original_available = ClaudeCLIProvider.available
    ClaudeCLIProvider.available = lambda self: (  # type: ignore[assignment]
        False,
        "測試用：暫時假裝本機沒有 Claude Code",
    )
    try:
        if available_map.get("gemini"):
            check(
                "claude_cli 不可用時，別名 auto 往下退到 gemini（正對照）",
                providers.resolve_name("auto") == "gemini",
                f"實際 {providers.resolve_name('auto')}",
            )
        else:
            blocked("auto 退到 gemini 的正對照", "本機沒有設定 GOOGLE_API_KEY")
        try:
            providers.resolve("claude")
            check("claude_cli 不可用時，別名 claude 應拋錯", False, "沒有拋錯")
        except InvalidParameter as exc:
            check(
                "別名 claude 全不可用時，錯誤訊息說得出各實作的原因",
                "claude_cli" in exc.message and "測試用" in exc.message,
                exc.message[:80],
            )
    finally:
        ClaudeCLIProvider.available = original_available  # type: ignore[assignment]

    check(
        "還原後別名 claude 又解析回 claude_cli（環境有還原）",
        providers.resolve_name("claude") == resolved,
        f"實際 {providers.resolve_name('claude')}",
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
        "ai" in me and len(me["ai"].get("providers") or []) == 2,
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

    # ------------------------------------------------------------------
    section("7. 舊偏好指到已移除的供應商時要退回伺服器預設")
    # 這一節是 claude_api 移除的直接後果：PATCH 擋得住「新存入」非法值，
    # 但擋不住「兩週前就存在資料庫裡」的舊值。若不處理，那位 Viewer 的
    # 摘要與 Draft Reply 會一律回「未知的 AI 供應商」，而他看不出問題在偏好。
    # 走 in-process 呼叫而不是 HTTP：這樣不必為了測一個分支去寫資料庫。
    from dashboard.api import server as api_server  # noqa: E402

    original_get_prefs = api_server.repo.get_preferences
    api_server.repo.get_preferences = lambda vid: {"default_provider": "claude_api"}
    try:
        p = api_server.get_provider(viewer_id=1)
        check(
            "偏好存著已移除的 claude_api 時，退回伺服器預設而不是拋錯",
            p.name == providers.resolve_name(None),
            f"實際 {p.name}",
        )
    except InvalidParameter as exc:
        check(
            "偏好存著已移除的 claude_api 時，退回伺服器預設而不是拋錯",
            False,
            f"拋了 InvalidParameter：{exc.message[:60]}",
        )
    finally:
        api_server.repo.get_preferences = original_get_prefs

    # 負對照：合法的偏好仍然要被採用，別為了防呆把正常路徑也吃掉
    api_server.repo.get_preferences = lambda vid: {"default_provider": "gemini"}
    try:
        p = api_server.get_provider(viewer_id=1)
        check("合法偏好仍然生效（負對照）", p.name == "gemini", f"實際 {p.name}")
    finally:
        api_server.repo.get_preferences = original_get_prefs

    return summary()


if __name__ == "__main__":
    sys.exit(main())
