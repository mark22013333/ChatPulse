"""ChatPulse 儀表板後端（FastAPI）。

規格對應：SPECIFICATION.md 八節（API 規格）、九節（資料模型）、六／七節（Phase 2）。
API 契約的單一事實來源是 docs/api-contract.md。

三個與 v1 實作的結構差異：
  1. **space_id 一律不放 path**（8.1）。GET 走 query string，POST 走 body——
     space id 內含斜線，放 path 只能靠 `{space_id:path}` 繞過，那是 v1 造成
     「同一份 API 三種風格」的根因。
  2. **錯誤統一**（8.4）。ChatPulseError 由 exception handler 轉成
     `{"error":{"code":...,"message":...}}`，串流中則轉成 error 事件。
  3. **多 Viewer**（4.3）。每個請求先由 session cookie 解析出 Viewer，
     Google Chat 憑證取自該 Viewer 的 credentials 列，不再是單一全域 client。
"""

import concurrent.futures as futures
import dataclasses
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from pydantic import BaseModel, Field, field_validator

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import config as cfg
from core import (
    attachments,
    code_search,
    crypto,
    db,
    directory,
    draft_context,
    identity,
    persona_sources,
    personas,
    polishers,
    prompts,
    providers,
    reply_profiles,
)
from core import repository as repo
from core.chat_client import (
    GoogleChatClient,
    attachment_note,
    format_conversation,
    validate_limit,
)
from core.errors import (
    ChatPulseError,
    CodeProjectNotFound,
    ConfigurationError,
    DraftNotFound,
    InvalidParameter,
    MentionNotFound,
    NotAuthenticated,
    PersonaInvalid,
    PersonaNotFound,
    ReplyPromptNotFound,
    RouteNotFound,
    SepiaUnavailable,
)
from core.mentions import CollectorRunner
from core.polishers import sepia as sepia_polisher

logging.basicConfig(
    level=os.environ.get("CHATPULSE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("chatpulse.api")

FRONTEND_DIR = os.path.join(BASE_DIR, "dashboard", "frontend")
FRONTEND_DIST = os.path.join(FRONTEND_DIR, "dist")


# ==========================================================================
# 每個 Viewer 的 Chat client 與 Space 快取
# ==========================================================================

_client_lock = threading.Lock()
_clients: Dict[int, GoogleChatClient] = {}
# 5 分鐘 Space 快取（2.2）。行程內字典，重啟即失效——刻意不落地，
# 因為它只是省一次 API 呼叫，不值得引入失效邏輯的複雜度。
_spaces_cache: Dict[int, Dict[str, Any]] = {}
SPACES_CACHE_TTL = 300
# 收件匣一次載入的筆數。與訊息抓取的 LIMIT_DEFAULT 是不同的東西（那個是
# 「一個 Space 抓幾則對話」，這個是「收件匣列幾筆 Mention」），所以另立常數，
# 但上下限仍沿用 cfg.LIMIT_MIN／LIMIT_MAX。
MENTIONS_PAGE_DEFAULT = 200


def _persist_refreshed(viewer_id: int):
    def _cb(creds: Credentials) -> None:
        try:
            repo.save_credentials(
                viewer_id,
                creds.to_json(),
                creds.expiry.isoformat() if creds.expiry else None,
                list(creds.scopes or []),
            )
        except Exception:
            log.exception("Viewer %s 的刷新後 token 存回失敗", viewer_id)

    return _cb


def get_client(viewer_id: int) -> GoogleChatClient:
    """取得（並快取）某位 Viewer 的 Chat client。"""
    with _client_lock:
        client = _clients.get(viewer_id)
        if client is not None:
            return client

    token_json = repo.load_credentials_json(viewer_id)
    if not token_json:
        raise NotAuthenticated("找不到你的 Google 憑證，請重新登入")

    info = json.loads(token_json)
    creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))
    client = GoogleChatClient(
        creds, on_token_refresh=_persist_refreshed(viewer_id), interactive=False
    )
    with _client_lock:
        _clients[viewer_id] = client
    return client


def invalidate_client(viewer_id: int) -> None:
    with _client_lock:
        _clients.pop(viewer_id, None)
        _spaces_cache.pop(viewer_id, None)


def _client_factory_for_collector(viewer_row: Dict[str, Any]) -> Optional[GoogleChatClient]:
    try:
        return get_client(viewer_row["id"])
    except NotAuthenticated:
        return None


def get_provider(
    viewer_id: Optional[int] = None, name: Optional[str] = None
) -> providers.AIProvider:
    """取得一個 AI 供應商實例，並把用量記到該 Viewer 名下。

    name 為 None 時依序取：Viewer 的偏好 → CHATPULSE_AI_PROVIDER → "claude"。
    模型名由供應商自己回報，不由這裡猜——換供應商後用量表才不會記到錯的模型上。
    """
    if name is None and viewer_id is not None:
        try:
            saved = repo.get_preferences(viewer_id).get("default_provider") or None
            # 偏好是使用者以前存下的，而供應商清單會變（例：claude_api 於
            # 2026-09-05 移除）。存過的舊值若已不合法，當作沒設、退回伺服器預設，
            # 不要原樣丟進 resolve()——那會讓摘要與 Draft Reply 一律回
            # 「未知的 AI 供應商」，而使用者看不出問題出在自己幾週前的偏好上。
            if saved and saved not in providers.VALID_NAMES:
                log.warning(
                    "Viewer %s 的 default_provider 偏好 %r 已不是合法供應商，改用伺服器預設",
                    viewer_id,
                    saved,
                )
                saved = None
            name = saved
        except Exception:
            name = None

    def _recorder(
        operation: str, model: str, prompt_t: int, output_t: int, total_t: int
    ) -> None:
        try:
            repo.record_token_usage(
                viewer_id, model, operation, prompt_t, output_t, total_t
            )
        except Exception:
            log.exception("token 用量記錄失敗")

    return providers.resolve(name, usage_recorder=_recorder)


# 背景補齊私訊對象時，同一個 viewer 不要同時跑兩份
_dm_fill_running: set = set()
_dm_fill_lock = threading.Lock()

#: 每輪背景解析的上限。使用者可能有上百個私訊，一次全解會撞配額也拖很久；
#: 按最後活動排序取前面這些，多刷新幾次就補完了。
DM_FILL_BATCH = 25


def _fill_dm_peers_bg(viewer_id: int, spaces: List[Dict[str, Any]]) -> None:
    """背景把還不認得的私訊對方補齊，不阻塞 /api/v1/spaces 的回應。

    為什麼要背景做：私訊的對方是誰，只能靠讀一則訊息看 sender 再查名錄
    （Google Chat 對 DIRECT_MESSAGE 不回 displayName，而 members.list 需要
    我們沒有的 scope）。使用者有上百個私訊，同步解析要幾十秒，清單會卡住。

    db 模組是 thread-local 連線（core/db.py），所以背景寫入安全。
    """
    with _dm_fill_lock:
        if viewer_id in _dm_fill_running:
            return
        _dm_fill_running.add(viewer_id)
    try:
        known = directory.load_dm_peers()
        todo = [
            s for s in spaces
            if s["type"] == "DIRECT_MESSAGE" and not known.get(s["id"])
        ]
        if not todo:
            return
        # 最近活動的排前面——那些是使用者最可能在清單上看到的
        todo.sort(key=lambda s: s.get("lastActiveTime") or "", reverse=True)
        todo = todo[:DM_FILL_BATCH]

        self_id = (repo.get_viewer(viewer_id) or {}).get("google_user_id")
        client = get_client(viewer_id)
        names = directory.load_all()
        linked = named = 0
        for s in todo:
            try:
                msgs = client.fetch_recent_messages(s["id"], limit=3)
                peer_id = directory.peer_id_from_messages(msgs, self_id)
                if not peer_id:
                    continue
                # 建立對照本身就有價值：即使現在叫不出名字，使用者之後手動
                # 命名時就不必再讀一次訊息，而且名字會掛在這個人身上。
                directory.link_dm_peer(s["id"], peer_id)
                linked += 1
                if names.get(peer_id):
                    named += 1
            except Exception:
                continue  # 單一 space 失敗不影響其他
        if linked:
            log.info(
                "背景辨識私訊對象：這輪 %d/%d 個找到對方，其中 %d 個叫得出名字",
                linked, len(todo), named,
            )
    except Exception:
        log.exception("背景辨識私訊對象失敗（不影響清單）")
    finally:
        with _dm_fill_lock:
            _dm_fill_running.discard(viewer_id)


def list_spaces_cached(viewer_id: int, refresh: bool = False) -> Dict[str, Any]:
    entry = _spaces_cache.get(viewer_id)
    now = time.time()
    if refresh or not entry or (now - entry["timestamp"] > SPACES_CACHE_TTL):
        raw = get_client(viewer_id).list_spaces()
        # 私訊沒有 displayName（Google Chat 對 DIRECT_MESSAGE 不回傳），
        # 但我們可能已經從讀過的訊息認出對方是誰，那份對照就在名錄裡。
        # 兩層查詢：space -> 對方是誰（user id）-> 那個人叫什麼。
        # 名字掛在人身上，所以使用者取的名字在摘要、草稿裡也一體適用，
        # 而且就算 Google 換掉 space 的資源名稱，重建對照即可、名字不會白費。
        dm_links = directory.load_dm_links()
        person_names = directory.load_all()
        manual_named = directory.manual_named_users()
        legacy_peers = directory.load_dm_peers()  # 舊格式（space -> 名字），相容用
        formatted = []
        for s in raw:
            member = s.get("membershipCount") or {}
            space_id = s.get("name")
            display = s.get("displayName")
            renamable = not display  # 只有沒有官方名稱的空間才給改名
            peer_id = dm_links.get(space_id)
            if not display:
                # 認得出對方就顯示名字；認不出來寧可寫「私訊」也不要寫
                # 「成員…8641」那種代號——清單上放代號比不放還難懂
                name = person_names.get(peer_id) if peer_id else None
                display = name or legacy_peers.get(space_id)
                if not display:
                    display = (
                        "（私訊）" if s.get("spaceType") == "DIRECT_MESSAGE" else "（未命名空間）"
                    )
            formatted.append(
                {
                    "id": space_id,
                    "displayName": display,
                    "type": s.get("spaceType", "UNKNOWN"),
                    # 草稿的脈絡形狀靠這個欄位決定（core/draft_context.py）。
                    # 從快取拿是零成本——這份清單本來就會被 space_display_name 走一次。
                    "threadingState": s.get("spaceThreadingState"),
                    "lastActiveTime": s.get("lastActiveTime"),
                    "memberCount": member.get("joinedDirectHumanUserCount"),
                    # 讓前端知道這個名字能不能改、以及現在的名字是誰取的
                    "renamable": renamable,
                    "nameSource": (
                        "dm_manual"
                        if peer_id in manual_named
                        else ("dm_peer" if peer_id or legacy_peers.get(space_id) else None)
                    ),
                }
            )
        # 一併建索引：收件匣每一列都要查一次 Space 名稱（_mention_public），
        # 200 則 × 436 個 Space 線性掃是白付的成本
        entry = {
            "timestamp": now,
            "data": formatted,
            "index": {s["id"]: s for s in formatted},
            "was_cached": False,
        }
        _spaces_cache[viewer_id] = entry
        # 還沒認出來的私訊，丟到背景慢慢補。下次刷新（或快取過期）就看得到名字了。
        threading.Thread(
            target=_fill_dm_peers_bg,
            args=(viewer_id, formatted),
            name=f"chatpulse-dm-fill-{viewer_id}",
            daemon=True,
        ).start()
    else:
        entry = {**entry, "was_cached": True}
    return entry


def _space_entry(viewer_id: int, space_id: str) -> Optional[Dict[str, Any]]:
    entry = list_spaces_cached(viewer_id)
    index = entry.get("index")
    if index is not None:
        return index.get(space_id)
    return next((s for s in entry["data"] if s["id"] == space_id), None)


def space_display_name(viewer_id: int, space_id: str) -> str:
    found = _space_entry(viewer_id, space_id)
    return found["displayName"] if found else space_id


def space_shape(viewer_id: int, space_id: str) -> Tuple[Optional[str], Optional[str]]:
    """回傳 (spaceType, spaceThreadingState)，決定草稿的脈絡形狀。

    走的是已經在記憶體裡的 spaces 快取，不多打一次 Google API。

    查不到（Space 不在清單裡、快取剛好沒有）就回 (None, None)——
    `draft_context.is_flat_space()` 對它的判定是「當作有討論串」，
    也就是**退回改動前的行為**。判不出來時保守，不要猜成扁平：
    把別串內容當成同一段對話，比少給脈絡貴。
    """
    found = _space_entry(viewer_id, space_id)
    return (found.get("type"), found.get("threadingState")) if found else (None, None)


def name_resolver_for(viewer: Dict[str, Any]):
    """建立 user_id -> 名字的解析器。

    Google 在使用者驗證下不回傳 sender.displayName，所以每次組對話文本都必須
    透過名錄反查，否則所有發言者都會是「未知成員」（見 core/directory.py）。
    """
    return directory.make_resolver(viewer.get("google_user_id"))


def learn_names(messages: List[Dict[str, Any]]) -> None:
    """順手從訊息的 mention annotation 累積名錄。失敗不影響主流程。"""
    try:
        directory.learn_from_messages(messages)
    except Exception:
        log.exception("名錄學習失敗（不影響主流程）")


def learn_dm_peer(
    viewer: Dict[str, Any], space_id: str, messages: List[Dict[str, Any]]
) -> None:
    """讀過某個私訊的訊息時，順手認出對方是誰並記下來。

    零額外 API 成本：摘要與採集器本來就會讀這些訊息，這裡只是多看一眼 sender。
    記下來之後，Space 清單就能顯示對方名字，而不是「（私訊）」。
    認不出來（對方從沒在任何群組被 @ 過）就什麼都不做，下次再試。
    """
    try:
        if not any(
            s["id"] == space_id and s["type"] == "DIRECT_MESSAGE"
            for s in list_spaces_cached(viewer["id"])["data"]
        ):
            return
        peer_id = directory.peer_id_from_messages(messages, viewer.get("google_user_id"))
        if peer_id:
            directory.link_dm_peer(space_id, peer_id)
    except Exception:
        log.exception("私訊對象辨識失敗（不影響主流程）")


# ==========================================================================
# 採集器
# ==========================================================================

collector_runner = CollectorRunner(_client_factory_for_collector)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("SQLite 已就緒（%s，journal_mode=%s）", cfg.DB_PATH, db.journal_mode())
    # 憑證檔與資料庫的權限每次啟動都收一次：google-auth 與 OAuth flow 用預設
    # umask 寫檔（實測 0644），而那些檔含明文 refresh token 與 client secret
    try:
        modes = crypto.harden_credential_files()
        loose = [p for p, m in modes.items() if m != "missing" and ("r" in m[4:] or "r" in m[7:])]
        log.info("憑證權限已收斂：%s", {os.path.basename(k): v for k, v in modes.items()})
        if loose:
            log.warning("以下路徑權限仍偏寬，請手動確認：%s", loose)
    except Exception:
        log.exception("憑證權限收斂失敗（不影響啟動）")
    try:
        purged = repo.purge_expired()
        if purged["summaries_deleted"] or purged["mentions_deleted"]:
            log.info("保留策略清理：%s", purged)
    except Exception:
        log.exception("保留策略清理失敗")
    collector_runner.start()
    try:
        yield
    finally:
        collector_runner.stop()
        log.info("採集器已停止")


app = FastAPI(
    title="ChatPulse Dashboard API",
    version="2.0.0",
    lifespan=lifespan,
)

# 前端在開發時跑 Vite dev server（另一個 port），需要帶 cookie
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================================
# 錯誤處理（8.4）
# ==========================================================================


@app.exception_handler(ChatPulseError)
async def chatpulse_error_handler(request: Request, exc: ChatPulseError):
    headers = {}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    if exc.detail:
        log.warning("%s %s -> %s: %s", request.method, request.url.path, exc.code, exc.detail)
    return JSONResponse(exc.to_dict(), status_code=exc.http_status, headers=headers)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("未預期錯誤：%s %s", request.method, request.url.path)
    return JSONResponse(
        {"error": {"code": "INTERNAL_ERROR", "message": f"系統內部錯誤：{exc}"}},
        status_code=500,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """FastAPI 預設回 422 且格式不同，這裡換成 8.4 的 400 INVALID_PARAMETER。

    否則同一個「limit 超出範圍」的錯誤，走 Query 參數會是 400（我們自己驗），
    走 body 會是 422（pydantic 驗），前端得處理兩種錯誤形狀——而 5.5 的驗收
    條件正是「四個入口行為一致」。
    """
    msg = "參數格式錯誤"
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        raw = str(first.get("msg", ""))
        # pydantic 的訊息前綴（Value error, …）對使用者是雜訊
        raw = raw.replace("Value error, ", "")
        msg = f"參數 {loc} 不合法：{raw}" if loc else f"參數不合法：{raw}"
    return JSONResponse(
        {"error": {"code": "INVALID_PARAMETER", "message": msg}}, status_code=400
    )


# ==========================================================================
# 認證（4.3）
# ==========================================================================


def current_viewer(request: Request) -> Dict[str, Any]:
    """從 session cookie 解析出 Viewer。這是所有受保護端點的入口。"""
    token = request.cookies.get(cfg.SESSION_COOKIE_NAME)
    if not token:
        raise NotAuthenticated()
    viewer = repo.resolve_session(token)
    if not viewer:
        raise NotAuthenticated()
    return viewer


ViewerDep = Depends(current_viewer)


def viewer_display_name(viewer: Dict[str, Any]) -> Optional[str]:
    """Viewer 自己該顯示成什麼名字。

    順序：資料庫存的名字 → 人名名錄 → email。三個都沒有才回 None
    （前端會退回 email，再沒有就什麼都不顯示）。

    **為什麼不直接信資料庫那欄**：舊的三 scope token 沒有 userinfo 權限，
    以前那條路徑把 `users/1098…` 當名字存進去（見 core/identity.py），
    畫面右上角就顯示那一串。identity 已經不再這樣寫，但資料庫裡的舊資料還在，
    所以讀取端也要擋。

    名錄通常查得到自己——只要你曾經在任何群組被 @ 過，
    `directory.learn_from_messages()` 就從 annotation 學到你的名字了。
    """
    stored = viewer.get("display_name")
    if stored and not directory.looks_like_user_id(stored):
        return stored
    try:
        known = directory.load_all().get(viewer.get("google_user_id") or "")
    except Exception:
        log.exception("查名錄取自己的名字失敗（不影響登入）")
        known = None
    # load_all() 已經濾過一次，這裡再擋一次是刻意的：這個函式的承諾是
    # 「絕不回傳 user id」，不該取決於別的模組有沒有做對
    if directory.looks_like_user_id(known):
        known = None
    return known or viewer.get("email") or None


def _viewer_public(viewer: Dict[str, Any]) -> Dict[str, Any]:
    name = viewer_display_name(viewer)
    # 順手把資料庫裡那筆壞的修回來。只在「存的是 user id」時才寫，
    # 不會蓋掉使用者本來就正確的名字。
    stored = viewer.get("display_name")
    if name and directory.looks_like_user_id(stored):
        try:
            repo.upsert_viewer(viewer["google_user_id"], viewer.get("email"), name)
        except Exception:
            log.exception("修正 viewers.display_name 失敗（不影響顯示）")
    return {
        "id": viewer["id"],
        "google_user_id": viewer["google_user_id"],
        "email": viewer.get("email"),
        "display_name": name,
    }


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        cfg.SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=cfg.SESSION_TTL_HOURS * 3600,
        path="/",
    )


def _login_with_credentials(creds: Credentials) -> Dict[str, Any]:
    """把一組憑證登記成 Viewer：解析身分 → 存加密憑證 → 建 session。"""
    ident = identity.resolve(creds)
    viewer = repo.upsert_viewer(
        ident["google_user_id"], ident["email"], ident["display_name"]
    )
    repo.save_credentials(
        viewer["id"],
        creds.to_json(),
        creds.expiry.isoformat() if creds.expiry else None,
        list(creds.scopes or []),
    )
    if ident.get("display_name"):
        directory.remember(
            ident["google_user_id"], ident["display_name"], source="identity"
        )
    invalidate_client(viewer["id"])
    token = repo.create_session(viewer["id"])
    return {"viewer": viewer, "session": token}


@app.get("/api/v1/auth/status")
def auth_status(request: Request):
    token = request.cookies.get(cfg.SESSION_COOKIE_NAME)
    viewer = repo.resolve_session(token) if token else None
    legacy_available = os.path.exists(cfg.LEGACY_TOKEN_FILE)
    return {
        "authenticated": viewer is not None,
        "viewer": _viewer_public(viewer) if viewer else None,
        "legacy_token_available": legacy_available,
        "can_bootstrap": legacy_available,
        "viewer_count": len(repo.list_viewers()),
    }


@app.post("/api/v1/auth/login")
def auth_login(response: Response):
    """啟動 Google OAuth loopback flow（會在伺服器所在機器開瀏覽器）。

    OAuth client 是 desktop（installed）型別，所以用 InstalledAppFlow 的
    loopback server，不需要另建 web 型 client 與固定 redirect URI。
    """
    if not os.path.exists(cfg.CLIENT_SECRET_FILE):
        raise ConfigurationError(
            f"找不到 OAuth client secret：{cfg.CLIENT_SECRET_FILE}。"
            "請先執行 mcp_app/setup_wizard.py 取得。"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        cfg.CLIENT_SECRET_FILE, cfg.DASHBOARD_SCOPES
    )
    try:
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            timeout_seconds=180,
            authorization_prompt_message="請在瀏覽器完成 Google 授權…",
            success_message="ChatPulse 授權完成，可以關閉這個視窗回到儀表板。",
        )
    except Exception as exc:
        raise NotAuthenticated(f"Google 授權未完成：{exc}") from exc

    result = _login_with_credentials(creds)
    _set_session_cookie(response, result["session"])
    return {"authenticated": True, "viewer": _viewer_public(result["viewer"])}


@app.post("/api/v1/auth/bootstrap")
def auth_bootstrap(response: Response):
    """匯入 Phase 1 之前的單人 token（config/google_chat_token.json）。

    那個 token 只有三個 chat scope、沒有 userinfo 權限，所以身分解析可能失敗；
    失敗時 identity.resolve 會回一段說明要怎麼處理的錯誤訊息。
    """
    if not os.path.exists(cfg.LEGACY_TOKEN_FILE):
        raise NotAuthenticated(f"找不到既有憑證檔：{cfg.LEGACY_TOKEN_FILE}")

    crypto.harden_file(cfg.LEGACY_TOKEN_FILE)
    with open(cfg.LEGACY_TOKEN_FILE) as f:
        info = json.load(f)
    creds = Credentials.from_authorized_user_info(
        info, scopes=info.get("scopes") or cfg.CHAT_SCOPES
    )
    if not creds.valid and creds.refresh_token:
        from google.auth.transport.requests import Request as GRequest

        creds.refresh(GRequest())

    result = _login_with_credentials(creds)
    _set_session_cookie(response, result["session"])
    return {"authenticated": True, "viewer": _viewer_public(result["viewer"])}


@app.post("/api/v1/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(cfg.SESSION_COOKIE_NAME)
    if token:
        repo.delete_session(token)
    response.delete_cookie(cfg.SESSION_COOKIE_NAME, path="/")
    return {"authenticated": False}


@app.get("/api/v1/me")
def get_me(viewer: Dict[str, Any] = ViewerDep):
    scopes = repo.credentials_scopes(viewer["id"])
    state = repo.get_collector_state(viewer["id"])
    stats = state.get("last_run_stats")
    return {
        "viewer": _viewer_public(viewer),
        "scopes": scopes,
        "has_identity_scope": identity.has_identity_scope(scopes),
        "preferences": repo.get_preferences(viewer["id"]),
        "collector": {
            "implementation": collector_runner.collector_name,
            "interval_seconds": collector_runner.interval,
            "running": collector_runner.is_running(),
            "last_polled_at": state.get("last_polled_at"),
            "last_error": state.get("last_error"),
            "last_run_stats": json.loads(stats) if stats else None,
        },
        "mention_counts": repo.count_mentions(viewer["id"]),
        "ai": {
            "default": providers.default_name(),
            "providers": providers.describe_all(),
        },
    }


class PreferencesRequest(BaseModel):
    pinned_space_ids: Optional[List[str]] = None
    default_limit: Optional[int] = None
    default_style: Optional[str] = None
    default_provider: Optional[str] = None
    #: 草稿預設要查哪個參考專案的哪個環境（ADR-0006）
    default_code_project_id: Optional[int] = None
    default_code_environment: Optional[str] = None
    #: Draft Reply 的回覆設定預設值（ADR-0007）。
    #:
    #: 這四個欄位與上面的舊欄位有一個關鍵差異：**送 `null` 代表「清除」，
    #: 不是「不改」**。因為「不使用 Persona」「不套用預設口氣」都是使用者
    #: 會主動選的狀態，必須存得下去。handler 靠 `model_fields_set` 區分
    #: 「沒帶這個欄位」與「帶了 null」，再對應到 `repo.UNSET` 或 `None`。
    #:
    #: `default_style` 是**摘要**的章節結構，`default_reply_tone` 是**回話**
    #: 的語氣——兩者不同層次也不同值域，刻意不共用欄位（見 ADR-0007）。
    default_reply_tone: Optional[str] = None
    default_persona_id: Optional[int] = None
    default_reply_prompt_id: Optional[int] = None
    default_sepia_enabled: Optional[bool] = None


# ==========================================================================
# 參考專案（Draft Reply 的程式碼佐證）
# ==========================================================================

class CodeProjectRequest(BaseModel):
    """登錄一個本機 git repo 當作草稿的程式碼佐證來源。

    `branches` 是 environment -> branch 的對照，例如
    `{"production": "main", "uat": "release/uat"}`。分開存的理由是
    「PM 問的往往正是『正式環境到底跑哪一版』」——拿 UAT 的程式碼回答
    正式環境的問題，會產生看似有憑有據、實則錯誤的答案。
    """

    name: str = Field(min_length=1, max_length=80)
    repo_path: str = Field(min_length=1)
    branches: Dict[str, str] = Field(default_factory=dict)
    default_env: str = cfg.CODE_ENV_DEFAULT
    include_globs: List[str] = Field(default_factory=list)
    exclude_globs: List[str] = Field(default_factory=list)


class CodeProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    repo_path: Optional[str] = None
    branches: Optional[Dict[str, str]] = None
    default_env: Optional[str] = None
    include_globs: Optional[List[str]] = None
    exclude_globs: Optional[List[str]] = None
    enabled: Optional[bool] = None


def _env(value: Optional[str]) -> str:
    """把 code_search 的 ValueError 轉成 API 的 InvalidParameter。

    封閉字彙的判斷屬於 core（那裡才是單一事實來源），但 core 不該知道 HTTP。
    轉換集中在這裡一次，避免每個呼叫點各自 try/except——漏一個就是 500。
    """
    try:
        return code_search.validate_environment(value)
    except ValueError as exc:
        raise InvalidParameter(str(exc)) from exc


def _validate_branches(branches: Dict[str, str]) -> Dict[str, str]:
    """環境名稱是封閉字彙，擋掉 uat/UAT/staging 這種同義混寫。"""
    if not branches:
        raise InvalidParameter("至少要指定一個環境對應的分支，例如 production")
    out: Dict[str, str] = {}
    for env, branch in branches.items():
        key = _env(env)
        out[key] = (branch or "").strip()
        if not out[key]:
            raise InvalidParameter(f"環境 {env} 的分支名稱不可空白")
    return out


def _validate_repo_path(repo_path: Optional[str]) -> None:
    """路徑必須是絕對路徑，而且不含 NUL。

    相對路徑會相對於**伺服器的工作目錄**去解，那跟使用者心裡想的不是同一個地方；
    等到產草稿才發現「查不到這個 repo」就太晚了。
    """
    if repo_path is None:
        return
    rp = repo_path.strip()
    if not rp:
        raise InvalidParameter("專案路徑不可空白")
    if "\x00" in rp:
        raise InvalidParameter("專案路徑含非法字元")
    if not os.path.isabs(rp):
        raise InvalidParameter("專案路徑必須是絕對路徑")


def _verify_and_record(viewer_id: int, project: Dict[str, Any]) -> Dict[str, Any]:
    """驗證專案並把結果寫回，回傳帶 verification 的專案 dict。

    登錄的當下就驗，讓設定頁能顯示「這個分支已經不在了」。等到產草稿時
    才發現，代價是一份使用者以為有依據的草稿。
    """
    verification = code_search.verify_project(
        project["repo_path"], project.get("branches") or {}
    )
    error: Optional[str] = verification.get("error")
    if not error:
        missing = [
            f"{env}={info['branch']}"
            for env, info in (verification.get("branches") or {}).items()
            if not info.get("exists")
        ]
        if missing:
            error = "分支不存在：" + "、".join(missing)
    try:
        repo.record_project_verification(viewer_id, project["id"], error)
    except Exception:
        log.exception("寫入專案驗證結果失敗（不影響回應）")
    out = dict(project)
    out["verification"] = verification
    out["last_verify_error"] = error
    return out


@app.get("/api/v1/code-projects")
def get_code_projects(viewer: Dict[str, Any] = ViewerDep):
    return {
        "projects": repo.list_code_projects(viewer["id"]),
        "environments": [
            {"value": e, "label": cfg.CODE_ENV_LABELS.get(e, e)} for e in cfg.CODE_ENVIRONMENTS
        ],
        "max_per_draft": cfg.CODE_MAX_PROJECTS_PER_DRAFT,
        "enabled": cfg.CODE_ENABLED,
    }


@app.post("/api/v1/code-projects/verify")
def verify_code_project(req: CodeProjectRequest, viewer: Dict[str, Any] = ViewerDep):
    """建立前先驗一次：路徑是不是 git repo、每個分支存不存在。

    分開成一個端點而不是在建立時才驗，是因為使用者最常打錯的就是分支名，
    而那個錯誤要等到產草稿時才炸出來就太晚了。
    """
    return code_search.verify_project(req.repo_path, _validate_branches(req.branches))


@app.post("/api/v1/code-projects")
def post_code_project(req: CodeProjectRequest, viewer: Dict[str, Any] = ViewerDep):
    branches = _validate_branches(req.branches)
    _validate_repo_path(req.repo_path)
    default_env = _env(req.default_env)
    if default_env not in branches:
        raise InvalidParameter(
            f"預設環境 {default_env} 沒有對應的分支，請先指定該環境的分支"
        )
    project_id = repo.create_code_project(
        viewer["id"],
        name=req.name.strip(),
        repo_path=req.repo_path.strip(),
        branches=branches,
        default_env=default_env,
        include_globs=req.include_globs,
        exclude_globs=req.exclude_globs,
    )
    project = repo.get_code_project(viewer["id"], project_id)
    assert project is not None
    # 分支不存在仍然建立成功（分支可能之後才開），但把錯誤記下來讓設定頁標警告
    return _verify_and_record(viewer["id"], project)


@app.get("/api/v1/code-projects/{project_id}")
def get_code_project(project_id: int, viewer: Dict[str, Any] = ViewerDep):
    project = repo.get_code_project(viewer["id"], project_id)
    if project is None:
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    return project


@app.patch("/api/v1/code-projects/{project_id}")
def patch_code_project(
    project_id: int, req: CodeProjectPatch, viewer: Dict[str, Any] = ViewerDep
):
    if not repo.get_code_project(viewer["id"], project_id):
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    branches = _validate_branches(req.branches) if req.branches is not None else None
    _validate_repo_path(req.repo_path)
    repo.update_code_project(
        viewer["id"],
        project_id,
        name=req.name.strip() if req.name else None,
        repo_path=req.repo_path.strip() if req.repo_path else None,
        branches=branches,
        default_env=_env(req.default_env) if req.default_env else None,
        include_globs=req.include_globs,
        exclude_globs=req.exclude_globs,
        enabled=req.enabled,
    )
    project = repo.get_code_project(viewer["id"], project_id)
    assert project is not None
    return _verify_and_record(viewer["id"], project)


@app.post("/api/v1/code-projects/{project_id}/verify")
def reverify_code_project(project_id: int, viewer: Dict[str, Any] = ViewerDep):
    """重新確認已登錄的專案：路徑還在嗎、分支還在嗎。

    與上面那個「建立前先驗」的端點不同——那個吃 repo_path＋branches（還沒存），
    這個吃 id（已經存了）。設定頁的「重新檢查」按鈕用這個。
    """
    project = repo.get_code_project(viewer["id"], project_id)
    if project is None:
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    return _verify_and_record(viewer["id"], project)


@app.delete("/api/v1/code-projects/{project_id}")
def remove_code_project(project_id: int, viewer: Dict[str, Any] = ViewerDep):
    if not repo.delete_code_project(viewer["id"], project_id):
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    return {"deleted": True}


# ==========================================================================
# Persona（Draft Reply 的表達風格參考，ADR-0007）
#
# 與參考專案、Reference Space 同一個哲學：由人指定，系統不自動發現。
# 所有查詢都帶 viewer_id（repository 的簽章強制），沒有跨 Viewer 的入口。
# ==========================================================================


class PersonaImportRequest(BaseModel):
    """從外部來源匯入 Persona。

    兩種形態：`{"source_type":"github","repository":"owner/repo","persona":"slug"}`
    或 `{"source_type":"url","url":"..."}`。網域限制與大小／轉址／私有 IP 的
    防護在 `core/persona_sources/base.py`。
    """

    source_type: str
    repository: Optional[str] = None
    persona: Optional[str] = None
    url: Optional[str] = None
    #: 指定分支或 tag。省略時用預設分支，但**一律會解析成 commit SHA 存下來**
    #: （見 `GitHubPersonaSource._resolve_commit`）。
    ref: Optional[str] = None
    #: 覆寫顯示名稱。來源檔案的 `name` 常常是 skill 識別字
    #: （實測到 `luozhenyu-perspective`）而不是人名，所以要能改。
    name: Optional[str] = None


class PersonaCreateRequest(BaseModel):
    """手動建立 Persona。"""

    name: str
    description: str = ""
    #: 貼一段風格描述（markdown），走與遠端相同的章節抽取
    raw_text: Optional[str] = None
    #: 或直接給結構化欄位（thinking_style／communication_style／avoid…）
    profile: Optional[Dict[str, Any]] = None


class PersonaUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


#: 只在簡體中文出現、繁體中文不會用到的字形。用來判斷來源的書寫系統。
#:
#: 比對字形而不是猜語言：這批字在繁體中文裡一律寫成另一個形（這／說／時…），
#: 所以命中就是明確證據，不是機率判斷。
_SIMPLIFIED_ONLY_CHARS = frozenset(
    "这说时会对应实现样边过还让认识语见进问间关开义张长门风马齐专业东车书买卖"
    "华单变点为无与众体亲头转记论设该则给结经统级绝继续观规视觉"
    "个们么来学国电产质题传处务广总类选价组数断满术双区医压参离罗员责权证"
    "议计讲谈调达输运连远迟适递复杂难济领导习惯态势创频谱发"
)

#: 要命中幾個**不同**的字才算數。設 3 是為了擋掉偶發的單字誤判
#: （引用一個簡體書名、一個人名），那種情況不值得提醒。
_SIMPLIFIED_HITS_NEEDED = 3


def _simplified_source_notice(profile: personas.PersonaProfile) -> Optional[str]:
    """抽出來的風格條目是簡體中文時說一句。

    **這裡刻意不做任何轉換。** 匯入器的職責是「忠實抽取 ＋ 淨化指令」，
    語言風格是輸出層的事（`core/polishers/`，`language="zh-TW"`）。在匯入時
    改寫第三方原文等於竄改來源語意，而且會讓 `raw_source` 與 profile 對不起來。

    但**不處理不等於不告知**。`prompts._BASE_RULES` 的「請使用繁體中文輸出」
    會把字形壓成繁體，壓不掉的是**用詞**：抽出來的條目裡有「高频词：…靠谱」
    「东北方言——嘎巴、整（做/搞）」這種直接指定用字的句子，它們跟繁體規則
    不衝突（字形轉了就是），於是 bot 會在公司群組裡講出「視頻」「信息」
    「靠譜」。使用者選 persona 之前應該知道這件事。
    """
    text = "".join(profile.thinking_style + profile.communication_style + profile.avoid)

    # 日文的新字體與簡體有一批共同的字形（会・学・国・来・体・点），光看字形
    # 會把日文 persona 誤判成簡體。有假名就一定不是中文，直接放棄判斷——
    # 這是提醒不是守衛，寧可少說一句也不要說錯。
    if any("぀" <= char <= "ヿ" for char in text):
        return None

    hits = {char for char in text if char in _SIMPLIFIED_ONLY_CHARS}
    if len(hits) < _SIMPLIFIED_HITS_NEEDED:
        return None
    return (
        "這份來源是簡體中文。回話會照設定輸出繁體，但抽出來的表達習慣裡"
        "可能帶著中國用語（例如「視頻」「信息」「靠譜」），"
        "請看一眼下面的條目再決定要不要啟用。"
    )


def _persona_import_notice(
    fetched: persona_sources.FetchedPersona,
    profile: Optional[personas.PersonaProfile] = None,
) -> Optional[str]:
    """匯入成功了，但有件事值得說一句。

    回傳的是**所有適用提醒串起來的一句**（各自的判斷見下面各函式），
    都不適用就回 `None`——不要為了「有東西可顯示」而硬湊一句廢話。

    **repo 根目錄的 SKILL.md 有可能不是 persona。** 實測
    `fxp/persona-distill-skills` 根目錄那份是「如何蒸餾一個 persona」的
    方法論，而它跑完淨化是 `is_usable() == True`（抽到思考 4／表達 2／
    邊界 2）——也就是說**擋住它的不是淨化器，是 Repository 模式的
    `_LISTING_RE` 要求 slug 那一層存在**（見 `persona_sources/github.py`）。

    網址模式沒有那道守衛：使用者可以直接貼根目錄的檔案網址，而抽出來的
    東西看起來完全像一份合理的 persona。所以這裡**不擋**（那會擋掉真的把
    persona 放在根目錄的 repo，那是生態裡的多數形態），只提醒一句，
    讓他去看一眼抽出來的條目對不對。

    2026-09-12：這裡原本先擋掉 `source_type != "url"`，理由寫的是
    「Repository 模式有 `_LISTING_RE` 守著，走不到根目錄」。**那句話不再成立**
    ——Repository 模式現在會在沒有任何 `personas/`／`skills/` 結構時採用根目錄的
    檔案（見 `persona_sources/github.py` 的 `_single_persona_root()`）。
    那道新守衛擋得住 `fxp/persona-distill-skills`（它兩種結構都有），但擋不住
    「整個 repo 就只有一份方法論 SKILL.md」的情況，所以這句提醒對它一樣需要。

    改成只看路徑形狀就同時涵蓋兩種模式：Repository 模式正常取到的路徑一定含
    `/`（`personas/<名稱>/SKILL.md`），只有根目錄那條不含。
    """
    notices: List[str] = []

    path = (fetched.extra or {}).get("path") or ""
    if path and "/" not in path:
        notices.append(
            f"這份是從 repo 根目錄的 {path} 匯入的。有些 repo 根目錄放的是"
            "「如何寫 persona」的方法論而不是某個人的風格，"
            "請看一眼下面抽出來的條目是不是你要的。"
        )

    if profile is not None:
        simplified = _simplified_source_notice(profile)
        if simplified:
            notices.append(simplified)

    return " ".join(notices) if notices else None


def _persona_import_result(
    viewer_id: int, fetched: persona_sources.FetchedPersona, override_name: Optional[str]
) -> Dict[str, Any]:
    """把取回的原文淨化、落地，回 API 形狀。

    **這是遠端內容唯一的入口。** 原文在這裡就被轉成 `PersonaProfile`，
    之後的任何路徑都只看得到淨化後的結構化資料（`raw_source` 只為 debug
    與更新時比較差異而存，不進 prompt——見 `core/personas.py`）。
    """
    profile = personas.normalize_persona(
        fetched.raw_text,
        name_hint=override_name or fetched.name_hint,
        description_hint=fetched.description_hint,
    )
    if not profile.is_usable():
        # 這是**可預期的正常結果**，不是 bug：來源檔案可能整份都是角色扮演
        # 指令與工作流程，那些一律不採用，淨化完就空了。
        #
        # 訊息一定要帶 `describe_unusable()` 的診斷。少了它，使用者只知道
        # 「這份不能用」卻不知道是「拿錯檔案」還是「只差一個章節標題」，
        # 而那兩件事的下一步完全不同（換來源 vs 改標題）。
        raise PersonaInvalid(
            "這份來源淨化之後沒有留下任何可用的風格資訊"
            "（角色扮演指令、工作流程、工具呼叫一律不採用）。"
            f"{personas.describe_unusable(fetched.raw_text)}"
            "也可以改用自訂 Persona 手動填寫風格描述。"
        )

    name = (override_name or profile.name or fetched.name_hint or "").strip()
    if not name:
        raise InvalidParameter("無法判斷 Persona 名稱，請用 name 欄位指定")

    payload = dict(
        name=name,
        description=profile.description,
        profile_json=profile.to_json(),
        source_repository=fetched.source_repository,
        source_url=fetched.source_url,
        source_ref=fetched.source_ref,
        source_commit_sha=fetched.source_commit_sha,
        source_hash=fetched.source_hash,
        raw_source=fetched.raw_text,
    )

    notice = _persona_import_notice(fetched, profile)

    existing = repo.find_persona_by_name(viewer_id, name)
    if existing is not None:
        # 同名視為「更新」而不是報衝突：使用者按「更新 Persona」時走的就是
        # 同一條匯入流程，報 409 會逼他先刪再匯入，中間那段時間偏好會斷掉。
        updated = repo.update_persona(
            viewer_id,
            existing["id"],
            description=payload["description"],
            profile_json=payload["profile_json"],
            source_ref=payload["source_ref"],
            source_commit_sha=payload["source_commit_sha"],
            source_hash=payload["source_hash"],
            raw_source=payload["raw_source"],
            touch_refreshed=True,
        )
        return {"persona": updated, "created": False, "notice": notice}

    persona_id = repo.create_persona(
        viewer_id, source_type=fetched.source_type, **payload
    )
    return {
        "persona": repo.get_persona(viewer_id, persona_id),
        "created": True,
        "notice": notice,
    }


@app.get("/api/v1/personas")
def get_personas(viewer: Dict[str, Any] = ViewerDep):
    """列出自己匯入的 Persona 與可用的來源型別。"""
    return {
        "personas": repo.list_personas(viewer["id"]),
        "sources": persona_sources.describe_all(),
    }


@app.get("/api/v1/personas/{persona_id}")
def get_persona_detail(
    persona_id: int,
    include_raw: bool = Query(False, description="是否附上遠端原文（僅供 debug）"),
    viewer: Dict[str, Any] = ViewerDep,
):
    persona = repo.get_persona(viewer["id"], persona_id, include_raw=include_raw)
    if persona is None:
        raise PersonaNotFound(f"找不到 Persona {persona_id}")
    return persona


@app.post("/api/v1/personas/import")
def import_persona(req: PersonaImportRequest, viewer: Dict[str, Any] = ViewerDep):
    """從公開來源匯入 Persona，並固定版本。

    版本固定不是嚴謹好看：遠端隨時可以改 SKILL.md，而 Viewer 不會知道。
    匯入時解析成 commit SHA 存下來，只有按「更新」才重新取得——
    否則「今天產生的回話」會因為明天遠端偷偷改了檔案而變一個樣子。
    """
    source = persona_sources.resolve(req.source_type)
    fetched = source.fetch(
        repository=req.repository,
        persona=req.persona,
        url=req.url,
        ref=req.ref,
    )
    return _persona_import_result(viewer["id"], fetched, req.name)


@app.get("/api/v1/personas/sources/{source_type}/list")
def list_source_personas(
    source_type: str,
    repository: str = Query(..., description="owner/repo"),
    ref: Optional[str] = Query(None),
    viewer: Dict[str, Any] = ViewerDep,
):
    """列出某個來源 repo 有哪些 Persona 可以匯入。

    需要登入（`ViewerDep`）但不讀 Viewer 的資料——這裡的 `ViewerDep` 純粹
    當認證閘門，避免變成一個未登入就能用的對外 GET 代理。
    寫法比照 `verify_code_project`。
    """
    source = persona_sources.resolve(source_type)
    return {"personas": source.list_personas(repository=repository, ref=ref)}


@app.post("/api/v1/personas/{persona_id}/refresh")
def refresh_persona(persona_id: int, viewer: Dict[str, Any] = ViewerDep):
    """重新從原來的來源取得這份 Persona（會更新 commit SHA）。"""
    viewer_id = viewer["id"]
    existing = repo.get_persona(viewer_id, persona_id)
    if existing is None:
        raise PersonaNotFound(f"找不到 Persona {persona_id}")
    if existing["source_type"] == "manual":
        raise InvalidParameter("自訂 Persona 沒有外部來源可以更新，請直接編輯內容")

    source = persona_sources.resolve(existing["source_type"])
    fetched = source.fetch(
        repository=existing["source_repository"],
        persona=existing["name"],
        url=existing["source_url"],
        # 刻意用原本的 ref（分支名）重新解析，而不是沿用舊的 commit SHA——
        # 「更新」的意思就是去看那個分支現在長什麼樣。
        ref=existing["source_ref"],
    )
    result = _persona_import_result(viewer_id, fetched, existing["name"])
    result["changed"] = existing["source_hash"] != fetched.source_hash
    return result


@app.post("/api/v1/personas")
def post_persona(req: PersonaCreateRequest, viewer: Dict[str, Any] = ViewerDep):
    """手動建立 Persona。

    手填內容**一樣**走完整的淨化流程：使用者最可能的填寫方式就是從某處
    複製一份 skill 全文貼進來，那與遠端抓下來的沒有任何差別。
    """
    source = persona_sources.resolve("manual")
    fetched = source.fetch(
        name=req.name,
        description=req.description,
        raw_text=req.raw_text,
        profile=req.profile,
    )
    if req.profile is not None:
        # 結構化輸入不需要章節抽取，但要過 `_coerce` 的逐條淨化
        profile = personas.PersonaProfile.from_json(
            json.dumps({**req.profile, "name": req.name}, ensure_ascii=False)
        )
        if not profile.is_usable():
            # 這條走的是**結構化輸入**，沒有原文可以做章節診斷，所以講的是
            # 欄位：`is_usable()` 刻意不含 boundaries，只填能力邊界會走到這裡
            # 而使用者看不出原因（見 `PersonaProfile.is_usable`）。
            raise PersonaInvalid(
                "填寫的內容淨化之後沒有留下可用的風格資訊。"
                "thinking_style、communication_style、avoid 至少要有一個有內容"
                "——只填 boundaries（能力邊界）不算，它描述的是不擅長什麼，"
                "單獨存在不構成 persona。"
            )
        name = req.name.strip()
        if not name:
            raise InvalidParameter("Persona 需要名稱")
        if repo.find_persona_by_name(viewer["id"], name) is not None:
            raise InvalidParameter(f"已經有一個叫「{name}」的 Persona")
        persona_id = repo.create_persona(
            viewer["id"],
            name=name,
            description=(req.description or "").strip(),
            profile_json=profile.to_json(),
            source_type="manual",
            source_hash=fetched.source_hash,
            raw_source=fetched.raw_text,
        )
        return {"persona": repo.get_persona(viewer["id"], persona_id), "created": True}

    return _persona_import_result(viewer["id"], fetched, req.name)


@app.patch("/api/v1/personas/{persona_id}")
def patch_persona(
    persona_id: int, req: PersonaUpdateRequest, viewer: Dict[str, Any] = ViewerDep
):
    """改名、改簡介、啟用／停用。**不能從這裡改 profile 內容**。

    profile 只能由匯入流程產生，因為那條路徑保證跑過淨化。開一個
    「直接寫 profile_json」的入口等於開一個繞過淨化的後門。
    """
    sent = req.model_fields_set

    # 改名一定要過淨化。`personas.name` 會被寫進 prompt（見
    # `resolve_reply_options` 以 row.name 為單一事實來源），所以它與匯入時的
    # frontmatter `name` 是同一個信任等級——不淨化的話「改名」就是一條
    # 繞過全部四層防線、把任意文字直接送進 prompt 的路徑。
    name = repo.UNSET
    if "name" in sent:
        name = personas.clean_display_name(req.name)
        if not name:
            raise InvalidParameter(
                "Persona 名稱不可以是空的，也不可以包含指令性的內容"
            )

    description = repo.UNSET
    if "description" in sent:
        # 簡介只顯示給人看、不進 prompt，但一樣走淨化——它會出現在選單裡，
        # 而使用者可能貼進整段 skill 文字。
        description = personas.clean_display_description(req.description)

    updated = repo.update_persona(
        viewer["id"],
        persona_id,
        name=name,
        description=description,
        enabled=req.enabled if "enabled" in sent else repo.UNSET,
    )
    if updated is None:
        raise PersonaNotFound(f"找不到 Persona {persona_id}")
    return updated


@app.delete("/api/v1/personas/{persona_id}")
def remove_persona(persona_id: int, viewer: Dict[str, Any] = ViewerDep):
    if not repo.delete_persona(viewer["id"], persona_id):
        raise PersonaNotFound(f"找不到 Persona {persona_id}")
    return {"deleted": True}


# ==========================================================================
# Reply Prompt Preset（存起來重複使用的自訂提示詞，ADR-0007）
# ==========================================================================


class ReplyPromptRequest(BaseModel):
    name: str
    description: str = ""
    prompt: str


class ReplyPromptUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None


@app.get("/api/v1/reply-prompts")
def get_reply_prompts(viewer: Dict[str, Any] = ViewerDep):
    return {"reply_prompts": repo.list_reply_prompts(viewer["id"])}


@app.post("/api/v1/reply-prompts")
def post_reply_prompt(req: ReplyPromptRequest, viewer: Dict[str, Any] = ViewerDep):
    name = req.name.strip()
    prompt = (req.prompt or "").strip()
    if not name:
        raise InvalidParameter("回覆提示詞需要名稱")
    if not prompt:
        raise InvalidParameter("回覆提示詞的內容不可以是空的")
    if len(prompt) > MAX_CUSTOM_PROMPT_CHARS:
        raise InvalidParameter(
            f"回覆提示詞過長（{len(prompt)} 字，上限 {MAX_CUSTOM_PROMPT_CHARS} 字）"
        )
    if repo.get_reply_prompt_by_name(viewer["id"], name) is not None:
        raise InvalidParameter(f"已經有一個叫「{name}」的回覆提示詞")
    prompt_id = repo.create_reply_prompt(
        viewer["id"], name=name, description=req.description, prompt=prompt
    )
    return repo.get_reply_prompt(viewer["id"], prompt_id)


@app.patch("/api/v1/reply-prompts/{prompt_id}")
def patch_reply_prompt(
    prompt_id: int, req: ReplyPromptUpdateRequest, viewer: Dict[str, Any] = ViewerDep
):
    sent = req.model_fields_set
    if "prompt" in sent:
        text = (req.prompt or "").strip()
        if not text:
            raise InvalidParameter("回覆提示詞的內容不可以是空的")
        if len(text) > MAX_CUSTOM_PROMPT_CHARS:
            raise InvalidParameter(
                f"回覆提示詞過長（{len(text)} 字，上限 {MAX_CUSTOM_PROMPT_CHARS} 字）"
            )
    updated = repo.update_reply_prompt(
        viewer["id"],
        prompt_id,
        name=req.name if "name" in sent else repo.UNSET,
        description=req.description if "description" in sent else repo.UNSET,
        prompt=req.prompt if "prompt" in sent else repo.UNSET,
    )
    if updated is None:
        raise ReplyPromptNotFound(f"找不到回覆提示詞 {prompt_id}")
    return updated


@app.delete("/api/v1/reply-prompts/{prompt_id}")
def remove_reply_prompt(prompt_id: int, viewer: Dict[str, Any] = ViewerDep):
    if not repo.delete_reply_prompt(viewer["id"], prompt_id):
        raise ReplyPromptNotFound(f"找不到回覆提示詞 {prompt_id}")
    return {"deleted": True}


class DraftTargetRequest(BaseModel):
    """指定一個 Space，對「對方最後說的話」產生回覆草稿。"""

    space_id: str


@app.post("/api/v1/spaces/draft-target")
def create_draft_target(req: DraftTargetRequest, viewer: Dict[str, Any] = ViewerDep):
    """挑出該 Space 裡對方最後說的那則訊息，回傳可以拿去產草稿的 mention_id。

    為什麼要這一步：草稿流程整條都掛在 mentions 上，而私訊不會產生 mention
    （沒人會在私訊裡 @ 你）。這裡合成一筆 state='manual' 的記錄當作錨點，
    前端拿到 id 之後走的還是原本那條 /mentions/{id}/draft/stream，
    不必為了私訊再寫一份草稿邏輯。

    只挑**不是自己發的**訊息——要回覆的是對方說的話，不是自己說的。
    """
    if not req.space_id.startswith("spaces/"):
        raise InvalidParameter(f"space_id 必須是完整資源名（spaces/…），收到 {req.space_id!r}")

    viewer_id = viewer["id"]
    messages = get_client(viewer_id).fetch_recent_messages(req.space_id, limit=20)
    learn_names(messages)
    learn_dm_peer(viewer, req.space_id, messages)

    self_id = viewer.get("google_user_id")
    target = None
    for msg in reversed(messages):  # 回傳是由舊到新，倒著找最近的一則
        if (msg.get("sender") or {}).get("name") != self_id:
            target = msg
            break
    if target is None:
        raise InvalidParameter("這個對話裡找不到別人發的訊息，沒有東西可以回覆")

    resolve = name_resolver_for(viewer)
    sender_id = (target.get("sender") or {}).get("name")
    mention_id = repo.upsert_draft_target(
        viewer_id,
        {
            "space_id": req.space_id,
            "space_name": space_display_name(viewer_id, req.space_id),
            "message_name": target["name"],
            "thread_name": (target.get("thread") or {}).get("name"),
            "sender_name": sender_id,
            "sender_display": resolve(sender_id),
            "create_time": target.get("createTime"),
        },
    )
    # 回完整的 mention 物件：收件匣端點會把 state='manual' 濾掉，前端拿不到，
    # 而草稿工作區需要整個物件才畫得出來。
    row = repo.get_mention(viewer_id, mention_id)
    rows = _hydrate_mention_content(get_client(viewer_id), [row]) if row else []
    return {
        "mention_id": mention_id,
        "mention": (
            _mention_public(
                rows[0],
                name_resolver_for(viewer),
                viewer_id=viewer_id,
                # 通常是剛建立的手動 Mention（沒有草稿），但這個端點對同一則
                # 按第二次會拿到既有的那筆——那時它可能已經有草稿了
                has_draft=repo.latest_draft(mention_id) is not None,
            )
            if rows
            else None
        ),
    }


class SpaceAliasRequest(BaseModel):
    """給沒有官方名稱的空間（主要是私訊）取一個自己看得懂的名字。"""

    space_id: str
    #: 空字串等於清除別名，回到自動辨識的結果
    alias: str = Field(default="", max_length=60)


@app.patch("/api/v1/spaces/alias")
def patch_space_alias(req: SpaceAliasRequest, viewer: Dict[str, Any] = ViewerDep):
    """手動命名一個空間。

    私訊的對方是誰只能靠「讀訊息取 sender ＋ 查人名名錄」推，而名錄只認得
    曾在群組被 @ 過的人——實測 131 個私訊只認得出 20 個。剩下的與其一直顯示
    「（私訊）」，不如讓使用者自己取名字，他本來就知道對方是誰。
    """
    if not req.space_id.startswith("spaces/"):
        raise InvalidParameter(f"space_id 必須是完整資源名（spaces/…），收到 {req.space_id!r}")

    viewer_id = viewer["id"]
    # 名字要掛在「人」身上而不是空間上：space 的資源名稱 Google 沒保證永久不變，
    # 而且掛在人身上的話，摘要與草稿裡的發言者名稱也會一起變成這個名字。
    peer_id = directory.load_dm_links().get(req.space_id)
    if not peer_id:
        # 還沒認出對方是誰就先認一次（讀 3 則訊息，很便宜）
        try:
            msgs = get_client(viewer_id).fetch_recent_messages(req.space_id, limit=3)
            peer_id = directory.peer_id_from_messages(msgs, viewer.get("google_user_id"))
            if peer_id:
                directory.link_dm_peer(req.space_id, peer_id)
        except Exception:
            log.exception("取對方 user id 失敗，改用空間別名")

    if peer_id:
        directory.set_person_name(peer_id, req.alias)
    else:
        # 對話一則訊息都沒有時沒有對方可掛，退回綁在空間上
        directory.set_space_alias(req.space_id, req.alias)

    _spaces_cache.pop(viewer_id, None)  # 清單是快取的，不清會顯示舊名字
    return {
        "space_id": req.space_id,
        "alias": req.alias.strip(),
        "bound_to": "person" if peer_id else "space",
        "ok": True,
    }


@app.patch("/api/v1/preferences")
def patch_preferences(req: PreferencesRequest, viewer: Dict[str, Any] = ViewerDep):
    viewer_id = viewer["id"]
    limit = validate_limit(req.default_limit) if req.default_limit is not None else None
    style = prompts.validate_style(req.default_style) if req.default_style else None
    provider = req.default_provider
    if provider:
        # 存進偏好前先驗一次，避免存下一個會在每次摘要時才爆的值
        providers.resolve_name(provider)
    # 同樣先驗一次，避免存下一個會在每次產草稿時才爆的值
    code_env = _env(req.default_code_environment) if req.default_code_environment else None
    if req.default_code_project_id is not None:
        if repo.get_code_project(viewer_id, req.default_code_project_id) is None:
            raise CodeProjectNotFound(f"找不到參考專案 {req.default_code_project_id}")

    # ---- 回覆設定（ADR-0007）----
    #
    # 這四個欄位要區分「沒帶這個欄位」與「帶了 null」：後者是使用者主動選
    # 「不使用 Persona」「不套用預設口氣」，必須存得下去。`model_fields_set`
    # 只包含請求 JSON 裡實際出現過的鍵，所以能分得出來；沒出現的就傳
    # `repo.UNSET`，讓 repository 沿用現值。
    sent = req.model_fields_set
    tone = repo.UNSET
    if "default_reply_tone" in sent:
        # 存進偏好前先驗一次，避免存下一個會在每次產草稿時才降級的值
        tone = (
            reply_profiles.validate_tone(req.default_reply_tone)
            if req.default_reply_tone
            else None
        )

    persona_id = repo.UNSET
    if "default_persona_id" in sent:
        persona_id = req.default_persona_id
        if persona_id:
            if repo.get_persona(viewer_id, persona_id) is None:
                raise PersonaNotFound(f"找不到 Persona {persona_id}")
        else:
            # 0 與 null 都當成「清除」——前端的「不使用 Persona」選項送哪個都行
            persona_id = None

    prompt_id = repo.UNSET
    if "default_reply_prompt_id" in sent:
        prompt_id = req.default_reply_prompt_id
        if prompt_id:
            if repo.get_reply_prompt(viewer_id, prompt_id) is None:
                raise ReplyPromptNotFound(f"找不到回覆提示詞 {prompt_id}")
        else:
            prompt_id = None

    sepia = repo.UNSET
    if "default_sepia_enabled" in sent:
        sepia = req.default_sepia_enabled

    return repo.update_preferences(
        viewer_id,
        pinned_space_ids=req.pinned_space_ids,
        default_limit=limit,
        default_style=style,
        default_provider=provider,
        default_code_project_id=req.default_code_project_id,
        default_code_environment=code_env,
        default_reply_tone=tone,
        default_persona_id=persona_id,
        default_reply_prompt_id=prompt_id,
        default_sepia_enabled=sepia,
    )


# ==========================================================================
# Phase 1：Space 與訊息
# ==========================================================================


@app.get("/api/v1/spaces")
def get_spaces(
    search: Optional[str] = None,
    refresh: bool = False,
    viewer: Dict[str, Any] = ViewerDep,
):
    """Space 列表（5.1）。必須涵蓋全部空間——list_spaces 已自動翻頁（D-1）。"""
    entry = list_spaces_cached(viewer["id"], refresh=refresh)
    spaces = entry["data"]
    pinned = set(repo.get_preferences(viewer["id"])["pinned_space_ids"])

    results = spaces
    if search:
        needle = search.lower()
        results = [s for s in spaces if needle in s["displayName"].lower()]

    return {
        "count": len(results),
        "total": len(spaces),
        "cached": entry.get("was_cached", False),
        "cached_at": datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat(
            timespec="seconds"
        ),
        "spaces": [{**s, "pinned": s["id"] in pinned} for s in results],
    }


@app.get("/api/v1/messages")
def get_messages(
    space_id: str = Query(..., description="完整 Space 資源名，例如 spaces/AAAAxLxqJxY"),
    limit: Optional[int] = Query(default=None),
    thread_name: Optional[str] = Query(
        default=None,
        description="給了就回傳整個討論串（忽略 limit 的『最近 N 則』語意）",
    ),
    viewer: Dict[str, Any] = ViewerDep,
):
    """訊息列表（8.2）。space_id 走 query string，不放 path（8.1）。

    **回傳的是扁平訊息流，討論串回覆混在裡面**——Google 的 `messages.list`
    沒有參數可以排除它們，所以「包含討論串」是預設行為，不是選項。
    但同一串常常只被切到片段（`limit` 是按 createTime 取最近 N 則），
    所以每則都帶 `thread_name`，呼叫端可以自己分組；要看完整的一串就再打一次
    這個端點並帶 `thread_name`。
    """
    limit = validate_limit(limit)
    if not space_id.startswith("spaces/"):
        raise InvalidParameter(f"space_id 必須是完整資源名（spaces/…），收到 {space_id!r}")
    if thread_name and not thread_name.startswith(f"{space_id}/threads/"):
        raise InvalidParameter(
            f"thread_name 必須屬於 {space_id}，收到 {thread_name!r}"
        )

    client = get_client(viewer["id"])
    if thread_name:
        messages = client.list_thread_messages(space_id, thread_name, limit=limit)
    else:
        messages = client.fetch_recent_messages(space_id, limit=limit)
    learn_names(messages)
    learn_dm_peer(viewer, space_id, messages)
    resolve = name_resolver_for(viewer)

    formatted = []
    for m in messages:
        text = (m.get("text") or "").strip()
        # 只有圖、沒有文字的訊息**也要回**。在此之前這裡直接 continue 掉，
        # 於是工作群組最常見的「@某人 ＋ 一張截圖」在清單上整則消失，
        # 而且看不出漏了東西——只會覺得「怎麼比我要的少幾則」。
        # 這與 chat_client.format_conversation 的處理一致，用同一個佔位符函式。
        note = attachment_note(m)
        if not text and not note:
            continue
        sender_obj = m.get("sender") or {}
        formatted.append(
            {
                "name": m.get("name"),
                "sender": sender_obj.get("displayName") or resolve(sender_obj.get("name")),
                "sender_id": sender_obj.get("name"),
                "time": (m.get("createTime") or "")[:16].replace("T", " "),
                "text": text,
                #: 附件的人話描述（含「存放於 Drive，本系統無權讀取」那種）。
                #: 沒有附件就是空字串。
                "attachment_note": note,
                #: 這則屬於哪一串。私訊幾乎每則各自一串，群組才看得出結構。
                "thread_name": (m.get("thread") or {}).get("name"),
            }
        )
    return {
        "space_id": space_id,
        "space_name": space_display_name(viewer["id"], space_id),
        "thread_name": thread_name,
        "count": len(formatted),
        "messages": formatted,
    }


@app.get("/api/v1/styles")
def get_styles():
    return {"styles": prompts.style_options()}


@app.get("/api/v1/reply-tones")
def get_reply_tones():
    """列出 Draft Reply 的回覆口氣選項（ADR-0007）。

    與 `/styles` 刻意分開：那個是**摘要**的章節結構，這個是**回話**的語氣，
    兩者值域不同、作用的功能也不同（見 `core/reply_profiles.py` 的模組說明）。

    回應**不含** prompt instruction——那是送給模型的片段，前端不需要，
    送出去只會變成使用者讀得到卻改不了的死資料。`example` 有送，
    UI 拿它做固定預覽，不必為了預覽去打一次 AI。

    不需登入，理由同 `/styles`：這是靜態選項清單。
    """
    return {
        "tones": reply_profiles.tone_options(),
        "default": reply_profiles.REPLY_TONE_DEFAULT,
    }


@app.get("/api/v1/polishers")
def get_polishers():
    """列出可用的潤稿器與可用性（ADR-0007）。

    `available: false` 時 `reason` 要說得出下一步（規則檔在哪、怎麼補），
    比照 `/providers` 的契約。這裡刻意不帶 provider 去檢查——
    這個端點只回答「規則裝好了嗎」，供應商的可用性由 `/providers` 回答，
    兩者混在一起會讓前端無法判斷是哪一邊沒裝好。
    """
    return {
        "polishers": polishers.describe_all(),
        "sepia": sepia_polisher.rules_version(),
    }


@app.get("/api/v1/providers")
def get_providers():
    """列出 AI 供應商與各自現在可不可用。

    不需登入——前端在登入畫面就可能要顯示「目前沒有可用的 AI 供應商」。
    `available: false` 的項目會附上**說得出下一步的原因**（要設哪個環境變數、
    要裝什麼），而不是只說失敗。
    """
    return {
        "default": providers.default_name(),
        "providers": providers.describe_all(),
    }


# ==========================================================================
# SSE 共用（8.3）
# ==========================================================================


def sse(payload: Dict[str, Any]) -> str:
    """把事件包成單行 JSON 的 SSE frame。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 反向代理（nginx 等）會緩衝串流，這個標頭要它別緩衝
    "X-Accel-Buffering": "no",
}


def sse_response(generator: Generator[str, None, None]) -> StreamingResponse:
    return StreamingResponse(
        generator, media_type="text/event-stream", headers=SSE_HEADERS
    )


def save_partial(save, kind: str, partial: str) -> None:
    """串流沒能正常跑完時，把已經生成的內容補存下來。

    **為什麼需要這個**：兩個串流端點原本都是「整段跑完 → 寫進資料庫 → 送 done」。
    但客戶端一旦中途斷線（使用者切走頁籤、關掉分頁、網路斷、重新整理），
    下一個 `yield` 就會拋 GeneratorExit，迴圈當場中止——寫入資料庫那行永遠
    到不了。結果是 AI 額度已經燒掉、內容也生成了，卻整份丟掉，使用者回來
    什麼都沒有。

    這裡在 finally 補存一次。GeneratorExit 期間不能再 yield（會 RuntimeError），
    但寫資料庫沒問題，所以只落檔、不回報事件。

    `save` 是一個只收內容字串的 callable，由呼叫端把其餘欄位綁好。
    """
    if not partial.strip():
        return
    try:
        save(partial)
        log.info("%s串流未正常結束，已補存 %d 字的內容", kind, len(partial))
    except Exception:
        # 補存失敗不能再往外拋——這裡已經在收尾路徑上，拋出去只會蓋掉原本的錯誤
        log.exception("%s在斷線後補存內容失敗", kind)


class SummarizeRequest(BaseModel):
    space_id: str
    #: AI 供應商；省略時用 Viewer 偏好或伺服器預設
    provider: Optional[str] = None
    # 5.5：SSE 端點補上驗證，不再是裸 int（v1 可傳 99999）
    limit: int = Field(default=cfg.LIMIT_DEFAULT, ge=cfg.LIMIT_MIN, le=cfg.LIMIT_MAX)
    style: str = cfg.SUMMARY_STYLE_DEFAULT

    @field_validator("style")
    @classmethod
    def _check_style(cls, v: str) -> str:
        return prompts.validate_style(v)

    @field_validator("space_id")
    @classmethod
    def _check_space(cls, v: str) -> str:
        if not v.startswith("spaces/"):
            raise ValueError("必須是完整資源名（spaces/…）")
        return v


@app.post("/api/v1/summarize/stream")
def summarize_stream(req: SummarizeRequest, viewer: Dict[str, Any] = ViewerDep):
    """單群摘要 SSE（5.2）。

    參數驗證在進串流之前做，所以 limit 傳 0／1001 會得到 HTTP 400，
    與其他三個入口一致（5.5 的驗收條件）。串流開始之後的錯誤才走 error 事件。
    """
    viewer_id = viewer["id"]
    space_id = req.space_id
    limit = req.limit
    style = req.style
    req_provider = req.provider
    display = space_display_name(viewer_id, space_id)
    # 供應商名稱在進串流前先驗，這樣打錯名字會得到 HTTP 400 而不是
    # 一個「串流開始後才出錯」的 error 事件（與 limit／style 的處理一致）
    resolved_provider = providers.resolve_name(req_provider)

    def generate() -> Generator[str, None, None]:
        # 這三個放在 try 外面：客戶端中途斷線時 yield 會拋 GeneratorExit，
        # finally 仍要看得到已收到的內容才補存得了（見 save_partial）
        collected: List[str] = []
        saved = False
        count = 0
        try:
            client = get_client(viewer_id)
            # 供應商在 meta 之前就要建好——meta 事件要帶 model，而 model
            # 由供應商自己回報
            ai = get_provider(viewer_id, req_provider)
            messages = client.fetch_recent_messages(space_id, limit=limit)
            learn_names(messages)
            learn_dm_peer(viewer, space_id, messages)
            conversation = format_conversation(messages, name_resolver_for(viewer))
            count = len(messages)

            if not conversation.strip():
                yield sse(
                    {
                        "type": "error",
                        "code": "NO_MESSAGES",
                        "message": "該聊天室在指定範圍內沒有可摘要的對話",
                    }
                )
                return

            # 能力檢查要在下載之前——不支援視覺就根本不去取圖，
            # 省掉整條 API 往返與流量（那些圖仍以佔位符出現在對話文本裡）
            images: List = []
            skipped_images: List[str] = []
            if ai.supports_vision:
                images, skipped_images = attachments.collect(
                    client.download_attachment,
                    messages,
                    space_id=space_id,
                    budget_tokens=cfg.IMAGE_BUDGET_TOKENS_SUMMARY,
                )

            yield sse(
                {
                    "type": "meta",
                    "space": display,
                    "space_id": space_id,
                    "message_count": count,
                    "style": style,
                    "provider": resolved_provider,
                    "model": ai.model,
                    "image_count": len(images),
                    "images_skipped": skipped_images,
                }
            )

            prompt = prompts.summary_prompt(display, conversation, count, style)
            for chunk in ai.stream_text(prompt, operation="summarize", images=images):
                collected.append(chunk)
                yield sse({"type": "chunk", "text": chunk})

            content = "".join(collected)
            summary_id = None
            if content.strip():
                summary_id = repo.create_summary(
                    viewer_id, space_id, display, style, count, content
                )
            saved = True
            yield sse({"type": "done", "summary_id": summary_id})
        except ChatPulseError as exc:
            yield sse(exc.to_sse_event())
        except Exception as exc:  # 串流已開始，只能用事件回報
            log.exception("摘要串流失敗")
            yield sse(
                {"type": "error", "code": "INTERNAL_ERROR", "message": f"摘要失敗：{exc}"}
            )
        finally:
            if not saved:
                save_partial(
                    lambda text: repo.create_summary(
                        viewer_id, space_id, display, style, count, text
                    ),
                    "摘要",
                    "".join(collected),
                )

    return sse_response(generate())


class PublishRequest(BaseModel):
    space_id: str
    text: str
    thread_name: Optional[str] = None

    @field_validator("text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("訊息內容不可為空")
        return v


@app.post("/api/v1/publish")
def publish_message(req: PublishRequest, viewer: Dict[str, Any] = ViewerDep):
    """推播回 Google Chat（5.4）。以 Viewer 本人身分送出，非 Bot（ADR-0001）。

    二次確認由前端負責——後端沒有辦法驗證「使用者真的按了確認」，
    把它做成 API 參數只會變成一個永遠傳 true 的欄位。
    """
    res = get_client(viewer["id"]).send_message(
        req.space_id, req.text, thread_name=req.thread_name
    )
    return {
        "status": "success",
        "message_id": res.get("name"),
        "createTime": res.get("createTime"),
        "thread_name": (res.get("thread") or {}).get("name"),
    }


@app.get("/api/v1/summaries")
def get_summaries(
    limit: int = Query(default=cfg.LIMIT_DEFAULT, ge=cfg.LIMIT_MIN, le=cfg.LIMIT_MAX),
    viewer: Dict[str, Any] = ViewerDep,
):
    """本人的歷史 Summary。ADR-0002：查詢一律帶 owner_viewer_id。

    limit 用 core 的共用常數，不要另外寫死一個數字——5.5 的要求是所有入口
    共用同一組上下限，而附錄 B 已經把「上限 500」列為 v1 的錯誤並修正為 1000。
    """
    rows = repo.list_summaries(viewer["id"], limit=limit)
    return {"count": len(rows), "summaries": rows}


# ==========================================================================
# Phase 2：Mention 收件匣（六節）
# ==========================================================================


def _hydrate_mention_content(
    client: GoogleChatClient, rows: List[Dict[str, Any]], max_fetch: int = 60
) -> List[Dict[str, Any]]:
    """即時向 Google Chat 取回訊息內容。

    mentions 表只存識別資訊（九節），所以顯示時要回頭抓。好處是訊息在 Chat 被
    編輯或刪除時不會顯示過期內容——代價是每次列表都要打 API，所以併發抓、
    並且設上限。
    """
    targets = rows[:max_fetch]

    def fetch(row: Dict[str, Any]):
        try:
            msg = client.get_message(row["message_name"])
            return row["id"], (msg.get("text") or "").strip(), None, msg
        except ChatPulseError as exc:
            return row["id"], None, exc.message, None
        except Exception as exc:
            return row["id"], None, str(exc), None

    contents: Dict[int, Any] = {}
    fetched: List[Dict[str, Any]] = []
    if targets:
        with futures.ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            for mid, text, err, msg in pool.map(fetch, targets):
                contents[mid] = (text, err)
                if msg:
                    fetched.append(msg)
    # 被 @ 的訊息一定含 USER_MENTION annotation，是名錄最可靠的來源
    learn_names(fetched)

    out = []
    for row in rows:
        text, err = contents.get(row["id"], (None, None))
        out.append({**row, "text": text, "content_error": err})
    return out


def _mention_public(
    row: Dict[str, Any],
    resolve=None,
    *,
    viewer_id: Optional[int] = None,
    has_draft: bool = False,
) -> Dict[str, Any]:
    sender = row.get("sender_display")
    if not sender and resolve:
        sender = resolve(row.get("sender_name"))

    # space_name 在資料表裡是**寫入當下的快照**，而私訊的名字是慢慢認出來的
    # （見 list_spaces_cached）。結果是同一個私訊的兩則 Mention 會顯示成
    # 「（私訊）」與「李小明」兩個名字，看起來像兩個不同的對話。
    # 有 viewer_id 時一律以現在的名字為準；快取查不到才退回快照。
    space_name = row.get("space_name")
    if viewer_id is not None and row.get("space_id"):
        try:
            current = space_display_name(viewer_id, row["space_id"])
            if current and current != row["space_id"]:
                space_name = current
        except Exception:
            log.exception("取 Space 顯示名稱失敗，改用寫入當下的快照")

    return {
        "id": row.get("id"),
        "space_id": row.get("space_id"),
        "space_name": space_name,
        "message_name": row.get("message_name"),
        "thread_name": row.get("thread_name"),
        "sender_display": sender or "未知成員",
        "sender_id": row.get("sender_name"),
        "create_time": row.get("create_time"),
        "state": row.get("state"),
        "resolved_at": row.get("resolved_at"),
        "text": row.get("text"),
        "content_error": row.get("content_error"),
        # 有沒有存下來的草稿。**呼叫端一定要算**（不要讓它預設 False 就送出）
        # ——這個旗標是前端決定「要不要去讀回草稿」的唯一依據，錯報 False
        # 的後果是草稿明明在資料庫裡卻永遠不會被載回來，與「草稿不見了」
        # 完全無法分辨。
        "has_draft": has_draft,
    }


@app.get("/api/v1/mentions")
def get_mentions(
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=MENTIONS_PAGE_DEFAULT, ge=cfg.LIMIT_MIN, le=cfg.LIMIT_MAX),
    with_content: bool = Query(default=True),
    viewer: Dict[str, Any] = ViewerDep,
):
    if state and state not in ("pending", "resolved"):
        raise InvalidParameter(f"state 只接受 pending／resolved，收到 {state!r}")

    rows = repo.list_mentions(viewer["id"], state=state, limit=limit)
    if with_content and rows:
        rows = _hydrate_mention_content(get_client(viewer["id"]), rows)

    # 名錄要在 hydrate 之後才建（那一步會學到新名字）
    resolve = name_resolver_for(viewer)
    # 一次查完整個 Viewer 的草稿分佈，不要每列各查一次——這裡預設就是 200 列
    with_drafts = repo.mention_ids_with_drafts(viewer["id"])
    return {
        "count": len(rows),
        "counts": repo.count_mentions(viewer["id"]),
        "mentions": [
            _mention_public(
                r, resolve, viewer_id=viewer["id"], has_draft=r.get("id") in with_drafts
            )
            for r in rows
        ],
    }


class MentionStateRequest(BaseModel):
    state: str

    @field_validator("state")
    @classmethod
    def _check(cls, v: str) -> str:
        if v not in ("pending", "resolved"):
            raise ValueError("只接受 pending 或 resolved")
        return v


@app.patch("/api/v1/mentions/{mention_id}")
def patch_mention(
    mention_id: int, req: MentionStateRequest, viewer: Dict[str, Any] = ViewerDep
):
    """手動標記狀態（6.4）。進入已處理只有兩種方式，這是其中一種。"""
    if not repo.get_mention(viewer["id"], mention_id):
        raise MentionNotFound()
    row = repo.set_mention_state(viewer["id"], mention_id, req.state)
    # 這一列也要帶對 has_draft：前端會拿這個回應覆蓋清單裡的那一列，
    # 用預設的 False 會把「這則有草稿」這件事洗掉。單筆查詢，很便宜。
    return _mention_public(
        row or {},
        name_resolver_for(viewer),
        viewer_id=viewer["id"],
        has_draft=repo.latest_draft(mention_id) is not None,
    )


@app.post("/api/v1/mentions/refresh")
def refresh_mentions(viewer: Dict[str, Any] = ViewerDep):
    """立刻跑一輪採集，不等下一個輪詢週期。"""
    results = collector_runner.run_once()
    return {"ran": True, "stats": results.get(viewer["id"])}


#: 一次最多合併幾則。上限不是技術限制，是品質限制——超過這個數量，
#: 一則回話同時回完所有問題就開始變得不像人話，該分開回了。
MERGE_MAX = 5


class CodeRefRequest(BaseModel):
    """要查哪一個專案的哪一個環境。"""

    project_id: int
    #: 省略時用專案的 default_env
    environment: Optional[str] = None
    #: 指定檔案時跳過關鍵字搜尋，直接讀這些檔（零猜測）
    paths: List[str] = Field(default_factory=list)


class DraftRequest(BaseModel):
    # 7.3：不自動選擇 Reference Space，預設空陣列
    reference_space_ids: List[str] = Field(default_factory=list)
    provider: Optional[str] = None
    limit: int = Field(default=cfg.LIMIT_DEFAULT, ge=cfg.LIMIT_MIN, le=cfg.LIMIT_MAX)

    #: 一起回的其他 Mention（收件匣多選合併）。空陣列＝只回 URL 上那一則。
    #: 限制見 resolve_merge_targets()：同一個 Space，群組還要同一個討論串。
    merge_mention_ids: List[int] = Field(default_factory=list)

    #: 要拿來當程式碼佐證的參考專案。空陣列＝不查程式碼（預設，ADR-0006）。
    #: **環境是綁在每一筆上、不是全域一個**——送同一個 project_id 兩次配不同
    #: environment，就是「比對正式與 UAT」，那是這個功能最有價值的用法。
    #: 原本的 `code_project_ids` ＋ 單一 `code_environment` 做不到那件事
    #: （id 會被去重、環境又只有一個），儘管 CODE_MAX_PROJECTS_PER_DRAFT=2
    #: 的註解正是為了它而設。
    code_refs: List["CodeRefRequest"] = Field(default_factory=list)
    #: 覆寫自動抽出的搜尋關鍵字。自動抽詞是刻意做弱的（見 code_search
    #: 的說明），猜錯時使用者可以直接指定
    code_terms: List[str] = Field(default_factory=list)

    # ---- 回覆設定（ADR-0007）。全部選填，省略時走 Viewer 偏好或系統預設。 ----
    #
    # `docs/draft-context-design.md`（C-7）曾決定「DraftRequest 不新增欄位」。
    # 那條決策針對的是**脈絡窗口大小**這類參數：`limit` 已被 Reference Space
    # 佔用，再加一個主窗參數會讓前端「每群抓取則數」的標籤變成靜默錯誤，
    # 所以第一版的脈絡旋鈕全走 config 常數。
    #
    # 下面四個欄位不屬於那一類，理由是它們**必須由使用者逐次選擇**：
    # 同一個人早上回 PM 要用「專業正式」、下午回工程師要用「工程師協作」，
    # 走 config 常數表達不了「這一次要用哪個」。它們也不影響任何既有欄位的
    # 語意（不碰 limit、不碰脈絡形狀），因此不會產生 C-7 擔心的靜默錯誤。
    #
    #: 回覆口氣。`None`＝用 Viewer 偏好，偏好也沒有就完全不介入（見 ADR-0007
    #: 的向後相容一節：不選 tone 的產出與這個功能存在之前逐字相同）。
    tone_id: Optional[str] = None
    #: 要套用的 Persona。`None`＝用 Viewer 偏好；`0` 是明確的「這次不用」。
    persona_id: Optional[int] = None
    #: 這一次直接輸入的自訂提示（inline）。優先於 `custom_prompt_id`。
    custom_prompt: Optional[str] = None
    #: 要套用的已存提示詞 preset。`custom_prompt` 有值時忽略這個。
    custom_prompt_id: Optional[int] = None
    #: 要不要跑 Sepia 潤稿。`None`＝用 Viewer 偏好，偏好也沒有就不潤。
    sepia_enabled: Optional[bool] = None


#: `persona_id` / `custom_prompt_id` 用 0 表達「這一次明確不使用」。
#:
#: 需要這個哨兵是因為 `None` 已經被「沿用 Viewer 偏好」佔用了：Viewer 設了
#: 預設 Persona 之後，「這次不要用 Persona」沒有別的方式可以表達——
#: 送 `null` 會被當成「照偏好來」，於是使用者關不掉它。
#: 0 不可能是合法的 AUTOINCREMENT id，所以拿它當哨兵不會與真實資料衝突。
NONE_ID = 0


def resolve_merge_targets(
    viewer_id: int, primary: Dict[str, Any], merge_ids: List[int]
) -> List[Dict[str, Any]]:
    """驗證並取回「一起回」的其他 Mention，回傳照時間排序的完整清單（含主要那則）。

    三條限制，每一條都會讓合併變成錯的：

    1. **同一個 Space。** 回話只送得到一個 Space，跨群合併等於有人收不到回覆。
    2. **群組還要同一個討論串。** 回話帶 thread_name 送出，兩則在不同串時
       其中一則的提問者根本看不到你的回覆——而且送出會「成功」，沒有任何錯誤。
       私訊／不分串聊天室沒有這個限制（那裡的 thread 不是對話單位）。
    3. **不能是已處理的。** 合併會把它們全部標成已處理，把已經回過的再結一次
       是無害的，但把「已處理」重新算進待辦數字會讓計數失真。

    前端會事先把不符合的項目變成不可勾選，但這裡照樣要驗——前端的規則
    是為了好用，不是為了正確。
    """
    if not merge_ids:
        return [primary]

    ids = [i for i in dict.fromkeys(merge_ids) if i != primary["id"]]
    if len(ids) + 1 > MERGE_MAX:
        raise InvalidParameter(
            f"一次最多合併 {MERGE_MAX} 則，收到 {len(ids) + 1} 則。"
            "要回的事情太多時，分兩次回會比一則塞滿更清楚。"
        )

    flat = draft_context.is_flat_space(*space_shape(viewer_id, primary["space_id"]))
    rows: List[Dict[str, Any]] = [primary]
    for mid in ids:
        row = repo.get_mention(viewer_id, mid)
        if not row:
            raise MentionNotFound(f"找不到要合併的 Mention {mid}")
        if row["space_id"] != primary["space_id"]:
            raise InvalidParameter(
                f"Mention {mid} 不在同一個聊天室，無法合併成一則回話"
            )
        if not flat and row.get("thread_name") != primary.get("thread_name"):
            raise InvalidParameter(
                f"Mention {mid} 在不同的討論串。回話只會送到其中一串，"
                "另一串的提問者看不到——請分開回覆。"
            )
        if row.get("state") == "resolved":
            raise InvalidParameter(f"Mention {mid} 已經處理過了，不用再回一次")
        rows.append(row)

    rows.sort(key=lambda r: r.get("create_time") or "")
    return rows


def _code_block(ctx: "code_search.CodeContext") -> Dict[str, Any]:
    """CodeContext -> prompts._code_section 吃的 dict。"""
    return {
        "project_name": ctx.project_name,
        "environment": ctx.environment,
        "environment_label": code_search.environment_label(ctx.environment),
        "branch": ctx.branch,
        "commit_sha": ctx.commit_sha,
        "commit_date": ctx.commit_date,
        "terms": list(ctx.terms),
        "notes": list(ctx.notes),
        # 預算吃滿時有片段被丟掉。前端要標出來——不然使用者以為看到的是全部
        "truncated": ctx.truncated,
        "hits": [
            {
                "path": h.path,
                "start_line": h.start_line,
                "end_line": h.end_line,
                "text": h.text,
            }
            for h in ctx.hits
        ],
    }


def resolve_code_refs(
    viewer_id: int, refs: List[CodeRefRequest]
) -> List[Tuple[Dict[str, Any], str, str, List[str]]]:
    """把 `code_refs` 解析成 (專案, 環境, 分支, 指定檔案)，**在 SSE 開始之前**。

    專案不見了、環境沒有對應分支——這些是參數錯誤，要用一般的 4xx 回，
    不要變成 SSE 的 error 事件。SSE 一旦開始就是 HTTP 200，前端得另外處理
    一種「串流開了但其實沒開始」的狀態，而使用者也比較不容易注意到。
    真正會慢的 git grep 留在 generator 裡跑。

    回傳的順序＝請求的順序，**不去重**：同一個 project_id 出現兩次配不同環境
    正是「比對正式與 UAT」，去重就把這個功能拿掉了。
    """
    if not cfg.CODE_ENABLED or not refs:
        return []

    out: List[Tuple[Dict[str, Any], str, str, List[str]]] = []
    for ref in refs[: cfg.CODE_MAX_PROJECTS_PER_DRAFT]:
        project = repo.get_code_project(viewer_id, ref.project_id)
        if project is None:
            raise CodeProjectNotFound(f"找不到參考專案 id={ref.project_id}")
        if not project.get("enabled", True):
            raise InvalidParameter(f"參考專案「{project['name']}」已停用")
        env = _env(ref.environment or project["default_env"])
        branch = (project.get("branches") or {}).get(env)
        if not branch:
            raise InvalidParameter(
                f"專案「{project['name']}」沒有設定 {env} 環境對應的分支，請先到設定頁補上"
            )
        out.append((project, env, branch, list(ref.paths)))
    return out


def collect_code_context(
    resolved: List[Tuple[Dict[str, Any], str, str, List[str]]],
    terms: List[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """對已解析的參考專案跑 git grep。回傳 (code_blocks, skipped)。

    沒指定專案就回空的——「沒查」與「查了沒找到」對模型是兩種不同的事實，
    prompts._code_section 會分別講清楚，這裡不要混為一談。

    路徑或分支不存在會往外拋（CodeRepoNotFound／CodeBranchNotFound），
    由呼叫端轉成 SSE error 事件。那是硬失敗而不是降級：使用者要的就是
    有依據的草稿，靜默給一份沒依據的更糟。
    """
    blocks: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for project, env, branch, paths in resolved:
        ctx, ctx_skipped = code_search.collect(
            repo_path=project["repo_path"],
            project_name=project["name"],
            environment=env,
            branch=branch,
            terms=terms,
            include_globs=project.get("include_globs") or (),
            exclude_globs=project.get("exclude_globs") or (),
            budget_tokens=cfg.CODE_BUDGET_TOKENS_DRAFT,
            explicit_paths=paths,
        )
        blocks.append(_code_block(ctx))
        skipped.extend(ctx_skipped)
    return blocks, skipped


#: 自訂提示的長度上限（字元）。
#:
#: 「這一次的回話要怎麼寫」用 2000 字綽綽有餘；超過通常是誤貼了一整份文件。
#: 上限同時也是成本與注入面積的控制——這段文字會原封不動進 prompt。
MAX_CUSTOM_PROMPT_CHARS = 2000


def resolve_reply_options(
    viewer_id: int, req: DraftRequest
) -> Tuple[reply_profiles.ReplyGenerationOptions, bool, Dict[str, Any]]:
    """解析這一次草稿的回覆設定，回 `(options, sepia_enabled, meta)`。

    ## 優先序

        Per Draft Override  >  Viewer Preference  >  System Default

    刻意**不套** Global／Conversation／Message 那種三層模型——ChatPulse
    沒有「Conversation Session」這種 domain object，硬套會產生一層沒有
    對應實體的設定，而那層設定要存在哪裡、什麼時候失效都答不出來。

    ## 兩種「找不到」要用不同方式處理

    這是這個函式最容易寫錯的地方：

    * **這一次明確指定的 id 找不到** → 拋 404。使用者剛剛選的東西不存在，
      那是他需要知道的事，靜默忽略會讓他以為 Persona 生效了。
    * **Viewer 偏好裡的 id 找不到** → 降級成「不使用」並記 log。那通常是
      persona 被刪掉而偏好沒清乾淨（欄位刻意沒有外鍵，見 `core/db.py`），
      拿它去擋住產草稿等於讓一筆過期的偏好把功能鎖死。

    同一條判準也適用於 tone：這次傳的值不合法要擋（400），
    偏好裡存的值不合法就降級（可能是那個 tone 在版本更新後被移除了）。
    """
    prefs = repo.get_preferences(viewer_id)
    meta: Dict[str, Any] = {}

    # ---------------------------------------------------------------- tone
    tone: Optional[str] = None
    if req.tone_id is not None:
        # 這次明確指定 → 不合法就擋（400）
        tone = reply_profiles.validate_tone(req.tone_id)
    elif prefs.get("default_reply_tone"):
        try:
            tone = reply_profiles.validate_tone(prefs["default_reply_tone"])
        except InvalidParameter:
            log.warning(
                "Viewer %s 的偏好 default_reply_tone=%r 已不是合法值，這次略過",
                viewer_id,
                prefs.get("default_reply_tone"),
            )
            tone = None
    # 兩者都沒有 → tone 保持 None，prompt 完全不加風格區塊（向後相容）

    # ---------------------------------------------------------------- persona
    profile: Optional[personas.PersonaProfile] = None
    persona_row: Optional[Dict[str, Any]] = None
    if req.persona_id == NONE_ID:
        pass  # 這一次明確不使用
    elif req.persona_id is not None:
        persona_row = repo.get_persona(viewer_id, req.persona_id)
        if persona_row is None:
            raise PersonaNotFound(f"找不到 Persona {req.persona_id}")
        if not persona_row["enabled"]:
            raise PersonaInvalid(
                f"Persona「{persona_row['name']}」已停用，請先啟用或改選其他 Persona"
            )
    elif prefs.get("default_persona_id"):
        persona_row = repo.get_persona(viewer_id, prefs["default_persona_id"])
        if persona_row is None or not persona_row["enabled"]:
            log.info(
                "Viewer %s 的預設 Persona %s 已不存在或已停用，這次略過",
                viewer_id,
                prefs.get("default_persona_id"),
            )
            persona_row = None

    if persona_row is not None:
        # 從 DB 讀回來時**再跑一次淨化**（`from_json` 內建），不直接信任
        # 落地的 profile_json——淨化規則會演進，而且有人可能直接改過 DB。
        profile = personas.PersonaProfile.from_json(
            json.dumps(persona_row["profile"], ensure_ascii=False)
        )
        # 進 prompt 的名字一律以 `personas.name` 為準，不用 profile_json 裡的。
        #
        # 兩者會分歧：改名只更新 `personas.name`，profile_json 內嵌的 name 是
        # 匯入當時從來源抽出的原始值（常是 skill 識別字或真人姓名，例如
        # `luozhenyu-perspective`／`罗振宇`）。而「改名」正是使用者想把那個
        # 識別身份換掉時會做的動作——他改完會以為 prompt 裡不再提到那個人，
        # 實際上 prompt 用的是 profile_json 那份，永遠不會變。
        #
        # 這條分歧沒有任何外顯訊號：UI 與 meta 顯示新名字，送進模型的是舊的，
        # 而 prompt 不外顯，使用者無法從產出察覺。所以以 row 為單一事實來源。
        row_name = personas.clean_display_name(persona_row["name"])
        if row_name and row_name != profile.name:
            profile = dataclasses.replace(profile, name=row_name)
        if not profile.is_usable():
            log.info(
                "Persona %s（%s）淨化後沒有可用內容，這次略過",
                persona_row["id"],
                persona_row["name"],
            )
            profile = None
        else:
            meta["persona_id"] = persona_row["id"]
            meta["persona_name"] = persona_row["name"]

    # ------------------------------------------------------------- custom prompt
    custom_text: Optional[str] = None
    used_prompt_id: Optional[int] = None
    inline = (req.custom_prompt or "").strip()
    if inline:
        # inline 優先於 preset：使用者在輸入框打的字是「這一次」最明確的意圖
        if len(inline) > MAX_CUSTOM_PROMPT_CHARS:
            raise InvalidParameter(
                f"自訂提示過長（{len(inline)} 字，上限 {MAX_CUSTOM_PROMPT_CHARS} 字）"
            )
        custom_text = inline
    elif req.custom_prompt_id == NONE_ID:
        pass  # 這一次明確不使用
    elif req.custom_prompt_id is not None:
        preset = repo.get_reply_prompt(viewer_id, req.custom_prompt_id)
        if preset is None:
            raise ReplyPromptNotFound(f"找不到回覆提示詞 {req.custom_prompt_id}")
        custom_text = (preset["prompt"] or "").strip() or None
        used_prompt_id = preset["id"]
    elif prefs.get("default_reply_prompt_id"):
        preset = repo.get_reply_prompt(viewer_id, prefs["default_reply_prompt_id"])
        if preset is None:
            log.info(
                "Viewer %s 的預設回覆提示詞 %s 已不存在，這次略過",
                viewer_id,
                prefs.get("default_reply_prompt_id"),
            )
        else:
            custom_text = (preset["prompt"] or "").strip() or None
            used_prompt_id = preset["id"]

    # ---------------------------------------------------------------- sepia
    if req.sepia_enabled is not None:
        sepia_enabled = bool(req.sepia_enabled)
    elif prefs.get("default_sepia_enabled") is not None:
        sepia_enabled = bool(prefs["default_sepia_enabled"])
    else:
        sepia_enabled = False  # 系統預設不潤稿（向後相容）

    # 擋在 SSE 開始之前：串流一旦開始就是 HTTP 200，之後只能發 error 事件。
    # 這裡只驗規則檔（不需要 AI 供應商），供應商的檢查留在 generator 內。
    if sepia_enabled:
        ok, reason = sepia_polisher.rules_available()
        if not ok:
            raise SepiaUnavailable(
                f"{reason} 你可以關閉 Sepia 潤稿後重新產生草稿。"
            )

    if tone:
        meta["tone"] = tone
        meta["tone_label"] = reply_profiles.tone_label(tone)
    # 只記「有沒有」與「是哪一筆 preset」，**不把自訂提示全文放進 meta**：
    # 那是使用者輸入，沒有必要出現在 SSE 事件與 devtools 裡。
    if custom_text:
        meta["custom_prompt"] = True
        if used_prompt_id is not None:
            meta["custom_prompt_id"] = used_prompt_id
    meta["sepia"] = sepia_enabled

    options = reply_profiles.ReplyGenerationOptions(
        tone=tone,
        custom_prompt=custom_text,
        persona=profile,
    )
    return options, sepia_enabled, meta


@app.get("/api/v1/mentions/{mention_id}/draft")
def get_stored_draft(mention_id: int, viewer: Dict[str, Any] = ViewerDep):
    """取回這一則**已經存下來**的最新草稿。

    為什麼需要這個端點：草稿一直都有存進 `draft_replies`，但在這之前沒有
    任何路徑把它讀回來——`repo.latest_draft()` 寫好了卻零呼叫者。於是重新
    整理、切回收件匣再點進來、或隔天再開，畫面都是空的，看起來像草稿沒了。
    實際上它在資料庫裡（本機實測 53 筆）。

    **`generation_config` 不等於產生當下的完整 meta。** 存下來的只有
    `{provider, model} ＋ reply_meta ＋ polish_meta`——也就是證據欄的
    「生成」「回話設定」「潤稿」三列。脈絡（讀了幾則、涵蓋範圍、時間範圍）、
    參考 Space、程式碼佐證、合併回覆對象**沒有存**，所以還原不了。
    前端要把這件事明講（見 `toEvidence` 的 `restored`），不可以讓一份
    只有一半證據的草稿看起來像完整的——這個分支整個設計前提就是
    「證據要對得上」，半套的證據比沒有更糟。
    """
    viewer_id = viewer["id"]
    if not repo.get_mention(viewer_id, mention_id):
        raise MentionNotFound()

    row = repo.latest_draft(mention_id)
    if not row:
        raise DraftNotFound(f"Mention {mention_id} 還沒有存下來的草稿")

    raw = row.get("generation_config_json")
    try:
        config = json.loads(raw) if raw else {}
    except ValueError:
        # 存壞的設定不該讓整個草稿讀不回來——內文才是主角
        log.warning("Draft %s 的 generation_config_json 不是合法 JSON", row.get("id"))
        config = {}

    return {
        "draft_id": row.get("id"),
        "mention_id": mention_id,
        "content_md": row.get("content_md") or "",
        "generation_config": config,
        "created_at": row.get("created_at"),
        "sent_at": row.get("sent_at"),
    }


@app.post("/api/v1/mentions/{mention_id}/draft/stream")
def draft_stream(
    mention_id: int, req: DraftRequest, viewer: Dict[str, Any] = ViewerDep
):
    """產生 Draft Reply SSE（七節）。

    流程：取回該討論串完整對話 → 併入 Viewer 勾選的 Reference Space 近期訊息
    → 送選定的 AI 供應商串流輸出兩段（脈絡分析、建議回話）。
    """
    viewer_id = viewer["id"]
    mention = repo.get_mention(viewer_id, mention_id)
    if not mention:
        raise MentionNotFound()

    for sid in req.reference_space_ids:
        if not sid.startswith("spaces/"):
            raise InvalidParameter(f"reference_space_ids 含非法值 {sid!r}")

    ref_ids = list(dict.fromkeys(req.reference_space_ids))  # 去重、保留順序
    limit = req.limit
    resolved_provider = providers.resolve_name(req.provider)
    req_provider = req.provider
    # 在進 generator 之前驗證：SSE 一旦開始就是 HTTP 200，之後的錯誤只能變成
    # error 事件，使用者比較難注意到。參數錯誤要用正常的 4xx 擋在門外。
    targets = resolve_merge_targets(viewer_id, mention, req.merge_mention_ids)
    resolved_code_refs = resolve_code_refs(viewer_id, req.code_refs)
    # 回覆設定（ADR-0007）也在這裡解析：不合法的 tone、不存在的 Persona／
    # preset、以及「要 Sepia 但規則沒安裝」都要變成 4xx，不是 error 事件。
    reply_options, sepia_enabled, reply_meta = resolve_reply_options(viewer_id, req)

    def generate() -> Generator[str, None, None]:
        # 放在 try 外面：客戶端中途斷線時 yield 會拋 GeneratorExit，
        # finally 仍要看得到已收到的內容才補存得了（見 save_partial）
        collected: List[str] = []
        saved = False
        # 與 `collected` 同樣放在 try 外面：斷線補存那條路在 finally 裡，
        # 而 meta 是在 try 中段才組好的——沒有這個初始值，「meta 還沒組好就
        # 出錯」會讓 finally 自己噴 NameError，把真正的錯誤蓋掉。
        meta_event: Optional[Dict[str, Any]] = None
        try:
            client = get_client(viewer_id)
            ai = get_provider(viewer_id, req_provider)

            # 被 @ 的那則訊息本身；多選合併時還有其他幾則要一起回
            anchor_msgs = [client.get_message(t["message_name"]) for t in targets]
            mention_msg = next(
                (m for m in anchor_msgs if m.get("name") == mention["message_name"]),
                anchor_msgs[0],
            )
            mention_text = (mention_msg.get("text") or "").strip() or "（訊息內容已被刪除或無法取回）"
            learn_names(anchor_msgs)

            # 脈絡：形狀由 Space 的結構語意決定，不是由「撈回來剛好幾則」決定
            # （core/draft_context.py 有完整理由與實測分布）。
            space_type, threading_state = space_shape(viewer_id, mention["space_id"])

            def _learn_then_resolve(msgs: List[Dict[str, Any]]):
                """脈絡訊息取回來之後、組成文字之前：先從它們學名字。

                順序不能顛倒——Google 不回 displayName，名字要從這批訊息的
                mention annotation 現學，晚一步的話發言者全部會是「未知成員」。
                """
                learn_names(msgs)
                return name_resolver_for(viewer)

            resolve = name_resolver_for(viewer)
            ctx = draft_context.build(
                client,
                space_id=mention["space_id"],
                space_type=space_type,
                threading_state=threading_state,
                anchor_msg=mention_msg,
                extra_anchor_msgs=[m for m in anchor_msgs if m is not mention_msg],
                thread_name=mention.get("thread_name"),
                resolve=resolve,
                on_retrieved=_learn_then_resolve,
                # 有了自己的 id，「要回哪幾則」才判得出來——判準是
                # 「從我上次發言到現在，對方講了什麼我還沒回」
                self_user_id=viewer.get("google_user_id"),
            )
            resolve = name_resolver_for(viewer)

            mention_sender = (
                (mention_msg.get("sender") or {}).get("displayName")
                or resolve((mention_msg.get("sender") or {}).get("name"))
                or mention.get("sender_display")
                or "未知成員"
            )

            # Reference Space（7.1）
            ref_blocks = []
            for sid in ref_ids:
                msgs = client.fetch_recent_messages(sid, limit=limit)
                learn_names(msgs)
                text = format_conversation(msgs, name_resolver_for(viewer))
                if not text.strip():
                    continue
                ref_blocks.append(
                    {
                        "space_id": sid,
                        "space_name": space_display_name(viewer_id, sid),
                        "message_count": len(msgs),
                        "conversation_text": text,
                    }
                )

            # 圖片：**只取被 @ 的那則與其脈絡**，Reference Space 不取。
            # 理由是成本——參考群組可能有好幾個、每個 50 則，圖片全抓會爆掉預算；
            # 而使用者真正需要看到的，是「@ 我的那則自己帶的截圖」
            # （實測工作群組最常見的形態就是「@某人 ＋ 一張截圖」）。
            #
            # 吃的是 `ctx.image_messages` 而不是文字脈絡那份清單：兩者**必須解耦**。
            # 在此之前這裡吃的就是 thread_msgs，所以把文字脈絡放大就會靜默放大
            # 圖片的取樣母體——而那一行 diff 上完全看不出改動。
            # scan_recent 也要明示：預設的 30 是為「摘要 500 則」設計的數字。
            images: List = []
            skipped_images: List[str] = []
            if ai.supports_vision:
                images, skipped_images = attachments.collect(
                    client.download_attachment,
                    ctx.image_messages,
                    space_id=mention["space_id"],
                    budget_tokens=cfg.IMAGE_BUDGET_TOKENS_DRAFT,
                    # 整串連發都優先，不是只有錨點那一則
                    priority_message_names=[
                        m["name"] for m in ctx.anchor_run if m.get("name")
                    ]
                    or [mention["message_name"]],
                    scan_recent=len(ctx.image_messages),
                )

            # 參考專案原始碼（ADR-0006）。這一步會跑 git grep，可能要幾秒，
            # 所以刻意放在 meta **之前**：meta 一送出，前端的檢索結果條就能顯示
            # 「查了哪個環境／哪個 commit／哪些關鍵字／命中哪些檔案」。
            # 搜錯環境、搜錯關鍵字，使用者在模型開口之前就看得到——
            # 那是保住 human-in-the-loop 的機制，不只是資訊展示。
            # 沒選參考專案時這裡完全不跑，meta 也就不會被拖慢。
            code_terms = [t.strip() for t in req.code_terms if t.strip()] or (
                code_search.extract_search_terms(
                    ctx.anchor_plain_text or mention_text, ctx.search_text
                )
                if resolved_code_refs
                else []
            )
            code_blocks, code_skipped = collect_code_context(
                resolved_code_refs, code_terms
            )

            # 先組好再送，因為**同一份**要一起存進 `generation_config_json`。
            #
            # 存「送給瀏覽器的那一份」而不是另外組一份，有兩個理由：
            #   1. 不會多洩漏任何東西——這份內容瀏覽器本來就收到了，
            #      而它已經過濾過（例如自訂提示只記「有沒有」不記全文，
            #      見 `resolve_reply_options`）。
            #   2. 不會有第二份組裝邏輯可以跟這裡漂移。
            # 少了這一步，重新載入的草稿就只還原得了「生成／回話設定／潤稿」
            # 三列，脈絡與參考來源永遠回不來（見 `get_stored_draft`）。
            meta_event = {
                    "type": "meta",
                    "mention_id": mention_id,
                    "space": mention.get("space_name") or mention["space_id"],
                    # 保留舊欄位：前端與 e2e 都在讀它，而它的語意（這次送進模型的
                    # 對話則數）沒有變，只是來源從「該討論串」變成「這次的脈絡」。
                    "thread_message_count": ctx.message_count,
                    "context": ctx.to_meta(),
                    # 這次的回話會結掉哪幾則。前端要拿它去送出與標記已處理——
                    # 讓伺服器回報自己實際採用了什麼，而不是讓前端沿用送出前的勾選，
                    # 兩邊分歧時使用者會以為某則回過了、其實沒有。
                    "answering": [
                        {
                            "mention_id": t["id"],
                            "sender_display": t.get("sender_display"),
                            "create_time": t.get("create_time"),
                        }
                        for t in targets
                    ],
                    "reference_spaces": [
                        {
                            "space_id": b["space_id"],
                            "space_name": b["space_name"],
                            "message_count": b["message_count"],
                        }
                        for b in ref_blocks
                    ],
                    # 讓 Viewer 在模型開口**之前**就看到依據對不對。
                    # hit_count 為 0 代表「查了但沒找到」，與「沒有查」是不同的事——
                    # 前端與 prompt 都必須分得出來。
                    "code_refs": [
                        {
                            "project_name": b["project_name"],
                            "environment": b["environment"],
                            "environment_label": b["environment_label"],
                            "branch": b["branch"],
                            "commit_sha": b["commit_sha"],
                            "commit_date": b["commit_date"],
                            "terms": b["terms"],
                            "hit_count": len(b["hits"]),
                            "files": sorted({h["path"] for h in b["hits"]}),
                            "truncated": b["truncated"],
                            "notes": b["notes"],
                        }
                        for b in code_blocks
                    ],
                    "code_skipped": code_skipped,
                    "provider": resolved_provider,
                    "model": ai.model,
                    "image_count": len(images),
                    "images_skipped": skipped_images,
                    # 這次套用的回覆設定（ADR-0007）。與 code_refs 同一個理由：
                    # 讓 Viewer 在模型開口之前就看得到「系統以為我選了什麼」。
                    "reply": reply_meta,
            }
            yield sse(meta_event)

            prompt = prompts.draft_reply_prompt(
                anchor_text=ctx.anchor_text or mention_text,
                mention_sender=mention_sender,
                space_name=mention.get("space_name") or mention["space_id"],
                space_type_label=ctx.space_type_label,
                context_blocks=[b.to_prompt_dict() for b in ctx.blocks],
                coverage=ctx.coverage,
                anchor_count=ctx.anchor_count,
                reference_blocks=ref_blocks,
                code_blocks=code_blocks or None,
                reply_options=reply_options,
            )

            for chunk in ai.stream_text(prompt, operation="draft_reply", images=images):
                collected.append(chunk)
                yield sse({"type": "chunk", "text": chunk})

            content = "".join(collected)

            # ---- 潤稿（ADR-0007）。只潤〈建議回話〉，脈絡分析逐字保留。 ----
            polish_meta: Dict[str, Any] = {}
            final_reply: Optional[str] = None
            if sepia_enabled and content.strip():
                sections = prompts.split_draft(content)
                if not sections.found:
                    # 模型沒照輸出格式回。潤整篇會改到脈絡分析（連程式碼佐證
                    # 一起改），所以不潤——這是降級，不是錯誤。
                    polish_meta = {
                        "polisher": "sepia",
                        "polished": False,
                        "fallback_reason": "草稿裡找不到〈建議回話〉章節，"
                        "為避免改動脈絡分析而略過潤稿",
                    }
                    log.info("Draft %s 找不到建議回話章節，略過潤稿", mention_id)
                else:
                    polisher = polishers.resolve("sepia", provider=ai)
                    result = polisher.polish(
                        polishers.PolishRequest(
                            reply=sections.reply,
                            tone=reply_options.tone,
                            tone_instruction=(
                                reply_profiles.tone_instruction(reply_options.tone)
                                if reply_options.tone
                                else None
                            ),
                            persona=(
                                reply_options.persona.to_prompt_dict()
                                if reply_options.persona is not None
                                else None
                            ),
                            custom_instruction=reply_options.custom_prompt,
                        )
                    )
                    polish_meta = result.to_meta()
                    if result.polished:
                        content = sections.reassemble(result.text)
                        final_reply = result.text
                    else:
                        # 完整性檢查沒過（或模型沒照契約回）→ 用未潤稿的版本，
                        # 但**必須**讓使用者知道。靜默退回會讓他以為潤過了。
                        log.warning(
                            "Draft %s 的 Sepia 潤稿未採用：%s",
                            mention_id,
                            result.fallback_reason,
                        )

            generation_config = {
                "provider": resolved_provider,
                "model": ai.model,
                **reply_meta,
                **polish_meta,
                # 整份 meta，讓重新載入時證據欄能完整還原（不只三列）。
                # 上面那些平鋪的鍵**刻意保留**：2026-09-11 之前產生的草稿只有
                # 平鋪版本，讀回來時還得靠它們，不能因為有了 meta 就拿掉。
                "meta": meta_event,
            }
            draft_id = (
                repo.create_draft(mention_id, content, generation_config)
                if content.strip()
                else None
            )
            saved = True
            # `reply` 帶潤稿後的建議回話全文。這不是 UX 裝飾——潤稿後 DB 存的
            # 與前端串流累積的會不一致，而使用者按「送出」時送的是前端那一份。
            # 沒有這個欄位，開了 Sepia 就會把**未潤稿**的版本送到 Google Chat。
            yield sse(
                {
                    "type": "done",
                    "draft_id": draft_id,
                    "reply": final_reply,
                    "polish": polish_meta or None,
                }
            )
        except ChatPulseError as exc:
            yield sse(exc.to_sse_event())
        except Exception as exc:
            log.exception("Draft Reply 串流失敗")
            yield sse(
                {"type": "error", "code": "INTERNAL_ERROR", "message": f"草稿產生失敗：{exc}"}
            )
        finally:
            if not saved:
                # 斷線補存也要帶設定，否則「串流中斷的那些草稿」會是唯一
                # 答不出「當時用什麼語氣產的」的一批。標 partial 讓它與
                # 正常完成的草稿分得出來——這一份沒有經過潤稿。
                partial_config = {
                    "provider": resolved_provider,
                    **reply_meta,
                    "partial": True,
                    "polished": False,
                }
                # meta 已經送給瀏覽器了就一起存——中斷的草稿同樣讀得回完整
                # 證據。還沒組好（更早就出錯）就不放，讀回來時那幾列會照
                # 既有邏輯畫成「沒有保存」。
                if meta_event is not None:
                    partial_config["meta"] = meta_event
                save_partial(
                    lambda text: repo.create_draft(mention_id, text, partial_config),
                    "草稿",
                    "".join(collected),
                )

    return sse_response(generate())


class ReplyRequest(BaseModel):
    text: str
    draft_id: Optional[int] = None
    #: 這則回話同時回掉的其他 Mention（草稿的 meta.answering 帶回來的）。
    #: 送出成功後一起標成已處理。
    merge_mention_ids: List[int] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("回話內容不可為空")
        return v


@app.post("/api/v1/mentions/{mention_id}/reply")
def reply_to_mention(
    mention_id: int, req: ReplyRequest, viewer: Dict[str, Any] = ViewerDep
):
    """送出回話（7.2 步驟 6）。

    以 Viewer 身分回到**原討論串**，成功後該 Mention 自動標記為已處理——
    這是 6.4 中「進入已處理」的第一種方式。

    合併回覆時（`merge_mention_ids`）只送**一則**訊息，但把被合併的那幾則
    一起標成已處理。驗證與草稿那一側共用 `resolve_merge_targets()`，
    兩邊規則若各寫一份，遲早會出現「草稿合得起來、送出卻結不掉」的分歧。
    """
    viewer_id = viewer["id"]
    mention = repo.get_mention(viewer_id, mention_id)
    if not mention:
        raise MentionNotFound()

    targets = resolve_merge_targets(viewer_id, mention, req.merge_mention_ids)

    res = get_client(viewer_id).send_message(
        mention["space_id"], req.text, thread_name=mention.get("thread_name")
    )
    if req.draft_id:
        # 帶 viewer_id 驗擁有權：draft_id 來自 request body，不驗的話
        # Viewer A 可以把 Viewer B 的草稿標成已送出
        if not repo.mark_draft_sent(viewer_id, req.draft_id):
            log.warning(
                "Viewer %s 送出時帶的 draft_id=%s 不屬於他，已略過標記",
                viewer_id,
                req.draft_id,
            )

    # 訊息已經送出去了，收不回來。這裡任何一則標記失敗都不該讓整個請求變成
    # 500——那會讓使用者以為沒送出而再送一次，對方就收到兩則。
    resolver = name_resolver_for(viewer)
    # 這條路徑上的 Mention **一定**有草稿（剛剛才送出去），所以不能讓
    # has_draft 用預設的 False——前端會拿這些列覆蓋清單，洗掉之後就再也
    # 讀不回那份草稿了。一次查完整組，不在迴圈裡逐筆查。
    with_drafts = repo.mention_ids_with_drafts(viewer_id)
    updated_rows = []
    for t in targets:
        try:
            row = repo.set_mention_state(viewer_id, t["id"], "resolved")
            if row:
                updated_rows.append(
                    _mention_public(
                        row,
                        resolver,
                        viewer_id=viewer_id,
                        has_draft=row.get("id") in with_drafts,
                    )
                )
        except Exception:
            log.exception("回話已送出，但 Mention %s 標記已處理失敗", t["id"])

    primary = next(
        (m for m in updated_rows if m.get("id") == mention_id),
        updated_rows[0] if updated_rows else {},
    )
    return {
        "status": "success",
        "message_id": res.get("name"),
        "createTime": res.get("createTime"),
        "thread_name": (res.get("thread") or {}).get("name"),
        # mention 保留舊欄位（單則送出的既有呼叫端還在讀它）；
        # mentions 是這次實際結掉的全部
        "mention": primary,
        "mentions": updated_rows,
    }


# ==========================================================================
# 維運
# ==========================================================================


@app.get("/api/v1/health")
def health():
    try:
        active_provider = providers.resolve_name()
    except Exception as exc:
        active_provider = f"（無可用供應商：{exc}）"
    return {
        "status": "ok",
        "db": db.journal_mode(),
        "ai_provider_default": providers.default_name(),
        "ai_provider_active": active_provider,
        "gemini_configured": bool(cfg.GEMINI_API_KEY),
        "collector_running": collector_runner.is_running(),
        "collector_implementation": collector_runner.collector_name,
        "viewer_count": len(repo.list_viewers()),
    }


@app.get("/api/v1/usage")
def get_usage(
    days: int = Query(default=14, ge=1, le=90), viewer: Dict[str, Any] = ViewerDep
):
    """R-2：**本人**每日 token 用量，累積兩週後評估成本。

    帶 viewer_id 查詢——用量看得出「誰哪幾天在用、用了多少」，
    與 ADR-0002 對 Summary 的私有標準一致。
    """
    return {"usage": repo.daily_token_usage(viewer["id"], days=days)}


# ==========================================================================
# 前端托管
# ==========================================================================

if os.path.isdir(os.path.join(FRONTEND_DIST, "assets")):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")),
        name="assets",
    )
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# 這段提示現在是最後一道網。正常情況看不到它——建置產物（dashboard/frontend/dist）
# 已進版控，clone 就有；而啟動器會在啟動前檢查，缺了會在**終端視窗**講清楚並且
# 不開瀏覽器。會讀到這段的人，多半是繞過啟動器自己跑 uvicorn，或把 dist 清掉了。
_BUILD_HINT = (
    "ChatPulse API 正在執行，但找不到前端畫面（dashboard/frontend/dist/index.html）。\n"
    "\n"
    "最省事的解法是改用啟動器，它會處理好這件事：\n"
    "  ./chatpulse.sh web          （Windows：chatpulse.bat web）\n"
    "\n"
    "畫面的建置產物本來就在版控裡，clone 下來就該有。會缺通常是被清掉了，\n"
    "用 git restore dashboard/frontend/dist 可以還原。\n"
    "\n"
    "要自己重建的話（需要 Node.js）：\n"
    "  cd dashboard/frontend && npm install && npm run build\n"
    "改前端時也可以另起 Vite dev server：\n"
    "  cd dashboard/frontend && npm run dev   # 然後開 http://localhost:5173"
)


@app.get("/")
def serve_index():
    """送 React 建置產物。

    刻意**不**退回 `dashboard/frontend/index.html`——Phase 1 之後那個檔案是
    Vite 的開發入口（內容只有一個 `<div id="root">` 加 `/src/main.tsx` 的
    module script），由 FastAPI 直接送出只會得到一片空白頁，比明確的錯誤訊息更難查。
    """
    dist_index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    return Response(_BUILD_HINT, media_type="text/plain; charset=utf-8", status_code=503)


@app.get("/{path:path}")
def spa_fallback(path: str):
    """React Router 的深層路徑要落回 index.html；/api 不在此列。"""
    if path.startswith("api/"):
        raise RouteNotFound(f"找不到這個 API 端點：/{path}")
    candidate = os.path.normpath(os.path.join(FRONTEND_DIST, path))
    # 用 commonpath 而不是字串前綴比對：`dist-backup/secret` 這種路徑
    # 的字串開頭也是 `…/dist`，前綴比對會放行；commonpath 比的是路徑元素。
    try:
        inside_dist = os.path.commonpath([FRONTEND_DIST, candidate]) == FRONTEND_DIST
    except ValueError:
        inside_dist = False  # 不同磁碟機（Windows）就不可能在 dist 底下
    # 防目錄穿越：normpath 之後必須仍在 dist 底下
    if inside_dist and os.path.isfile(candidate):
        return FileResponse(candidate)
    dist_index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    return Response(_BUILD_HINT, media_type="text/plain; charset=utf-8", status_code=503)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
