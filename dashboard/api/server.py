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
    prompts,
    providers,
)
from core import repository as repo
from core.chat_client import (
    GoogleChatClient,
    format_conversation,
    validate_limit,
)
from core.errors import (
    ChatPulseError,
    CodeProjectNotFound,
    ConfigurationError,
    InvalidParameter,
    MentionNotFound,
    NotAuthenticated,
    RouteNotFound,
)
from core.mentions import CollectorRunner

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
        entry = {"timestamp": now, "data": formatted, "was_cached": False}
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


def space_display_name(viewer_id: int, space_id: str) -> str:
    for s in list_spaces_cached(viewer_id)["data"]:
        if s["id"] == space_id:
            return s["displayName"]
    return space_id


def space_shape(viewer_id: int, space_id: str) -> Tuple[Optional[str], Optional[str]]:
    """回傳 (spaceType, spaceThreadingState)，決定草稿的脈絡形狀。

    走的是已經在記憶體裡的 spaces 快取，不多打一次 Google API。

    查不到（Space 不在清單裡、快取剛好沒有）就回 (None, None)——
    `draft_context.is_flat_space()` 對它的判定是「當作有討論串」，
    也就是**退回改動前的行為**。判不出來時保守，不要猜成扁平：
    把別串內容當成同一段對話，比少給脈絡貴。
    """
    for s in list_spaces_cached(viewer_id)["data"]:
        if s["id"] == space_id:
            return s.get("type"), s.get("threadingState")
    return None, None


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


def _viewer_public(viewer: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": viewer["id"],
        "google_user_id": viewer["google_user_id"],
        "email": viewer.get("email"),
        "display_name": viewer.get("display_name"),
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


def _validate_branches(branches: Dict[str, str]) -> Dict[str, str]:
    """環境名稱是封閉字彙，擋掉 uat/UAT/staging 這種同義混寫。"""
    if not branches:
        raise InvalidParameter("至少要指定一個環境對應的分支，例如 production")
    out: Dict[str, str] = {}
    for env, branch in branches.items():
        out[code_search.validate_environment(env)] = (branch or "").strip()
        if not out[code_search.validate_environment(env)]:
            raise InvalidParameter(f"環境 {env} 的分支名稱不可空白")
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
    project_id = repo.create_code_project(
        viewer["id"],
        name=req.name.strip(),
        repo_path=req.repo_path.strip(),
        branches=branches,
        default_env=code_search.validate_environment(req.default_env),
        include_globs=req.include_globs,
        exclude_globs=req.exclude_globs,
    )
    return repo.get_code_project(viewer["id"], project_id)


@app.patch("/api/v1/code-projects/{project_id}")
def patch_code_project(
    project_id: int, req: CodeProjectPatch, viewer: Dict[str, Any] = ViewerDep
):
    if not repo.get_code_project(viewer["id"], project_id):
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    branches = _validate_branches(req.branches) if req.branches is not None else None
    repo.update_code_project(
        viewer["id"],
        project_id,
        name=req.name.strip() if req.name else None,
        repo_path=req.repo_path.strip() if req.repo_path else None,
        branches=branches,
        default_env=(
            code_search.validate_environment(req.default_env) if req.default_env else None
        ),
        include_globs=req.include_globs,
        exclude_globs=req.exclude_globs,
        enabled=req.enabled,
    )
    return repo.get_code_project(viewer["id"], project_id)


@app.delete("/api/v1/code-projects/{project_id}")
def remove_code_project(project_id: int, viewer: Dict[str, Any] = ViewerDep):
    if not repo.delete_code_project(viewer["id"], project_id):
        raise CodeProjectNotFound(f"找不到參考專案 {project_id}")
    return {"deleted": project_id}


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
        "mention": _mention_public(rows[0], name_resolver_for(viewer)) if rows else None,
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
    limit = validate_limit(req.default_limit) if req.default_limit is not None else None
    style = prompts.validate_style(req.default_style) if req.default_style else None
    provider = req.default_provider
    if provider:
        # 存進偏好前先驗一次，避免存下一個會在每次摘要時才爆的值
        providers.resolve_name(provider)
    return repo.update_preferences(
        viewer["id"],
        pinned_space_ids=req.pinned_space_ids,
        default_limit=limit,
        default_style=style,
        default_provider=provider,
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
    viewer: Dict[str, Any] = ViewerDep,
):
    """訊息列表（8.2）。space_id 走 query string，不放 path（8.1）。"""
    limit = validate_limit(limit)
    if not space_id.startswith("spaces/"):
        raise InvalidParameter(f"space_id 必須是完整資源名（spaces/…），收到 {space_id!r}")

    client = get_client(viewer["id"])
    messages = client.fetch_recent_messages(space_id, limit=limit)
    learn_names(messages)
    learn_dm_peer(viewer, space_id, messages)
    resolve = name_resolver_for(viewer)

    formatted = []
    for m in messages:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        sender_obj = m.get("sender") or {}
        formatted.append(
            {
                "name": m.get("name"),
                "sender": sender_obj.get("displayName") or resolve(sender_obj.get("name")),
                "sender_id": sender_obj.get("name"),
                "time": (m.get("createTime") or "")[:16].replace("T", " "),
                "text": text,
            }
        )
    return {
        "space_id": space_id,
        "space_name": space_display_name(viewer["id"], space_id),
        "count": len(formatted),
        "messages": formatted,
    }


@app.get("/api/v1/styles")
def get_styles():
    return {"styles": prompts.style_options()}


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


def _mention_public(row: Dict[str, Any], resolve=None) -> Dict[str, Any]:
    sender = row.get("sender_display")
    if not sender and resolve:
        sender = resolve(row.get("sender_name"))
    return {
        "id": row.get("id"),
        "space_id": row.get("space_id"),
        "space_name": row.get("space_name"),
        "message_name": row.get("message_name"),
        "thread_name": row.get("thread_name"),
        "sender_display": sender or "未知成員",
        "sender_id": row.get("sender_name"),
        "create_time": row.get("create_time"),
        "state": row.get("state"),
        "resolved_at": row.get("resolved_at"),
        "text": row.get("text"),
        "content_error": row.get("content_error"),
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
    return {
        "count": len(rows),
        "counts": repo.count_mentions(viewer["id"]),
        "mentions": [_mention_public(r, resolve) for r in rows],
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
    return _mention_public(row or {}, name_resolver_for(viewer))


@app.post("/api/v1/mentions/refresh")
def refresh_mentions(viewer: Dict[str, Any] = ViewerDep):
    """立刻跑一輪採集，不等下一個輪詢週期。"""
    results = collector_runner.run_once()
    return {"ran": True, "stats": results.get(viewer["id"])}


class DraftRequest(BaseModel):
    # 7.3：不自動選擇 Reference Space，預設空陣列
    reference_space_ids: List[str] = Field(default_factory=list)
    provider: Optional[str] = None
    limit: int = Field(default=cfg.LIMIT_DEFAULT, ge=cfg.LIMIT_MIN, le=cfg.LIMIT_MAX)

    #: 要拿來當程式碼佐證的參考專案。空陣列＝不查程式碼（預設）
    code_project_ids: List[int] = Field(default_factory=list)
    #: 查哪個環境的分支。沒給就用各專案自己的 default_env
    code_environment: Optional[str] = None
    #: 覆寫自動抽出的搜尋關鍵字。自動抽詞是刻意做弱的（見 code_search
    #: 的說明），猜錯時使用者可以直接指定
    code_terms: List[str] = Field(default_factory=list)
    #: 直接指定要看的檔案，跳過搜尋
    code_paths: List[str] = Field(default_factory=list)


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


def collect_code_context(
    viewer_id: int, req: DraftRequest, mention_text: str, thread_text: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """依請求指定的專案去查程式碼。回傳 (code_blocks, skipped)。

    沒指定專案就回空的——「沒查」與「查了沒找到」對模型是兩種不同的事實，
    prompts._code_section 會分別講清楚，這裡不要混為一談。

    路徑或分支不存在會往外拋（CodeRepoNotFound／CodeBranchNotFound），
    由呼叫端轉成 SSE error 事件。那是硬失敗而不是降級：使用者要的就是
    有依據的草稿，靜默給一份沒依據的更糟。
    """
    if not cfg.CODE_ENABLED or not req.code_project_ids:
        return [], []

    ids = list(dict.fromkeys(req.code_project_ids))[: cfg.CODE_MAX_PROJECTS_PER_DRAFT]
    terms = [t.strip() for t in req.code_terms if t.strip()] or code_search.extract_search_terms(
        mention_text, thread_text
    )

    blocks: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for pid in ids:
        project = repo.get_code_project(viewer_id, pid)
        if not project:
            skipped.append(f"找不到參考專案 {pid}")
            continue
        if not project.get("enabled", True):
            skipped.append(f"參考專案「{project['name']}」已停用")
            continue
        env = code_search.validate_environment(req.code_environment or project["default_env"])
        branch = repo.resolve_branch(viewer_id, pid, env)
        if not branch:
            skipped.append(
                f"參考專案「{project['name']}」沒有設定 {env} 環境的分支"
            )
            continue
        ctx, ctx_skipped = code_search.collect(
            repo_path=project["repo_path"],
            project_name=project["name"],
            environment=env,
            branch=branch,
            terms=terms,
            include_globs=project.get("include_globs") or (),
            exclude_globs=project.get("exclude_globs") or (),
            explicit_paths=req.code_paths,
        )
        blocks.append(_code_block(ctx))
        skipped.extend(ctx_skipped)
    return blocks, skipped


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

    def generate() -> Generator[str, None, None]:
        # 放在 try 外面：客戶端中途斷線時 yield 會拋 GeneratorExit，
        # finally 仍要看得到已收到的內容才補存得了（見 save_partial）
        collected: List[str] = []
        saved = False
        try:
            client = get_client(viewer_id)
            ai = get_provider(viewer_id, req_provider)

            # 被 @ 的那則訊息本身
            mention_msg = client.get_message(mention["message_name"])
            mention_text = (mention_msg.get("text") or "").strip() or "（訊息內容已被刪除或無法取回）"
            learn_names([mention_msg])

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
                thread_name=mention.get("thread_name"),
                resolve=resolve,
                on_retrieved=_learn_then_resolve,
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

            yield sse(
                {
                    "type": "meta",
                    "mention_id": mention_id,
                    "space": mention.get("space_name") or mention["space_id"],
                    # 保留舊欄位：前端與 e2e 都在讀它，而它的語意（這次送進模型的
                    # 對話則數）沒有變，只是來源從「該討論串」變成「這次的脈絡」。
                    "thread_message_count": ctx.message_count,
                    "context": ctx.to_meta(),
                    "reference_spaces": [
                        {
                            "space_id": b["space_id"],
                            "space_name": b["space_name"],
                            "message_count": b["message_count"],
                        }
                        for b in ref_blocks
                    ],
                    "provider": resolved_provider,
                    "model": ai.model,
                    "image_count": len(images),
                    "images_skipped": skipped_images,
                }
            )

            # 程式碼佐證。放在 prompt 組裝之前，因為查失敗要能變成 error 事件——
            # 使用者指定了專案卻拿到一份沒查程式碼的草稿，比直接報錯更糟。
            code_blocks, code_skipped = collect_code_context(
                viewer_id, req, ctx.anchor_plain_text or mention_text, ctx.search_text
            )
            if code_blocks or code_skipped:
                yield sse(
                    {
                        "type": "code_meta",
                        "projects": [
                            {
                                "project_name": b["project_name"],
                                "environment": b["environment"],
                                "environment_label": b["environment_label"],
                                "branch": b["branch"],
                                "commit_sha": b["commit_sha"],
                                "terms": b["terms"],
                                "hit_count": len(b["hits"]),
                                "notes": b["notes"],
                            }
                            for b in code_blocks
                        ],
                        "skipped": code_skipped,
                    }
                )

            prompt = prompts.draft_reply_prompt(
                anchor_text=ctx.anchor_text or mention_text,
                mention_sender=mention_sender,
                space_name=mention.get("space_name") or mention["space_id"],
                space_type_label=ctx.space_type_label,
                context_blocks=[b.to_prompt_dict() for b in ctx.blocks],
                coverage=ctx.coverage,
                reference_blocks=ref_blocks,
                code_blocks=code_blocks or None,
            )

            for chunk in ai.stream_text(prompt, operation="draft_reply", images=images):
                collected.append(chunk)
                yield sse({"type": "chunk", "text": chunk})

            content = "".join(collected)
            draft_id = repo.create_draft(mention_id, content) if content.strip() else None
            saved = True
            yield sse({"type": "done", "draft_id": draft_id})
        except ChatPulseError as exc:
            yield sse(exc.to_sse_event())
        except Exception as exc:
            log.exception("Draft Reply 串流失敗")
            yield sse(
                {"type": "error", "code": "INTERNAL_ERROR", "message": f"草稿產生失敗：{exc}"}
            )
        finally:
            if not saved:
                save_partial(
                    lambda text: repo.create_draft(mention_id, text),
                    "草稿",
                    "".join(collected),
                )

    return sse_response(generate())


class ReplyRequest(BaseModel):
    text: str
    draft_id: Optional[int] = None

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
    """
    mention = repo.get_mention(viewer["id"], mention_id)
    if not mention:
        raise MentionNotFound()

    res = get_client(viewer["id"]).send_message(
        mention["space_id"], req.text, thread_name=mention.get("thread_name")
    )
    if req.draft_id:
        # 帶 viewer_id 驗擁有權：draft_id 來自 request body，不驗的話
        # Viewer A 可以把 Viewer B 的草稿標成已送出
        if not repo.mark_draft_sent(viewer["id"], req.draft_id):
            log.warning(
                "Viewer %s 送出時帶的 draft_id=%s 不屬於他，已略過標記",
                viewer["id"],
                req.draft_id,
            )
    updated = repo.set_mention_state(viewer["id"], mention_id, "resolved")

    return {
        "status": "success",
        "message_id": res.get("name"),
        "createTime": res.get("createTime"),
        "thread_name": (res.get("thread") or {}).get("name"),
        "mention": _mention_public(updated or {}, name_resolver_for(viewer)),
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
