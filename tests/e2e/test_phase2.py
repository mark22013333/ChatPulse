"""E2E Phase 2：Mention 收件匣、Draft Reply、Reference Space、送出流程、多 Viewer 隔離。

對應規格 SPECIFICATION.md 六、七節與十三節 Phase 2 驗收條件。

發訊息一律只發到暫存群組（e2e_lib.TEMP_SPACE），其他 Space 只讀。
"""

import os
import sys
import time

# 專案根＝tests/e2e 往上兩層。不寫死絕對路徑，同事 clone 到別的位置也要能跑
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, E2E_DIR)
sys.path.insert(0, os.path.dirname(os.path.dirname(E2E_DIR)))

from e2e_lib import (  # noqa: E402
    TEMP_SPACE,
    blocked,
    check,
    is_quota_error,
    client,
    err_shape,
    info,
    read_sse,
    section,
    send_to_temp,
    set_report,
    summary,
)

REPORT = os.environ.get(
    "E2E_REPORT",
    os.path.join(E2E_DIR, "reports", "e2e-phase2.md"),
)

MY_ID = "users/109827265019732088641"

# Reference Space 測試用：答案只存在於這個群組，且是猜不到的專案代號。
# 來源是該群組 2026-09-04 的真實對話（唯讀，不會被寫入）。
REF_SPACE = "spaces/AAQAja9MT6I"  # ILOOP2601 - 勞動部…
SECRET_FACT_PARTS = ["FIA01P2401", "SmartKMS"]


def main() -> int:
    set_report(REPORT)
    c = client()

    section("0. 登入")
    r = c.post("/api/v1/auth/bootstrap")
    check("登入成功", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return summary()
    viewer = r.json()["viewer"]
    check("自身 user id 正確", viewer["google_user_id"] == MY_ID, viewer["google_user_id"])

    # ------------------------------------------------------------------
    section("1. 確認參考群組真的含有那個「只存在於別處」的事實")
    r = c.get("/api/v1/messages", params={"space_id": REF_SPACE, "limit": 50})
    check("可讀取參考群組", r.status_code == 200, r.text[:160])
    ref_text = " ".join(m["text"] for m in r.json().get("messages", []))
    has_fact = all(p in ref_text for p in SECRET_FACT_PARTS)
    check(
        f"參考群組含關鍵事實 {SECRET_FACT_PARTS}",
        has_fact,
        f"命中 {[p for p in SECRET_FACT_PARTS if p in ref_text]}",
    )
    info(f"參考群組名稱：{r.json().get('space_name')}，可讀 {r.json().get('count')} 則")

    # 缺陷 D-7 的證據：發言者是否已不再全部是「未知成員」
    senders = {m["sender"] for m in r.json().get("messages", [])}
    check(
        "發言者不再全部是「未知成員」（D-7 名錄修復）",
        senders != {"未知成員"},
        f"共 {len(senders)} 種發言者標示：{sorted(senders)[:6]}",
    )

    # 確認被 @ 的那一側**沒有**這個事實，否則測不出 Reference Space 的作用。
    #
    # 檢查範圍必須對齊草稿產生器真正讀得到的東西：它讀「該 Mention 的討論串」
    # ＋「Viewer 勾選的 Reference Space」，**不讀**被 @ 那個 Space 的其他歷史訊息。
    # 因此前提是「討論串裡沒有答案」，而不是「整個暫存群組沒有答案」。
    # 討論串的內容就是下面第 2 節那句問題（第 5 節的 meta 會確認該串只有 1 則）。
    r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": 200})
    temp_text = " ".join(m["text"] for m in r.json().get("messages", []))
    incidental = [p for p in SECRET_FACT_PARTS if p in temp_text]
    if incidental:
        info(
            f"註：{incidental} 出現在暫存群組的其他歷史訊息中（含本測試前幾次執行留下的）。"
            "草稿產生器讀不到那些訊息，故不影響前提——真正的證據是第 5 節的負對照。"
        )

    # ------------------------------------------------------------------
    section("2. 製造一則真的 Mention 並由採集器抓到（6.1、6.3）")
    marker = time.strftime("%H%M%S")
    question = (
        f"<{MY_ID}> [E2E {marker}] 想請問 ILOOP2601 那個案子的 git 專案路徑是什麼？"
        "我要申請權限，需要完整的專案代號與 repo 名稱。"
    )
    check(
        "問題本身不含答案（前提：討論串裡沒有答案）",
        not any(p in question for p in SECRET_FACT_PARTS),
        "問題只問路徑，沒有提供路徑",
    )
    r = send_to_temp(c, question)
    check("已在暫存群組發出一則 @ 自己的訊息", r.status_code == 200, r.text[:160])
    posted = r.json()
    info(f"訊息 {posted.get('message_id')}")

    r = c.post("/api/v1/mentions/refresh")
    stats = r.json().get("stats") or {}
    check("POST /mentions/refresh 回 200", r.status_code == 200, r.text[:200])
    check(
        "採集器抓到新的 Mention",
        (stats.get("new_mentions") or 0) >= 1,
        f"new={stats.get('new_mentions')} matched={stats.get('total_matched')}",
    )
    check(
        "每輪只輪詢活躍子集，不是全部 436 個（實作 B 的槓桿）",
        0 < (stats.get("spaces_polled") or 0) < (stats.get("spaces_total") or 0),
        f"polled={stats.get('spaces_polled')} / total={stats.get('spaces_total')}",
    )
    check(
        "每輪 API 呼叫次數遠低於 Space 總數",
        (stats.get("api_calls") or 999) <= 40,
        f"api_calls={stats.get('api_calls')}，耗時 {stats.get('elapsed_seconds')}s",
    )
    info(f"採集統計：{stats}")

    r = c.get("/api/v1/mentions", params={"state": "pending"})
    body = r.json()
    check("GET /mentions?state=pending 回 200", r.status_code == 200)
    target = next((m for m in body["mentions"] if marker in (m.get("text") or "")), None)
    check("收件匣含剛才那則 Mention", target is not None, f"共 {body['count']} 筆")
    if target is None:
        return summary()
    check(
        "即時取回訊息內容（mentions 表只存識別資訊）",
        bool(target.get("text")) and target.get("content_error") is None,
        f"text 長度 {len(target.get('text') or '')}",
    )
    check("記錄了 thread_name", bool(target.get("thread_name")), target.get("thread_name"))
    check("狀態為待處理", target["state"] == "pending")
    check(
        "提問者顯示為看得懂的名字（非 users/…）",
        bool(target.get("sender_display")) and not target["sender_display"].startswith("users/"),
        f"sender_display={target.get('sender_display')!r}",
    )
    mention_id = target["id"]
    info(f"Mention id={mention_id}，Space={target.get('space_name')}")

    # ------------------------------------------------------------------
    section("3. 冪等性：重複採集不會產生第二筆")
    before = c.get("/api/v1/mentions").json()["count"]
    c.post("/api/v1/mentions/refresh")
    after = c.get("/api/v1/mentions").json()["count"]
    check("再跑一輪採集，總數不變", before == after, f"{before} -> {after}")

    # ------------------------------------------------------------------
    section("4. 狀態模型（6.4）：手動標記與退回")
    r = c.patch(f"/api/v1/mentions/{mention_id}", json={"state": "resolved"})
    check("PATCH 標記為 resolved", r.status_code == 200 and r.json()["state"] == "resolved", r.text[:160])
    check("resolved_at 已填", bool(r.json().get("resolved_at")), r.json().get("resolved_at"))
    counts = c.get("/api/v1/mentions").json()["counts"]
    check("計數反映已處理", counts.get("resolved", 0) >= 1, str(counts))

    r = c.patch(f"/api/v1/mentions/{mention_id}", json={"state": "pending"})
    check("可退回 pending", r.status_code == 200 and r.json()["state"] == "pending")
    check("退回後 resolved_at 清空", r.json().get("resolved_at") is None)

    r = c.patch(f"/api/v1/mentions/{mention_id}", json={"state": "done"})
    check(
        "非法狀態值回 400",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )
    r = c.patch("/api/v1/mentions/999999", json={"state": "resolved"})
    check(
        "不存在的 Mention 回 404 MENTION_NOT_FOUND",
        r.status_code == 404 and err_shape(r).get("code") == "MENTION_NOT_FOUND",
        f"{r.status_code} {err_shape(r).get('code')}",
    )

    # 冪等採集不會把已處理的改回待處理
    c.patch(f"/api/v1/mentions/{mention_id}", json={"state": "resolved"})
    c.post("/api/v1/mentions/refresh")
    st = c.get(f"/api/v1/mentions").json()
    again = next((m for m in st["mentions"] if m["id"] == mention_id), {})
    check(
        "再次採集不會把已處理的 Mention 改回待處理",
        again.get("state") == "resolved",
        f"state={again.get('state')}",
    )
    c.patch(f"/api/v1/mentions/{mention_id}", json={"state": "pending"})

    # ------------------------------------------------------------------
    section("5. Draft Reply — 不帶 Reference Space（負對照）")
    res_no_ref = read_sse(
        c, f"/api/v1/mentions/{mention_id}/draft/stream", {"reference_space_ids": [], "limit": 50}
    )
    check("串流回 200", res_no_ref["status"] == 200, str(res_no_ref["status"]))
    types = res_no_ref["types"]
    quota = is_quota_error(res_no_ref)
    if quota:
        info(
            "Gemini 回 429（配額用盡）。這驗證了 8.3 的錯誤事件路徑（HTTP 200、先 meta 再 error），"
            "但 Reference Space 的正負對照需要兩次真實呼叫才成立，改標為無法驗證。"
        )
        blocked("事件序為 meta → chunk… → done", "Gemini 配額用盡")
    else:
        check("事件序為 meta → chunk… → done", bool(types) and types[0] == "meta" and types[-1] == "done", str(types[:1] + types[-1:]))
    meta = res_no_ref["events"][0]
    check(
        "meta 的 reference_spaces 為空（7.3：不自動選擇）",
        meta.get("reference_spaces") == [],
        str(meta.get("reference_spaces")),
    )
    check("meta 帶 thread_message_count", "thread_message_count" in meta, str(meta.get("thread_message_count")))
    check(
        "討論串只有那一則提問（證實非參考群組的輸入裡沒有答案）",
        meta.get("thread_message_count") == 1,
        f"thread_message_count={meta.get('thread_message_count')}",
    )
    no_ref_text = res_no_ref["text"]
    if quota:
        blocked("有產出草稿內容", "Gemini 配額用盡")
        blocked("未帶參考群組時，答案不在草稿中（負對照成立）", "Gemini 配額用盡")
        blocked("草稿含兩個規定章節（脈絡分析 / 建議回話）", "Gemini 配額用盡")
    else:
        check("有產出草稿內容", len(no_ref_text) > 50, f"{len(no_ref_text)} 字")
        leaked = [p for p in SECRET_FACT_PARTS if p in no_ref_text]
        check(
            "未帶參考群組時，答案不在草稿中（負對照成立）",
            not leaked,
            f"洩漏 {leaked}" if leaked else "未出現關鍵事實",
        )
        check(
            "草稿含兩個規定章節（脈絡分析 / 建議回話）",
            "脈絡分析" in no_ref_text and "建議回話" in no_ref_text,
            f"脈絡分析={('脈絡分析' in no_ref_text)} 建議回話={('建議回話' in no_ref_text)}",
        )
        info(f"無參考群組草稿（前 150 字）：{no_ref_text[:150]!r}")

    # ------------------------------------------------------------------
    section("6. Draft Reply — 帶 Reference Space（正對照，7.1 的核心驗收）")
    res_ref = read_sse(
        c,
        f"/api/v1/mentions/{mention_id}/draft/stream",
        {"reference_space_ids": [REF_SPACE], "limit": 50},
    )
    check("串流回 200", res_ref["status"] == 200, str(res_ref["status"]))
    meta2 = res_ref["events"][0]
    check(
        "meta 列出所勾選的 Reference Space",
        len(meta2.get("reference_spaces") or []) == 1
        and meta2["reference_spaces"][0]["space_id"] == REF_SPACE,
        str(meta2.get("reference_spaces")),
    )
    ref_draft = res_ref["text"]
    draft_id = res_ref["events"][-1].get("draft_id")
    if quota or is_quota_error(res_ref):
        blocked(
            f"草稿引用了只存在於參考群組的事實 {SECRET_FACT_PARTS}",
            "Gemini 配額用盡——這是 7.1 的核心驗收條件，不可用任何替代品宣稱通過",
        )
        blocked("最後事件是 done 且帶 draft_id", "Gemini 配額用盡")
    else:
        hit = [p for p in SECRET_FACT_PARTS if p in ref_draft]
        check(
            f"草稿引用了只存在於參考群組的事實 {SECRET_FACT_PARTS}",
            len(hit) == len(SECRET_FACT_PARTS),
            f"命中 {hit}",
        )
        check("最後事件是 done 且帶 draft_id", res_ref["types"][-1] == "done" and draft_id, str(res_ref["events"][-1]))
        info(f"草稿長度 {len(ref_draft)} 字，draft_id={draft_id}")
        info(f"含事實的段落：{next((l for l in ref_draft.splitlines() if any(p in l for p in SECRET_FACT_PARTS)), '')[:160]!r}")

    # 參數驗證
    r = c.post(
        f"/api/v1/mentions/{mention_id}/draft/stream",
        json={"reference_space_ids": ["not-a-space"], "limit": 50},
    )
    check(
        "非法 reference_space_ids 回 400",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )
    r = c.post(
        f"/api/v1/mentions/{mention_id}/draft/stream",
        json={"reference_space_ids": [], "limit": 1001},
    )
    check(
        "draft 的 limit 也受 5.5 上限保護",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )
    r = c.post("/api/v1/mentions/999999/draft/stream", json={"reference_space_ids": []})
    check(
        "不存在的 Mention 回 404",
        r.status_code == 404 and err_shape(r).get("code") == "MENTION_NOT_FOUND",
        str(r.status_code),
    )

    # ------------------------------------------------------------------
    section("7. 送出回話（7.2 步驟 6）：回原討論串 + 自動標記已處理")
    # 刻意**不**把 SECRET_FACT_PARTS 寫進送出的內容：這則訊息會留在暫存群組，
    # 若含關鍵事實，下一次跑這個測試時第 1 節的前提檢查就會被自己汙染而失效。
    reply_text = (
        f"[E2E {marker}] 已查到 git 專案路徑，稍後私訊你完整連結，"
        "可以先從 ikm 找相關資訊申請權限。"
    )
    r = c.post(
        f"/api/v1/mentions/{mention_id}/reply",
        json={"text": reply_text, "draft_id": draft_id},
    )
    check("POST /reply 回 200", r.status_code == 200, r.text[:250])
    rep = r.json()
    check("回傳 message_id", bool(rep.get("message_id")), rep.get("message_id"))
    check(
        "落在原討論串",
        rep.get("thread_name") == target.get("thread_name"),
        f"送出 thread={rep.get('thread_name')} vs 原 thread={target.get('thread_name')}",
    )
    check(
        "該 Mention 自動標記為已處理",
        rep["mention"]["state"] == "resolved" and rep["mention"].get("resolved_at"),
        f"state={rep['mention']['state']} resolved_at={rep['mention'].get('resolved_at')}",
    )

    # 回讀 Google Chat 確認訊息真的在那個討論串、且發送者是本人
    r = c.get("/api/v1/messages", params={"space_id": TEMP_SPACE, "limit": 5})
    sent = next((m for m in r.json()["messages"] if reply_text[:20] in m["text"]), None)
    check("在 Google Chat 讀回剛送出的回話", sent is not None, str(bool(sent)))
    if sent:
        check(
            "訊息顯示為 Viewer 本人（ADR-0001，非 Bot）",
            sent.get("sender_id") == MY_ID,
            f"sender={sent.get('sender')} sender_id={sent.get('sender_id')}",
        )

    r = c.post(f"/api/v1/mentions/{mention_id}/reply", json={"text": "  "})
    check(
        "空白回話回 400",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )

    # ------------------------------------------------------------------
    section("8. 多 Viewer 隔離（4.3、ADR-0002）")
    # 沒有第二個 Google 帳號可用，因此直接在資料庫建立第二位 Viewer 與其 session，
    # 再用該 session 走 HTTP 層驗證隔離——驗的是授權邏輯，不是 Google 的權限。
    from core import db as _db
    from core import repository as _repo

    _db.init_db()
    other = _repo.upsert_viewer("users/999999999999999999999", "other@example.com", "測試用第二位 Viewer")
    other_token = _repo.create_session(other["id"])
    mine_summaries = c.get("/api/v1/summaries").json()
    mine_mentions = c.get("/api/v1/mentions", params={"with_content": "false"}).json()

    c2 = client()
    c2.cookies.set("chatpulse_session", other_token)
    r = c2.get("/api/v1/me")
    check("第二位 Viewer 可自行登入 session", r.status_code == 200, r.text[:160])
    check(
        "第二位 Viewer 的身分是他自己",
        r.json()["viewer"]["google_user_id"] == "users/999999999999999999999",
        r.json()["viewer"]["google_user_id"],
    )
    r = c2.get("/api/v1/summaries").json()
    check(
        "第二位 Viewer 看不到第一位的 Summary（ADR-0002）",
        r["count"] == 0 and mine_summaries["count"] > 0,
        f"對方 {r['count']} 筆 vs 我 {mine_summaries['count']} 筆",
    )
    r = c2.get("/api/v1/mentions", params={"with_content": "false"}).json()
    check(
        "第二位 Viewer 看不到第一位的 Mention",
        r["count"] == 0 and mine_mentions["count"] > 0,
        f"對方 {r['count']} 筆 vs 我 {mine_mentions['count']} 筆",
    )
    r = c2.get("/api/v1/spaces")
    check(
        "第二位 Viewer 沒有自己的憑證，讀不到任何 Space（授權由 Google 決定）",
        r.status_code == 401 and err_shape(r).get("code") == "NOT_AUTHENTICATED",
        f"{r.status_code} {err_shape(r).get('code')}",
    )
    r = c2.get(f"/api/v1/summaries")
    check("跨 Viewer 讀取不會漏出內容", "我的摘要" not in r.text and r.json()["count"] == 0)
    _db.execute("DELETE FROM viewers WHERE id = ?", (other["id"],))

    # ------------------------------------------------------------------
    section("9. 保留策略與名錄")
    from core import directory as _dir

    st = _dir.stats()
    check("名錄已累積使用者", st["known_users"] >= 1, f"{st['known_users']} 人")
    info(f"名錄內容：{_dir.load_all()}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
