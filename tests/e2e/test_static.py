"""E2E：前端靜態托管、SPA fallback、路徑穿越防護、錯誤格式。

對應規格 SPECIFICATION.md 3.2（建置產物交由 FastAPI 靜態托管）、8.4（錯誤規格）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e_lib import (  # noqa: E402
    blocked,
    check,
    client,
    err_shape,
    info,
    section,
    set_report,
    summary,
)

REPORT = os.environ.get(
    "E2E_REPORT",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad/e2e-static.md",
)

TRAVERSAL_PATHS = [
    "../../core/config.py",
    "..%2f..%2frequirements.txt",
    "assets/../../../etc/hosts",
    "../../config/google_chat_token.json",
    "../../.venv/pyvenv.cfg",
    "../../data/token.key",
]
LEAK_MARKERS = [
    "GEMINI_API_KEY",
    "refresh_token",
    "client_secret",
    "BASE_DIR",
    "home = ",
    "google-api-python-client==",
]


def main() -> int:
    set_report(REPORT)
    c = client()

    section("1. React 建置產物由 FastAPI 托管")
    r = c.get("/")
    check("GET / 回 200", r.status_code == 200, str(r.status_code))
    body = r.text
    check("送出的是 React 產物（引用 ./assets/ 下的 bundle）", "./assets/index-" in body, body[:80])
    check("不是 Vite 開發入口（不該引用 /src/main.tsx）", "/src/main.tsx" not in body)
    check("標題正確", "<title>" in body and "ChatPulse" in body)

    # 找出實際的 asset 檔名再取一次，這是「檔案服務真的通」的正對照
    import re

    assets = re.findall(r"\./assets/([\w.\-]+)", body)
    check("index.html 有列出 asset", bool(assets), str(assets[:3]))
    served = 0
    for name in assets[:4]:
        ra = c.get(f"/assets/{name}")
        if ra.status_code == 200 and len(ra.content) > 100:
            served += 1
    check(
        "asset 檔案可正常取回（正對照：證明檔案服務路徑是通的）",
        served == len(assets[:4]),
        f"{served}/{len(assets[:4])} 個可取回",
    )

    section("2. SPA fallback：深層路徑落回 index.html")
    for path in ["/mentions", "/mentions/7", "/summary/abc"]:
        r = c.get(path)
        check(
            f"GET {path} 落回 index.html",
            r.status_code == 200 and "./assets/index-" in r.text,
            str(r.status_code),
        )

    section("3. 未知 API 路徑走 8.4 錯誤格式，不落回 index.html")
    r = c.get("/api/v1/does-not-exist")
    e = err_shape(r)
    check(
        "未知 API 回 404 ROUTE_NOT_FOUND，且不落回 index.html",
        r.status_code == 404
        and e.get("code") == "ROUTE_NOT_FOUND"
        and "html" not in r.text.lower(),
        f"{r.status_code} {e.get('code')}",
    )

    section("4. 路徑穿越防護")
    leaked = []
    for path in TRAVERSAL_PATHS:
        r = c.get(f"/{path}")
        hit = [m for m in LEAK_MARKERS if m in r.text]
        if hit:
            leaked.append((path, hit))
        check(
            f"/{path} 未洩漏檔案內容",
            not hit,
            f"洩漏 {hit}" if hit else f"回 {r.status_code}、{len(r.content)} bytes（SPA index.html）",
        )
    if not leaked:
        info("全部穿越嘗試都落回 index.html，且正對照已證明檔案服務本身可用")

    authz_regressions()

    return summary()



def authz_regressions() -> None:
    """獨立審查找出的授權面問題，固化成迴歸測試。

    這四項都不在規格書原本的驗收條件裡——測試全綠也抓不到，是 2026-09-05
    的獨立審查補上的。放在這裡是為了它們不會再退回去。
    """
    import stat as _stat
    import sys as _sys

    _sys.path.insert(0, "/Users/cheng/google-chat-bot")
    from core import config as _cfg
    from core import crypto as _crypto
    from core import db as _db
    from core import repository as _repo

    c = client()
    r = c.post("/api/v1/auth/bootstrap")
    if r.status_code != 200:
        blocked("授權迴歸測試", f"無法登入（{r.status_code}）")
        return

    section("5. 授權迴歸（獨立審查發現）")

    # (1) /summaries 的 limit 上限要與 5.5 共用常數一致，不可硬寫 500
    check(
        "GET /summaries?limit=1000 可接受（先前硬寫 le=500）",
        c.get("/api/v1/summaries", params={"limit": 1000}).status_code == 200,
    )
    r = c.get("/api/v1/summaries", params={"limit": 1001})
    check(
        "GET /summaries?limit=1001 回 400 INVALID_PARAMETER",
        r.status_code == 400 and err_shape(r).get("code") == "INVALID_PARAMETER",
        str(r.status_code),
    )

    # (2) /usage 只能回本人用量
    _db.init_db()
    other = _repo.upsert_viewer("users/777777777777777777777", "u@x.z", "用量隔離測試")
    _repo.record_token_usage(other["id"], "test-model", "summarize", 111, 222, 333)
    usage = c.get("/api/v1/usage").json()["usage"]
    check(
        "GET /usage 不含其他 Viewer 的用量（ADR-0002 同標準）",
        all(u["model"] != "test-model" for u in usage),
        f"回傳 {len(usage)} 列，models={sorted({u['model'] for u in usage})}",
    )

    # (3) 草稿的 sent_at 不可被其他 Viewer 標記
    _repo.upsert_mention(
        other["id"],
        {
            "space_id": "spaces/REGRESSION",
            "message_name": "spaces/REGRESSION/messages/authz",
            "create_time": "2026-09-05T00:00:00Z",
        },
    )
    other_mention = _repo.list_mentions(other["id"])[0]
    other_draft = _repo.create_draft(other_mention["id"], "別人的草稿")
    me = c.get("/api/v1/me").json()["viewer"]
    check(
        "其他 Viewer 的 draft_id 無法被標記為已送出",
        _repo.mark_draft_sent(me["id"], other_draft) is False,
    )
    check(
        "草稿擁有者本人可以標記",
        _repo.mark_draft_sent(other["id"], other_draft) is True,
    )
    _db.execute("DELETE FROM viewers WHERE id = ?", (other["id"],))

    # (4) 憑證檔與資料檔權限不可放寬
    _crypto.harden_credential_files()
    for path in (_cfg.LEGACY_TOKEN_FILE, _cfg.CLIENT_SECRET_FILE, _cfg.ENCRYPTION_KEY_FILE):
        import os as _os

        if not _os.path.exists(path):
            continue
        mode = _stat.S_IMODE(_os.stat(path).st_mode)
        check(
            f"{_os.path.basename(path)} 權限不對 group／other 開放",
            not (mode & 0o077),
            f"實際 {oct(mode)}（{_stat.filemode(_os.stat(path).st_mode)}）",
        )
    info(
        "舊的單人 token 檔仍是明文（供 bootstrap 匯入），所以權限是唯一的保護；"
        "google-auth 用預設 umask 寫檔會是 0644，因此每次啟動都會重新收斂一次。"
    )

if __name__ == "__main__":
    sys.exit(main())
