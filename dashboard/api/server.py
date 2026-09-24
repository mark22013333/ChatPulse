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
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
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
    worklog_allocation,
    worklog_clustering,
    worklog_evidence,
    worklog_submit,
)
from core import repository as repo
from core.chat_client import (
    GoogleChatClient,
    attachment_note,
    format_conversation,
    parse_rfc3339,
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
    WorkRepoNotFound,
    WorklogAlreadySubmitted,
    WorklogDraftNotFound,
    WorklogSubmitInProgress,
)
from core.mentions import CollectorRunner
from core.polishers import sepia as sepia_polisher
from core.zplanner_client import ZPlannerClient

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


def preferred_provider_name(
    viewer_id: Optional[int] = None, name: Optional[str] = None
) -> Optional[str]:
    """請求沒指定供應商時，補上 Viewer 的偏好（供應商解析的第一步）。

    抽成獨立函式是為了讓 `get_provider()` 與 `resolve_model()` 看到**同一個**
    供應商名。不共用的話會出現這種漏洞：請求沒帶 provider、Viewer 偏好是
    `auto`、body 帶了 model——model 解析那邊以為不是 auto 而放行，實際建構
    供應商時才炸，錯誤就從 400 掉進串流中的 error 事件裡。

    傳入的 name 有值時原樣回傳，所以重複呼叫是冪等的。
    """
    if name is not None or viewer_id is None:
        return name
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
        return saved
    except Exception:
        return None


def viewer_default_models(viewer_id: Optional[int]) -> Dict[str, str]:
    """Viewer 存下來的「每個供應商各自的預設 model」，鍵是具體實作名。

    讀不到就當作沒設過。偏好壞掉不該讓摘要與草稿整個打不開——退回去用供應商
    自己的 config 預設，Viewer 頂多發現「怎麼沒照我選的」，而不是整個功能不能用。

    🔴 **只給讀取路徑用，不可以拿來當寫入的基底。** 這支會把讀取失敗吞成空
    dict，當成 merge 的基底就等於「一次短暫的讀取失敗，PATCH 靜默變成整份
    覆寫、把別的供應商那格清掉，而且照樣回 200」。寫入路徑要用會拋的
    `repo.get_default_models()`（同 `repository.py` 自己的分界：讀取寬鬆、
    寫入一律拋）。
    """
    if viewer_id is None:
        return {}
    try:
        return dict(repo.get_preferences(viewer_id).get("default_models") or {})
    except Exception:
        log.exception("Viewer %s 的 default_models 偏好讀取失敗，這次當作沒設過", viewer_id)
        return {}


def resolve_model(
    viewer_id: Optional[int] = None,
    name: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """一次解析出「這次要用哪個供應商、哪個 model」。這條路的唯一實作。

    回傳 `(供應商名, model 裸值)`。順序是**先展開、再查**：

    0. 先把別名展開成具體實作名（`auto` → `claude_cli`）
    1. body 的 `model` 有值 → 用它
    2. 否則查 `default_models[具體實作名]`
    3. 否則 `None`（讓供應商用自己的 config 預設）

    🔴 回傳的 model 是裸值（`opus`），不是識別字（`claude-cli:opus`）。前綴由
    供應商自己加（`claude_cli.py:58`），這裡再加一次會讓
    `claude-cli:claude-cli:opus` 流進 CLI 參數。

    **為什麼要先展開**：`resolve()` 拿到「別名 ＋ model」會拒絕（`auto` 自己會
    換供應商，把 `opus` 餵給 Gemini 要到真的呼叫 API 才看得出來壞了）。先展開
    就完全避開那個禁忌組合，而且偏好查的是**展開後那一格**，不會錯配。
    退而求其次的做法是「auto 時不查偏好」，但那會讓 Viewer 設了預設卻靜默失效
    ——同樣是靜默失敗，只是換一種。

    ⚠️ **代價是多建一次供應商實例再丟掉**（`resolve_name()` 內部會建一個來問
    可用性）。這不是冗餘呼叫，不要順手優化掉：`auto` 的挑選時機必須維持原樣
    （仍由 `resolve()` 那條路決定誰可用），而兩個實作的 `__init__` 只是存字串、
    Gemini 的 client 是 lazy（`gemini.py:33`），所以成本可忽略。

    仍然要擋的例外：**body 明確帶了 model、而供應商是多候選別名** → 400。
    偏好是 per-provider 的對照表，查哪一格不會錯；body 的單一 model 值不帶
    供應商資訊，Viewer 選的是「auto ＋ 某個 model」這個本身就矛盾的組合。
    「是不是多候選別名」一律問 `providers.is_multi_candidate()`，**不要在這裡
    自己列一份名單**——手抄的複本不會跟著 core 新增的別名走，而它失效的表現是
    靜默放行（新別名底下的 Gemini 收到 `opus`，要到呼叫 API 才壞）。
    """
    # 先 strip：`model="   "` 在底層等同沒指定（供應商自己會 strip），這裡若把它
    # 當成「有指定」，auto 會回一個 400 而底層根本不認為 Viewer 指定了什麼——
    # 兩層對同一個輸入給出相反結論，是最難查的那種不一致。
    model = (model or "").strip() or None
    name = preferred_provider_name(viewer_id, name)
    # `is_multi_candidate()` 自己會把 None 退回 CHATPULSE_AI_PROVIDER，
    # 所以這裡原樣傳——伺服器預設本身就可能是 auto，「沒指定」不等於「不是別名」。
    if model and providers.is_multi_candidate(name):
        raise InvalidParameter(
            "指定 model 時請一併指定具體的供應商（claude_cli 或 gemini），"
            "auto 會自行挑選供應商，指定的 model 未必適用"
        )

    try:
        implementation = providers.resolve_name(name)
    except ChatPulseError:
        # 供應商現在不可用（沒裝 CLI、沒設金鑰）。那個錯誤要留在它原本會發生的
        # 地方——串流中的 error 事件或建構供應商時——不要因為「順便展開個名字」
        # 就提前變成 400，那會改掉既有端點的錯誤時機。名字原樣傳回去。
        return name, (model or None)

    if model:
        return implementation, model
    return implementation, viewer_default_models(viewer_id).get(implementation) or None


def get_provider(
    viewer_id: Optional[int] = None,
    name: Optional[str] = None,
    model: Optional[str] = None,
) -> providers.AIProvider:
    """取得一個 AI 供應商實例，並把用量記到該 Viewer 名下。

    name 為 None 時依序取：Viewer 的偏好 → CHATPULSE_AI_PROVIDER → "claude"。
    供應商名與 model 都走 `resolve_model()`，所以這裡拿到的一律是具體實作名。
    模型名由供應商自己回報，不由這裡猜——換供應商後用量表才不會記到錯的模型上。
    """
    name, model_id = resolve_model(viewer_id, name, model)

    def _recorder(
        operation: str, model: str, prompt_t: int, output_t: int, total_t: int
    ) -> None:
        try:
            repo.record_token_usage(
                viewer_id, model, operation, prompt_t, output_t, total_t
            )
        except Exception:
            log.exception("token 用量記錄失敗")

    return providers.resolve(name, model=model_id, usage_recorder=_recorder)


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


def _warn_if_multi_worker() -> None:
    """多 worker 啟動時在 log 留一行警告。

    🔴 **工時送出的並行防護是行程內的**（`_claim_worklog_submit` 用一個模組級
    的 set）。多 worker 時兩個重疊的送出各自拿到自己行程的鎖，那道防線**完全
    不存在**，而且沒有任何東西會擋下來：2026-09-14 實測，兩個重疊送出拿到的是
    **不同**的 `worklog_id`，所以 `idx_worklog_entry_remote` 一次都不會觸發，
    ZPlanner 就多出幾筆月結後刪不掉的重複工時。

    docstring 寫了不算數——沒有人會在加 `--workers 4` 之前去讀那份 docstring。
    這裡至少讓它在啟動 log 裡留下一行，事後查得到。要真的支援多 worker，得先
    把那道鎖換成 DB 認領（見 `_claim_worklog_submit` 與 plan.md 的 D-10）。

    偵測只涵蓋實務上會用到的兩種寫法（`--workers/-w` 與 `WEB_CONCURRENCY`），
    **偵測不到不代表安全**——這是提醒，不是防護。
    """
    workers = 0
    env_workers = os.environ.get("WEB_CONCURRENCY", "").strip()
    if env_workers.isdigit():
        workers = int(env_workers)
    argv = sys.argv
    for flag in ("--workers", "-w"):
        if flag in argv:
            pos = argv.index(flag) + 1
            if pos < len(argv) and argv[pos].isdigit():
                workers = max(workers, int(argv[pos]))
    if workers > 1:
        log.warning(
            "偵測到 %s 個 worker：工時送出的並行防護是行程內的，多 worker 下"
            "等於沒有防護，重疊送出會在 ZPlanner 造出月結後刪不掉的重複工時。"
            "要多 worker 請先把 _claim_worklog_submit 換成 DB 認領。",
            workers,
        )


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
    _warn_if_multi_worker()
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
    body = exc.to_dict()
    # `detail` 是**字串**時維持原樣：那是給 log 看的內部訊息，不外洩。
    # 是 **dict** 時代表拋的人刻意要給前端用的結構化補充（例：「重產會清掉
    # 幾筆已選的專案」）。把那個數字只寫在 message 裡，前端就只能拿正則從
    # 一句中文裡挖數字——句子改一個字就挖錯，而挖錯的後果是跟使用者說錯
    # 「會弄丟幾筆」。
    if isinstance(exc.detail, dict):
        body["error"]["detail"] = exc.detail
    return JSONResponse(body, status_code=exc.http_status, headers=headers)


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
            # 每個供應商各自的預設 model（裸值）。沒設過的供應商不會出現在這裡，
            # 前端要顯示「跟著供應商的預設走」而不是空字串。
            "default_models": viewer_default_models(viewer["id"]),
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

    #: 每個供應商各自的預設 model（**裸值**，例 `opus`）。
    #:
    #: 一個供應商一格，而不是單一個 `default_model`：model 的意義依附在供應商
    #: 上，`opus` 對 Gemini 毫無意義。存單一字串的話，Viewer 一換供應商偏好就
    #: 變成非法值，而且要到送出時才浮現。
    #:
    #: 鍵必須是**具體實作名**（`claude_cli`／`gemini`），不接受別名——`claude`／
    #: `auto` 會展開成不同實作，存進去就對應不回來。值送 `null` 或空字串＝清除
    #: 那一個供應商的偏好；整個欄位沒帶＝不動既有偏好（同其他欄位）。
    #: 值**不驗白名單**：白名單只是常用值，「也能自己打字自訂」正是這個功能要的。
    default_models: Optional[Dict[str, Optional[str]]] = None


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


def _validate_repo_path(repo_path: Optional[str], *, label: str = "專案路徑") -> None:
    """路徑必須是絕對路徑，而且不含 NUL。

    相對路徑會相對於**伺服器的工作目錄**去解，那跟使用者心裡想的不是同一個地方；
    等到產草稿才發現「查不到這個 repo」就太晚了。

    `label` 只影響錯誤訊息的主詞。參考專案與「我的工作 repo」是兩份不同的登錄、
    兩個不同的設定頁，訊息講錯會把人送去錯的那一頁（規則本身只有這一份，
    不要為了換個名詞複製一次）。
    """
    if repo_path is None:
        return
    rp = repo_path.strip()
    if not rp:
        raise InvalidParameter(f"{label}不可空白")
    if "\x00" in rp:
        raise InvalidParameter(f"{label}含非法字元")
    if not os.path.isabs(rp):
        raise InvalidParameter(f"{label}必須是絕對路徑")


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
# 我的工作 repo（Draft Worklog 的證據來源，ADR-0008）
#
# 與上面的參考專案是**兩份不同的登錄**，不共用資料表也不共用端點：那邊是
# 「查程式碼回答問題」（要釘死環境與分支），這邊是「回想這天做了什麼」
# （預設掃全部分支）。詳見 core/db.py 的 work_repos 註解。
# ==========================================================================


class WorkRepoRequest(BaseModel):
    """登錄一個本機 git repo 當作工時草稿的證據來源。

    `author_emails` 是**陣列**：同一個人在不同 repo 常常設不同的 `user.email`，
    只填一個會讓另一個信箱的 commit 靜默漏掉。不確定自己在這個 repo 用哪個
    信箱時，先建立、再按「重新檢查」，`detected_emails` 會列出來。

    `branch_filter` **空陣列＝掃全部分支**（`--all`），不是「不掃」。
    """

    name: str = Field(min_length=1, max_length=80)
    repo_path: str = Field(min_length=1)
    author_emails: List[str] = Field(default_factory=list)
    branch_filter: List[str] = Field(default_factory=list)
    #: 只用來把該專案的 issue 排進候選清單並標示來源，**絕不自動填**（ADR-0008）
    default_zplanner_project_id: Optional[int] = None
    default_zplanner_project_key: Optional[str] = None


class WorkRepoPatch(BaseModel):
    """部分更新。

    `default_zplanner_project_id` / `_key` 與偏好的那幾個欄位同一個慣例：
    **送 `null` 代表「清除」，不是「不改」**——「這個 repo 沒有預設專案」
    是使用者會主動選的狀態。handler 靠 `model_fields_set` 區分兩者。
    其餘欄位是 NOT NULL，沒帶就是不改。
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    repo_path: Optional[str] = None
    author_emails: Optional[List[str]] = None
    branch_filter: Optional[List[str]] = None
    default_zplanner_project_id: Optional[int] = None
    default_zplanner_project_key: Optional[str] = None
    enabled: Optional[bool] = None


def _clean_str_list(values: Optional[List[str]], *, label: str) -> List[str]:
    """整理使用者送來的字串陣列：去空白、丟掉空項、去重且保留順序。

    去重保留順序而不是排序：使用者填的順序有意義（第一個通常是主要信箱），
    重排會讓他下次打開設定頁看到一份自己沒填過的清單。

    這些值會被當成 `--author=` 與 ref 名稱傳給 git（永遠是 argv list，
    不組 shell 字串），但仍然擋掉換行與 NUL：那兩個字元進得去就代表
    有人在某處用字串拼了指令，早點擋掉比事後追查便宜。
    """
    if values is None:
        return []
    out: List[str] = []
    seen = set()
    for raw in values:
        item = (raw or "").strip()
        if not item:
            continue
        if "\x00" in item or "\n" in item or "\r" in item:
            raise InvalidParameter(f"{label}「{item[:40]}」含非法字元")
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


#: `detected_emails` 掃幾筆 commit。實際值在 `cfg.WORKLOG_EMAIL_SCAN_COMMITS`，
#: 這裡只留一個指標，避免同一個數字在兩處各自漂移。
WORK_REPO_EMAIL_SCAN_COMMITS = worklog_evidence.WORK_REPO_EMAIL_SCAN_COMMITS

#: 這兩個驗證函式的本體住在 `core/worklog_evidence.py`——它們讀的是 git，
#: 不是 HTTP request，和證據蒐集用的是同一組 git 慣例（`code_search._git`、
#: ADR-0006 的「不碰工作區」）。這裡只留一個別名給端點與既有測試用。
_verify_work_repo = worklog_evidence.verify_work_repo


def _verify_and_record_work_repo(
    viewer_id: int, work_repo: Dict[str, Any]
) -> Dict[str, Any]:
    """驗證並把結果寫回，回傳帶 `verification` 的 dict。

    本體在 `core/worklog_evidence.verify_and_record_work_repo`；這裡只負責
    把 `viewer_id` 綁進寫回用的 callable。那個模組刻意不 import repository
    （同 `code_search` 的可測性慣例），所以「寫哪張表」由這一層決定。
    """
    return worklog_evidence.verify_and_record_work_repo(
        work_repo,
        record=lambda error: repo.record_work_repo_verification(
            viewer_id, work_repo["id"], error
        ),
        on_record_error=lambda exc: log.exception(
            "寫入工作 repo 驗證結果失敗（不影響回應）"
        ),
    )


def _require_work_repo(viewer_id: int, repo_id: int) -> Dict[str, Any]:
    found = repo.get_work_repo(viewer_id, repo_id)
    if found is None:
        raise WorkRepoNotFound(f"找不到工作 repo {repo_id}")
    return found


def _assert_work_repo_unique(
    viewer_id: int, *, name: Optional[str], repo_path: Optional[str], exclude_id: Optional[int]
) -> None:
    """先查一次同名／同路徑，才能回一句看得懂的 400。

    `UNIQUE(viewer_id, name)` 與 `UNIQUE(viewer_id, repo_path)` 都會擋住重複，
    但那會變成 sqlite3.IntegrityError（500）。做法同 reply-prompts 的同名檢查。
    """
    if name:
        clash = repo.find_work_repo_by_name(viewer_id, name)
        if clash is not None and clash["id"] != exclude_id:
            raise InvalidParameter(f"已經有一個叫「{name}」的工作 repo")
    if repo_path:
        clash = repo.find_work_repo_by_path(viewer_id, repo_path)
        if clash is not None and clash["id"] != exclude_id:
            raise InvalidParameter(
                f"這個路徑已經登錄成「{clash['name']}」了。"
                "同一個 repo 登錄兩次會讓同一批 commit 被算兩遍，時數會分錯。"
            )


@app.get("/api/v1/work-repos")
def get_work_repos(viewer: Dict[str, Any] = ViewerDep):
    return {"repos": repo.list_work_repos(viewer["id"])}


@app.post("/api/v1/work-repos")
def post_work_repo(req: WorkRepoRequest, viewer: Dict[str, Any] = ViewerDep):
    name = req.name.strip()
    _validate_repo_path(req.repo_path, label="工作 repo 的路徑")
    repo_path = req.repo_path.strip()
    emails = _clean_str_list(req.author_emails, label="作者信箱")
    branches = _clean_str_list(req.branch_filter, label="分支名稱")
    _assert_work_repo_unique(
        viewer["id"], name=name, repo_path=repo_path, exclude_id=None
    )
    repo_id = repo.create_work_repo(
        viewer["id"],
        name=name,
        repo_path=repo_path,
        author_emails=emails,
        branch_filter=branches,
        default_zplanner_project_id=req.default_zplanner_project_id,
        default_zplanner_project_key=(req.default_zplanner_project_key or "").strip()
        or None,
    )
    created = repo.get_work_repo(viewer["id"], repo_id)
    assert created is not None
    # 路徑當下不可讀仍然建立成功（外接磁碟可能還沒掛上、專案可能待會才 clone），
    # 但把錯誤記下來讓設定頁標警告。理由同參考專案的「分支不存在仍然建立成功」。
    return _verify_and_record_work_repo(viewer["id"], created)


@app.patch("/api/v1/work-repos/{repo_id}")
def patch_work_repo(
    repo_id: int, req: WorkRepoPatch, viewer: Dict[str, Any] = ViewerDep
):
    _require_work_repo(viewer["id"], repo_id)
    name = req.name.strip() if req.name else None
    _validate_repo_path(req.repo_path, label="工作 repo 的路徑")
    repo_path = req.repo_path.strip() if req.repo_path else None
    _assert_work_repo_unique(
        viewer["id"], name=name, repo_path=repo_path, exclude_id=repo_id
    )

    # 這兩個欄位要區分「沒帶」與「帶了 null」：後者是使用者主動選「這個 repo
    # 沒有預設的 ZPlanner 專案」，必須存得下去。做法同 PATCH /preferences。
    sent = req.model_fields_set
    project_id: Any = repo.UNSET
    if "default_zplanner_project_id" in sent:
        project_id = req.default_zplanner_project_id or None
    project_key: Any = repo.UNSET
    if "default_zplanner_project_key" in sent:
        project_key = (req.default_zplanner_project_key or "").strip() or None

    repo.update_work_repo(
        viewer["id"],
        repo_id,
        name=name,
        repo_path=repo_path,
        author_emails=(
            _clean_str_list(req.author_emails, label="作者信箱")
            if req.author_emails is not None
            else None
        ),
        branch_filter=(
            _clean_str_list(req.branch_filter, label="分支名稱")
            if req.branch_filter is not None
            else None
        ),
        enabled=req.enabled,
        default_zplanner_project_id=project_id,
        default_zplanner_project_key=project_key,
    )
    updated = repo.get_work_repo(viewer["id"], repo_id)
    assert updated is not None
    return _verify_and_record_work_repo(viewer["id"], updated)


@app.post("/api/v1/work-repos/{repo_id}/verify")
def reverify_work_repo(repo_id: int, viewer: Dict[str, Any] = ViewerDep):
    """重新確認路徑還是可讀的 git repo，並回報偵測到的作者信箱。

    設定頁的「重新檢查」按鈕用這個。`detected_emails` 是給使用者**參考**的，
    不會自動寫進 `author_emails`——同一條哲學：由人指定，系統不自動發現。
    """
    found = _require_work_repo(viewer["id"], repo_id)
    return _verify_and_record_work_repo(viewer["id"], found)


@app.delete("/api/v1/work-repos/{repo_id}")
def remove_work_repo(repo_id: int, viewer: Dict[str, Any] = ViewerDep):
    if not repo.delete_work_repo(viewer["id"], repo_id):
        raise WorkRepoNotFound(f"找不到工作 repo {repo_id}")
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

    # ---- 每個供應商各自的預設 model ----
    #
    # **這裡是 merge，不是整份覆寫。** 前端只會送「剛剛改動的那一個供應商」，
    # 直接覆寫等於把另一個供應商的偏好一起清掉。契約給了 null／空字串
    # 「清除這一個」的語意，那句話也只有在 merge 之下才成立——整份覆寫的話，
    # 不送那個鍵就已經是清除了，null 就沒有存在的必要。
    #
    # 寫在 update_preferences 之前：那個函式的回傳值就是這支端點的回應，
    # 它讀的是資料庫現值，先寫才看得到這次的改動。
    #
    # 🔴 基底用**會拋**的 `repo.get_default_models()`，不是寬鬆的
    # `viewer_default_models()`。後者把讀取失敗吞成 `{}`，當基底的話一次短暫的
    # 讀取失敗就會讓這段 merge 靜默退化成整份覆寫——別的供應商那格被清掉、
    # 回應還是 200，而 Viewer 沒有任何線索。讀取路徑可以降級，**寫入路徑不行**。
    #
    # 整個欄位沒帶、或帶了 null／空物件都是「不改」：沒有任何一格要動的時候
    # 不去寫，才不會白白推進 `updated_at`（與文件寫的一致）。
    incoming = req.default_models if "default_models" in sent else None
    if incoming:
        merged = dict(repo.get_default_models(viewer_id))
        for key, value in incoming.items():
            if key not in providers.MODEL_CHOICES:
                raise InvalidParameter(
                    f"default_models 的鍵 {key!r} 不是具體的供應商實作名。"
                    f"可用值：{'、'.join(providers.MODEL_CHOICES)}。"
                    "別名（claude／auto）會展開成不同實作，存了就對應不回來"
                )
            text = (value or "").strip()
            if text:
                merged[key] = text
            else:
                merged.pop(key, None)
        repo.set_default_models(viewer_id, merged)

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
    #: 這次要用的 model **裸值**（例 `opus`）；省略時走 Viewer 偏好或供應商預設。
    #: 值不驗白名單（自訂輸入是這個功能的重點），但 `provider` 是 `auto` 時
    #: 帶值會被擋下來——見 `resolve_model()`。
    model: Optional[str] = None
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
    providers.resolve_name(req_provider)
    # 供應商與 model 一起在門外解析：別名在這裡就展開成具體實作名，
    # 而「明確指定 model ＋ auto」這個矛盾組合也在 SSE 開始之前就回 400。
    #
    # ⚠️ `meta.provider` 要用這裡的 `provider_name`，**不要**另外呼叫一次
    # `providers.resolve_name(req.provider)`：後者看不到 Viewer 的
    # `default_provider` 偏好，於是會回報 claude_cli、實際卻是用 gemini 跑的。
    # 以前沒人在意，但 Viewer 現在會盯著 meta 判斷自己選的 model 有沒有生效。
    provider_name, req_model = resolve_model(viewer_id, req_provider, req.model)

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
            ai = get_provider(viewer_id, provider_name, model=req_model)
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
                    "provider": provider_name,
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
    #: 這次要用的 model **裸值**；語意同 `SummarizeRequest.model`
    model: Optional[str] = None
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
    providers.resolve_name(req.provider)
    req_provider = req.provider
    # 與 summarize_stream 同一份解析：別名先展開，「指定 model ＋ auto」在門外擋掉。
    # `meta.provider` 一律用這裡的 `provider_name`，理由同 summarize_stream。
    provider_name, req_model = resolve_model(viewer_id, req_provider, req.model)
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
            ai = get_provider(viewer_id, provider_name, model=req_model)

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
                    "provider": provider_name,
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
                "provider": provider_name,
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
                    "provider": provider_name,
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
# 工時草稿（Draft Worklog，ADR-0008）
#
# 這一段目前只做兩件事：把一天的證據變成草稿（SSE），以及把一週的草稿狀態
# 與 ZPlanner 既有工時併起來（週檢視）。**送出不在這裡**（階段 2c）。
#
# 貫穿整段的一條規則：**ZPlanner 不可用時不准把整支端點弄掉**。沒設
# ZPLANNER_BASE_URL／APIKEY 的人，仍然該看得到自己的草稿與證據——他只是
# 暫時挑不了候選 issue。所以每一處 ZPlanner 呼叫都降級成一段帶 reason 的
# `available: false`，而不是拋出去。
# ==========================================================================


#: 一群最多列幾個候選。計畫 §7 第 5 條：**最多顯示 3 筆＋常駐搜尋框**，
#: 清單永不封閉。列太多會讓人從「讀理由」退化成「掃清單挑一個看起來像的」。
WORKLOG_MAX_CANDIDATES = 3

#: 權限預檢最多查幾個專案。每個專案是 **2 次** HTTP（role-permissions ＋
#: members），而 SSE 的 meta 要盡快送出去——證據欄晚一秒出現，使用者就多
#: 盯著一個空畫面一秒。超出的專案標 `permission_checked: false`，不猜。
WORKLOG_MAX_PERMISSION_PROJECTS = 5

#: 自動組出來的 note 上限。這一階段的 note 是 commit subject 接起來的，
#: 不是 AI 寫的——長度只要夠讓人認出「這筆在講什麼」就好。
WORKLOG_NOTE_MAX_CHARS = 120

#: ZPlanner 週工時的 client 方法名。
#:
#: ⚠️ **`core/zplanner_client.py` 目前沒有這支。** 那一層只實作了六個唯讀
#: 端點（issues／projects／role-permissions／members），不含
#: `GET /api/worklogs/weekly/`。所以這裡用 duck-typing 探測而不是直接呼叫：
#: 探不到就回 `available: false` 並說明原因，本地草稿照常回。
#:
#: 約定的簽章是 `list_weekly_worklogs(week_start)`（`YYYY-MM-DD`，回該週七天）。
#: client 補上這支之後，這裡不需要改。
ZPLANNER_WEEKLY_METHOD = "list_weekly_worklogs"


def zplanner_client() -> ZPlannerClient:
    """建一個 ZPlanner client。

    獨立成函式**只為了讓測試整支換掉**——不然驗「ZPlanner 不可用時週檢視
    仍然回得出本地草稿」就得依賴執行環境剛好沒設 token，那種測試在別人的
    機器上會得到相反的結果。
    """
    return ZPlannerClient()


def _zplanner_week_rows(
    data: Any,
) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    """把 ZPlanner 的週工時正規化成 `{work_date: {hours, worklog_count}}`。

    回 `(結果, 錯誤原因)`，兩者恰有一個是 None。**認不出來的形狀一律回錯誤
    原因，不回空字典**：空字典與「那週真的沒填工時」長得一模一樣，而這兩件
    事對使用者的意義正好相反（一個是「系統看不懂」，一個是「你還沒填」）。
    同一條判準見 `zplanner_client._as_list` 的註解。
    """
    rows: Any = data
    if isinstance(data, dict):
        for key in ("items", "results", "days", "worklogs"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        else:
            return None, f"ZPlanner 週工時的回應形狀不認得（拿到 {sorted(data)[:10]}）"
    if not isinstance(rows, list):
        return None, f"ZPlanner 週工時的回應不是清單（拿到 {type(rows).__name__}）"

    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None, "ZPlanner 週工時的項目不是物件"
        date = str(row.get("date") or row.get("work_date") or "").strip()[:10]
        if not date:
            return None, "ZPlanner 週工時的項目沒有日期欄位"
        bucket = out.setdefault(date, {"hours": 0.0, "worklog_count": 0})
        try:
            bucket["hours"] += float(row.get("hours") or 0)
        except (TypeError, ValueError):
            return None, f"ZPlanner 週工時的 hours 不是數字：{row.get('hours')!r}"
        # 筆數優先數 `entries` 的長度。
        #
        # ZPlanner 的 `days[]` **沒有 worklog_count 這個欄位**（2026-09-13 對
        # 正式站實測），那天實際填了幾筆在 `entries` 裡。原本寫成
        # `int(row.get("worklog_count") or 1)`，於是不管那天填了 1 筆還是 5 筆
        # 都一律算成 1——畫面上「已填 n 筆」永遠等於「有填工時的天數」，
        # 而且數字看起來很合理，不會有人起疑。
        entries = row.get("entries")
        if isinstance(entries, list):
            bucket["worklog_count"] += len(entries)
        else:
            bucket["worklog_count"] += int(row.get("worklog_count") or 1)
    return {d: {**v, "hours": f"{v['hours']:.2f}"} for d, v in out.items()}, None


def _zplanner_week_target(data: Any) -> Dict[str, Any]:
    """從 ZPlanner 的週回應取出「目標時數」與「完成率」。

    這兩個值回答週檢視最有用的那個問題——**這週還差多少**。ZPlanner 自己
    算好了（`target_hours`／`completion_pct`，2026-09-13 實測），照抄就好，
    不要在這裡自己除——它的目標時數會因人、因請假而不同，我們算的版本
    遲早會與 ZPlanner 網頁上的數字對不起來。

    取不到就整個不回報（回空字典），**不要填一個猜的 40 小時**：一個編出來
    的目標會讓「完成 38.8%」這種話變成假的。
    """
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    target = data.get("target_hours")
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        out["target_hours"] = f"{float(target):.2f}"
    pct = data.get("completion_pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        out["completion_pct"] = float(pct)
    return out


def _zplanner_week_mismatch(data: Any, dates: List[str]) -> Optional[Dict[str, Any]]:
    """ZPlanner 回的那一週，跟我們請求的七天是同一段嗎？不是就回描述，是就回 None。

    🔴 **ZPlanner 的 `/api/worklogs/weekly/` 只回「本週」，`week_start` 送了
    沒有作用**（2026-09-14 實測五種輸入皆回同一週，見 `list_weekly_worklogs`
    的 docstring）。所以只要使用者把週檢視切到本週以外的任何一週，這裡就必然
    對不上——這不是偶發的邊界情況，是**非本週一律如此**。

    再加上我們的端點明說 `week_start` 不強制是星期一（見 `get_worklog_week`），
    即使在本週，週日起算的七天也會與 ZPlanner 的週一起算錯開。

    錯開之後最危險的不是少一格，是**總計**：ZPlanner 的 `total_hours`／
    `target_hours`／`completion_pct` 描述的是**它那一週**，直接抄過來就變成
    「表頭的數字跟下面七個格子加起來對不上」。2026-09-13 實測（週日起的
    `2026-09-06`）：格子合計 40.0 小時，表頭卻寫 44.00 小時、完成率 110%。
    兩個數字都看起來很合理，沒有人會發現它算的是別的區間。

    舊版回應沒有 `week_start`／`week_end` 時回 None——**沒有東西可以比對不等於
    對得起來**，但也不能憑空判它錯；這種情況只靠逐日資料的日期自然過濾。
    """
    if not isinstance(data, dict):
        return None
    zp_start = str(data.get("week_start") or "").strip()[:10]
    zp_end = str(data.get("week_end") or "").strip()[:10]
    if not zp_start and not zp_end:
        return None
    if zp_start == dates[0] and (not zp_end or zp_end == dates[-1]):
        return None
    return {
        "requested_week_start": dates[0],
        "requested_week_end": dates[-1],
        "zplanner_week_start": zp_start or None,
        "zplanner_week_end": zp_end or None,
        "reason": (
            f"ZPlanner 只查得到本週（{zp_start or '?'}～{zp_end or '?'}）的工時，"
            f"與這裡顯示的 {dates[0]}～{dates[-1]} 不是同一段，所以這一段沒有"
            "ZPlanner 的資料可以對照——不代表那幾天真的沒填。目標時數與完成率"
            "描述的是它那一週，拿來用會對不上，因此整個不顯示。"
        ),
    }


def _zplanner_week_section(dates: List[str]) -> Dict[str, Any]:
    """週檢視裡 ZPlanner 那一段。**永遠不拋**，不可用就回 reason。

    `dates` 是這支端點要顯示的七天（`dates[0]` 就是送給 ZPlanner 的
    `week_start`）。**要整份七天而不是只要開頭**：回來的資料得先濾回這七天，
    總計才會等於畫面上七個格子的和（見 `_zplanner_week_mismatch`）。
    """
    try:
        client = zplanner_client()
        ok, reason = client.available()
    except Exception as exc:  # noqa: BLE001 - 設定壞掉不該讓週檢視整支失敗
        log.exception("建立 ZPlanner client 失敗（週檢視降級）")
        return {"available": False, "reason": f"ZPlanner client 無法建立：{exc}"}
    if not ok:
        return {"available": False, "reason": reason}

    fetch = getattr(client, ZPLANNER_WEEKLY_METHOD, None)
    if not callable(fetch):
        return {
            "available": False,
            "reason": "這個版本的 ZPlanner client 還沒有週工時端點"
            "（GET /api/worklogs/weekly/），本地草稿不受影響。",
        }
    try:
        data = fetch(dates[0])
    except ChatPulseError as exc:
        return {"available": False, "reason": exc.message}
    except Exception as exc:  # noqa: BLE001
        log.exception("查 ZPlanner 週工時失敗（週檢視降級）")
        return {"available": False, "reason": f"查 ZPlanner 週工時失敗：{exc}"}

    days, error = _zplanner_week_rows(data)
    if days is None:
        return {"available": False, "reason": error}

    # 只留落在這七天裡的。ZPlanner 正規化到週一之後可能回到別的日期（週日
    # 起算時必然如此），而那些日期前端根本沒有格子可以畫——算進總計就是
    # 「表頭與格子對不起來」。
    inside = {d: days[d] for d in dates if d in days}
    section: Dict[str, Any] = {
        "available": True,
        "days": inside,
        "total_hours": f"{sum(float(v['hours']) for v in inside.values()):.2f}",
        "worklog_count": sum(int(v["worklog_count"]) for v in inside.values()),
    }
    mismatch = _zplanner_week_mismatch(data, dates)
    if mismatch is None:
        # target_hours／completion_pct 取不到時整個不出現，前端據此決定要不要
        # 畫「還差多少」那一行。**不要在這裡補預設值**——編出來的目標會讓
        # 完成率變成一個看起來精確的假數字。
        section.update(_zplanner_week_target(data))
    else:
        # 對不起來就**一個總計都不抄**：ZPlanner 的目標與完成率是按它那一週
        # 算的，硬套到我們這七天上會得到一個算錯卻看起來精確的數字。
        section["week_mismatch"] = mismatch
    return section


@app.get("/api/v1/worklogs/week")
def get_worklog_week(
    week_start: str = Query(..., description="這一週的第一天（YYYY-MM-DD）"),
    viewer: Dict[str, Any] = ViewerDep,
):
    """一週的工時概況：本地草稿狀態 ＋ ZPlanner 既有工時。

    七天一律回滿（沒有草稿的那天 `draft` 是 `null`），前端不必自己補空格——
    「這天還沒產草稿」與「這天不在回應裡」必須是同一件事，否則週欄會少一列。

    `week_start` 照傳進來的那天往後算七天。**不強制是星期一**：週的起點是
    前端的顯示決定，後端沒有立場否決；但回應一定同時帶 `week_start` 與
    `week_end`，讓兩邊對得起來。
    """
    viewer_id = viewer["id"]
    start = worklog_evidence.parse_work_date(week_start)  # 格式錯就是 400
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    drafts = {
        row["work_date"]: row
        for row in repo.list_worklog_drafts_in_range(viewer_id, dates[0], dates[-1])
    }
    zplanner = _zplanner_week_section(dates)
    zp_days = zplanner.get("days") or {}

    days = []
    for index, date in enumerate(dates):
        days.append(
            {
                "work_date": date,
                # 1＝星期一。前端要畫「週六/日 休」那兩列，而它不該自己
                # 從字串反推星期幾（那會在跨時區時錯一天）。
                "weekday": (start + timedelta(days=index)).isoweekday(),
                "draft": drafts.get(date),
                "zplanner": zp_days.get(date),
            }
        )

    return {
        "week_start": dates[0],
        "week_end": dates[-1],
        "days": days,
        "totals": {
            "draft_hours": f"{sum(float(d['hours']) for d in drafts.values()):.2f}",
            "days_with_draft": len(drafts),
            "days_submitted": sum(
                1 for d in drafts.values() if d["status"] == "submitted"
            ),
            "entries_unassigned": sum(d["unassigned_count"] for d in drafts.values()),
        },
        # ZPlanner 的帳與本地草稿的帳**分開放**，不混成一個數字：一個是
        # 「已經填進系統的」，一個是「我還沒送出的」，加起來沒有意義。
        "zplanner": {k: v for k, v in zplanner.items() if k != "days"},
    }


class WorklogDraftRequest(BaseModel):
    """產生某一天的工時草稿。

    `total_hours` **省略時不分配**：使用者還沒決定總時數之前，不該看到憑空
    冒出來的數字。這時候 `entries[].hours` 一律是 null，草稿仍然有證據、
    有分群、有候選 issue——那才是這個功能真正在解決的事（回想那天做了什麼）。

    `tz_offset` 必填，沒有預設值。「半夜一點的 commit 算哪一天」必須有一個
    寫得出來的依據，而那個依據是 Viewer 的偏移，不是伺服器剛好設成什麼
    （見 `core/worklog_evidence.py` 的第 3 條規則）。
    """

    tz_offset: str
    #: 省略／空 ＝ 全部啟用中的工作 repo
    repo_ids: Optional[List[int]] = None
    #: 省略 ＝ 釘選的 Space ∪ 當天有 Mention 命中的 Space（絕不掃全部）
    space_ids: Optional[List[str]] = None
    total_hours: Optional[float] = None
    provider: Optional[str] = None
    #: ⚠️ **這裡刻意沒有 `model` 欄位**，另外兩個串流端點有。
    #: 這個端點這一階段不接 AI（見下方 meta：note 來自 commit subject，
    #: `meta.model` 固定 null），收下一個保證無效的參數比不收更糟——呼叫端會
    #: 以為自己選的模型生效了。等這裡接上 AI 再加，連同 `resolve_model()` 一起。
    #: 明示「我知道重產會清掉已經選好的專案，還是要重來」。
    #:
    #: 預設 `False`，因為選專案是這個功能裡最花心力的一步（ADR-0008：系統只
    #: 給候選，一律由 Viewer 選），而重產是整組 DELETE——安靜地清掉那些決定
    #: 跟弄丟它們沒有差別。`force=true` **不能**用來蓋過「已經送進 ZPlanner」
    #: 那條線：worklog id 弄丟了就是永遠對不回來。
    force: bool = False


def _worklog_chat_space_ids(
    viewer_id: int, requested: Optional[List[str]], *, work_date: str, tz_offset: str
) -> List[str]:
    """決定要掃哪些 Space。母體絕不是全部的四百多個（計畫 §8）。

    指定了就用指定的（仍然套 `WORKLOG_MAX_SPACES` 上限）；沒指定就是
    「釘選的 ∪ 當天有 Mention 命中的」。
    """
    if requested is not None:
        for sid in requested:
            if not str(sid).startswith("spaces/"):
                raise InvalidParameter(f"space_ids 含非法值 {sid!r}")
        return worklog_evidence.resolve_chat_space_ids(pinned_space_ids=requested)

    pinned = repo.get_preferences(viewer_id)["pinned_space_ids"]
    start, end = worklog_evidence.day_bounds(work_date, tz_offset)
    mention_spaces: List[str] = []
    for row in repo.list_mentions(viewer_id):
        created = parse_rfc3339(row.get("create_time") or "")
        if created is None or not (start <= created < end):
            continue
        if row["space_id"] not in mention_spaces:
            mention_spaces.append(row["space_id"])
    return worklog_evidence.resolve_chat_space_ids(
        pinned_space_ids=pinned, mention_space_ids=mention_spaces
    )


def _worklog_note(cluster: Any, by_ref: Dict[str, Any]) -> str:
    """這一群的 note。

    ⚠️ **這一階段不接 AI。** note 是該群 commit subject 接起來的字串——
    把管線打通優先，潤飾是後續的事。它會原封不動存進 `worklog_draft_entries.note`
    並在確認畫面可編輯，所以寧可平鋪直敘也不要看起來像生成過的東西：
    使用者若以為這句話是 AI 讀懂他的工作之後寫的，就不會去改它。
    """
    items = [by_ref[ref] for ref in cluster.evidence_refs if ref in by_ref]
    titles = [e.title.strip() for e in items if e.kind == "commit" and e.title.strip()]
    if not titles:  # 純 Chat 的群：退用發言節錄
        titles = [e.title.strip() for e in items if e.title.strip()]
    note = "；".join(titles[:2])
    if len(note) > WORKLOG_NOTE_MAX_CHARS:
        note = note[: WORKLOG_NOTE_MAX_CHARS - 1] + "…"
    return note


def _issue_field(raw: Dict[str, Any], *names: str) -> Any:
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _candidate_shape(raw: Any) -> Optional[Dict[str, Any]]:
    """把 ZPlanner 的一筆 issue 正規化成候選。認不出 id 就丟掉。

    欄位名用多個別名去試，是因為 `worklogable` 與 `projects/{id}/issues`
    兩支的欄位命名實測就不一致（見 `core/zplanner_client.py` 開頭那串）。
    """
    if not isinstance(raw, dict):
        return None
    issue_id = _issue_field(raw, "id", "issue_id", "pk")
    if issue_id is None:
        return None
    return {
        "issue_id": issue_id,
        "issue_key": _issue_field(raw, "key", "issue_key", "code"),
        "title": _issue_field(raw, "title", "subject", "summary", "name") or "",
        "project_id": _issue_field(raw, "project_id", "project"),
        "project_key": _issue_field(raw, "project_key", "project_code"),
    }


def _worklog_permissions(
    client: Any, project_ids: List[Any], email: str
) -> Dict[Any, Dict[str, Any]]:
    """對每個專案查「我能不能填工時」。

    ZPlanner 的權限是**每個專案各自一張角色矩陣、由該專案 PM 自訂**，同一把
    token 在 A 專案能做的事在 B 專案不一定能做——所以沒有「查一次套用全部」
    這種東西，只能逐一查（見 `list_project_role_permissions` 的 docstring）。

    🔴 **查不到就標 `checked: false`，絕不假設有權限。** 猜「應該可以吧」的
    代價是使用者選了一個填不進去的 issue，一路走到送出才被 403 擋掉——
    而那時候整天的草稿都已經填完了。
    """
    out: Dict[Any, Dict[str, Any]] = {}
    for project_id in project_ids[:WORKLOG_MAX_PERMISSION_PROJECTS]:
        entry: Dict[str, Any] = {
            "project_id": project_id,
            "role": None,
            "can_worklog": None,
            "checked": False,
            "reason": "",
        }
        out[project_id] = entry
        try:
            members = client.list_project_members(project_id)
            permissions = client.list_project_role_permissions(project_id)
        except ChatPulseError as exc:
            entry["reason"] = exc.message
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("查 ZPlanner 專案 %s 的權限失敗", project_id)
            entry["reason"] = f"查權限失敗：{exc}"
            continue

        role = None
        for member in members or ():
            if not isinstance(member, dict) or member.get("left_at"):
                continue
            if str(member.get("user_email") or "").strip().lower() == email:
                role = member.get("role")
                break
        if role is None:
            # 比對不到本人。**這不是「沒有權限」**——`members[].user_email`
            # 未必等於 Google 帳號 email（計畫 §7 的卡點），所以答案是
            # 「不知道」，由 UI 去講清楚要找該專案 PM。
            entry["reason"] = f"在這個專案的成員名單裡找不到 {email}"
            continue

        entry["role"] = role
        for rule in permissions or ():
            if isinstance(rule, dict) and rule.get("role") == role:
                entry["can_worklog"] = bool(rule.get("can_worklog"))
                entry["checked"] = True
                break
        if not entry["checked"]:
            entry["reason"] = f"角色 {role} 不在這個專案的權限矩陣裡"
    for project_id in project_ids[WORKLOG_MAX_PERMISSION_PROJECTS:]:
        out[project_id] = {
            "project_id": project_id,
            "role": None,
            "can_worklog": None,
            "checked": False,
            "reason": f"一次最多預檢 {WORKLOG_MAX_PERMISSION_PROJECTS} 個專案的權限",
        }
    return out


def _worklog_candidates(
    clusters: List[Any], *, default_project_ids: set, viewer_email: str
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """對每一群撈候選 issue，並把 `can_worklog` 的預檢結果帶上。

    回 `(每群的候選, meta 的 zplanner 段)`。**ZPlanner 不可用時回空候選而不是
    拋錯**：沒設 ZPlanner 的人仍然該產得出草稿，他只是暫時挑不了 issue。

    信心度分三級，而且**一律附一句可見的理由**（計畫 §7 第 3 條）：不做排名
    徽章、不做百分比、不做進度條——要讓人讀理由而不是讀名次。
    """
    meta: Dict[str, Any] = {
        "available": False,
        "reason": "",
        "queried_terms": [],
        "worklogable_count": 0,
        # 權限比對用的信箱是不是權威來源。計畫 §7 的卡點：
        # `members[].user_email` 未必等於 Google 帳號 email，真正該用的是
        # `preferences.zplanner_email`——**那個欄位還不存在**（這次不動
        # core/db.py），所以現階段一律退用 viewer.email，這個旗標一律是 false。
        "permission_checked": False,
        "permission_email": viewer_email,
        "permission_email_source": "viewer_email",
        "permission_note": "",
        "projects_checked": [],
        "errors": [],
    }
    by_cluster: Dict[str, List[Dict[str, Any]]] = {c.key: [] for c in clusters}

    try:
        client = zplanner_client()
        ok, reason = client.available()
    except Exception as exc:  # noqa: BLE001
        log.exception("建立 ZPlanner client 失敗（候選 issue 降級）")
        meta["reason"] = f"ZPlanner client 無法建立：{exc}"
        return by_cluster, meta
    if not ok:
        meta["reason"] = reason
        return by_cluster, meta
    meta["available"] = True

    seen_issue_ids = set()
    for cluster in clusters:
        term = cluster.query_term
        meta["queried_terms"].append(term)
        try:
            # `q` 必填。空字串回 0 筆而不是全部，所以 `query_term` 保證有值。
            issues = client.list_worklogable_issues(term)
        except ChatPulseError as exc:
            meta["errors"].append({"cluster": cluster.key, "term": term, "reason": exc.message})
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("查 ZPlanner 候選 issue 失敗（關鍵字 %s）", term)
            meta["errors"].append(
                {"cluster": cluster.key, "term": term, "reason": f"查詢失敗：{exc}"}
            )
            continue

        ranked: List[Tuple[int, Dict[str, Any]]] = []
        for raw in issues:
            candidate = _candidate_shape(raw)
            if candidate is None:
                continue
            seen_issue_ids.add(candidate["issue_id"])
            if cluster.issue_key and candidate["issue_key"] == cluster.issue_key:
                rank, source = 0, "issue_key"
                why = f"你在 commit 訊息裡直接寫了 {cluster.issue_key}"
                confidence = "strong"
            elif candidate["project_id"] in default_project_ids:
                rank, source = 1, "default_project"
                why = "這是你替該 repo 設定的預設 ZPlanner 專案底下的 issue"
                confidence = "medium"
            else:
                rank, source = 2, "search"
                why = f"用關鍵字「{term}」在可填工時的 issue 裡搜到的"
                confidence = "weak"
            candidate.update(
                {"source": source, "confidence": confidence, "reason": why}
            )
            ranked.append((rank, candidate))

        ranked.sort(key=lambda pair: (pair[0], str(pair[1]["issue_key"] or "")))
        by_cluster[cluster.key] = [c for _, c in ranked[:WORKLOG_MAX_CANDIDATES]]

    meta["worklogable_count"] = len(seen_issue_ids)

    # 權限預檢：只查真的出現在候選裡的專案
    project_ids: List[Any] = []
    for candidates in by_cluster.values():
        for candidate in candidates:
            pid = candidate["project_id"]
            if pid is not None and pid not in project_ids:
                project_ids.append(pid)
    permissions = _worklog_permissions(client, project_ids, (viewer_email or "").lower())
    meta["projects_checked"] = list(permissions.values())
    meta["permission_note"] = (
        f"用登入的 Google 信箱（{viewer_email or '未知'}）比對 ZPlanner 成員名單。"
        "那不保證是同一個帳號，所以 permission_checked 一律是 false；"
        "要確定得先有 preferences.zplanner_email。"
    )
    for candidates in by_cluster.values():
        for candidate in candidates:
            info = permissions.get(candidate["project_id"]) or {}
            candidate["can_worklog"] = info.get("can_worklog")
            candidate["permission_checked"] = bool(info.get("checked"))
            candidate["permission_reason"] = info.get("reason") or ""
    return by_cluster, meta


def _refuse_destructive_regeneration(
    date: str, entries: List[Dict[str, Any]], *, force: bool
) -> None:
    """重產前的守門。重產 ＝ `replace_worklog_entries` 整組 DELETE 再 INSERT。

    那次 DELETE 會帶走兩種東西，性質完全不同，所以擋法也不同：

      1. **`zplanner_worklog_id`**——修改／刪除遠端工時的唯一入口。本地刪掉
         不會讓 ZPlanner 那側跟著消失，只會讓兩邊永遠對不回來，之後只剩
         correction-request。這條線 **`force` 也不給過**。
      2. **Viewer 已經選好的 issue**——ADR-0008 的唯一硬規則是「系統只給候選，
         一律由人選」，所以這是整個功能裡最花心力的一步。它可以重選，但
         **不可以沒人講一聲就清掉**：擋下來，說清楚會影響幾筆，由呼叫端帶
         `force=true` 明示。

    兩種都把筆數放進 `detail`（dict → 會進回應的 `error.detail`，見
    `chatpulse_error_handler`），前端才不必從中文句子裡用正則挖數字。
    """
    sent = [e for e in entries if e["zplanner_worklog_id"]]
    if sent:
        raise WorklogAlreadySubmitted(
            f"{date} 已經有 {len(sent)} 筆工時送進 ZPlanner 了，不能重產草稿。"
            "重產會弄丟那幾筆的 worklog id，之後只能走 correction-request。",
            detail={"submitted_count": len(sent), "force_allowed": False},
        )

    assigned = [e for e in entries if e["issue_id"] or e["issue_key"]]
    if assigned and not force:
        keys = [str(e["issue_key"]) for e in assigned if e["issue_key"]]
        shown = ("（" + "、".join(keys[:5]) + "）") if keys else ""
        raise InvalidParameter(
            f"{date} 的草稿裡有 {len(assigned)} 筆已經選好專案了{shown}，"
            "重產會把那些選擇整組清掉。確定要重來請帶 force=true。",
            detail={
                "assigned_count": len(assigned),
                "assigned_issue_keys": keys,
                "force_allowed": True,
            },
        )


@app.post("/api/v1/worklogs/{work_date}/draft/stream")
def worklog_draft_stream(
    work_date: str, req: WorklogDraftRequest, viewer: Dict[str, Any] = ViewerDep
):
    """產生某一天的工時草稿（**SSE**）。

    事件序：`meta` → 每群一個 `chunk` → `done`；沒有任何 commit／發言則是
    單一個 `error`（`NO_EVIDENCE`，SSE-only code，比照既有的 `NO_MESSAGES`）。
    **事件型別不新增**——前端只要認得這四種。

    `meta` 是證據欄的唯一來源：任何無法回溯到 meta 某個欄位的東西，都不准
    進證據欄（設計紅線）。所以這裡漏掉什麼，前端就永遠顯示不出來。

    ⚠️ `done` 的 `entries[]` 裡 **`issue_id`／`issue_key` 一律是 null**。
    系統只給候選，選擇權在 Viewer——ADR-0008 的唯一硬規則。一份看起來合理的
    自動歸屬，代價由一個月後發現填錯的人支付（月結後要走 correction-request）。
    """
    viewer_id = viewer["id"]
    # 以下全部擋在 SSE 開始之前：串流一旦開始就是 HTTP 200，之後的錯誤只能
    # 變成 error 事件，使用者比較難注意到（同 draft_stream 的處置）。
    date = worklog_evidence.parse_work_date(work_date).strftime("%Y-%m-%d")
    tz_offset = worklog_evidence.normalize_tz_offset(req.tz_offset)
    resolved_provider = providers.resolve_name(req.provider) if req.provider else None
    space_ids = _worklog_chat_space_ids(
        viewer_id, req.space_ids, work_date=date, tz_offset=tz_offset
    )

    repos = repo.list_work_repos(viewer_id, enabled_only=True)
    if req.repo_ids:
        wanted = set(req.repo_ids)
        repos = [r for r in repos if r["id"] in wanted]
        missing = wanted - {r["id"] for r in repos}
        if missing:
            raise WorkRepoNotFound(
                f"找不到（或已停用）工作 repo {', '.join(str(i) for i in sorted(missing))}"
            )

    existing = repo.get_worklog_draft(viewer_id, date)
    if existing:
        _refuse_destructive_regeneration(date, existing["entries"], force=req.force)

    def generate() -> Generator[str, None, None]:
        try:
            evidence = worklog_evidence.collect_day_evidence(
                work_date=date,
                tz_offset=tz_offset,
                work_repos=repos,
                chat_client=get_client(viewer_id) if space_ids else None,
                space_ids=space_ids,
                google_user_id=viewer.get("google_user_id") or "",
                space_names={sid: space_display_name(viewer_id, sid) for sid in space_ids},
            )

            if evidence.is_empty:
                # 這不是錯誤狀態，是「那天沒有留下痕跡」。硬產一份空草稿會讓
                # 使用者把時數分給不存在的證據，那比什麼都不給更糟。
                yield sse(
                    {
                        "type": "error",
                        "code": "NO_EVIDENCE",
                        "message": f"{date} 沒有找到任何 commit 或發言。"
                        "確認一下工作 repo 的作者信箱設定，或換一天試試。",
                    }
                )
                return

            clusters = worklog_clustering.build_clusters(evidence.evidence)
            by_ref = {e.ref: e for e in evidence.evidence}
            candidates, zplanner_meta = _worklog_candidates(
                clusters,
                default_project_ids={
                    r["default_zplanner_project_id"]
                    for r in repos
                    if r["default_zplanner_project_id"] is not None
                },
                viewer_email=viewer.get("email") or "",
            )

            meta_event: Dict[str, Any] = {"type": "meta", **evidence.to_meta()}
            meta_event["clusters"] = [
                {**c.to_meta(), "candidates": candidates.get(c.key, [])}
                for c in clusters
            ]
            meta_event["zplanner"] = zplanner_meta
            # 這一階段不接 AI，但欄位保留：前端的證據欄有「生成」那一列，
            # 而「沒有用模型」本身就是要顯示的事實，不是空著不講。
            meta_event["provider"] = resolved_provider
            meta_event["model"] = None
            meta_event["note_source"] = "commit_subject"
            yield sse(meta_event)

            notes = {c.key: _worklog_note(c, by_ref) for c in clusters}
            for index, cluster in enumerate(clusters):
                yield sse(
                    {
                        "type": "chunk",
                        "index": index,
                        "cluster_key": cluster.key,
                        "text": notes[cluster.key],
                    }
                )

            allocation = None
            if req.total_hours is not None:
                # 省略 total_hours 就不分配：使用者還沒決定總時數之前，
                # 不該看到憑空冒出來的數字。
                allocation = worklog_allocation.allocate(
                    req.total_hours, [c.to_allocation_item() for c in clusters]
                )
            allocated = {e.index: e for e in (allocation.entries if allocation else ())}

            # 寫之前再查一次「這一天有沒有已經送出的工時」。
            #
            # 正確性不靠這一道——`replace_worklog_entries(refuse_submitted=True)`
            # 已經在同一個交易裡把關了。這裡多查一次是為了**不留副作用**：
            # `upsert_worklog_draft` 會更新 generation_meta 與 generated_at，
            # 而它排在 replace 前面。少了這一道，一次被拒絕的重產仍然會把證據欄
            # 換成新證據、entries 卻還是舊的——畫面上兩邊對不起來，而使用者
            # 剛剛才收到一句「已經送出了，不能重產」。
            #
            # 被拒絕的操作不該留下任何痕跡，這是那一道的全部理由。
            current = repo.get_worklog_draft(viewer_id, date)
            if current:
                _refuse_destructive_regeneration(
                    date, current["entries"], force=req.force
                )

            draft = repo.upsert_worklog_draft(
                viewer_id,
                date,
                tz_offset=tz_offset,
                status="draft",
                total_hours=(
                    None if req.total_hours is None else f"{float(req.total_hours):.2f}"
                ),
                generation_meta=meta_event,
                generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            rows = []
            for index, cluster in enumerate(clusters):
                entry = allocated.get(index)
                rows.append(
                    {
                        # 🔴 ADR-0008 的唯一硬規則：產生時歸屬一律留空。
                        "issue_id": None,
                        "issue_key": None,
                        "project_id": None,
                        "project_key": None,
                        "hours": entry.allocated_hours if entry else None,
                        "allocated_hours": entry.allocated_hours if entry else None,
                        "note": notes[cluster.key],
                        "weight": cluster.weight,
                        "rounded_up": bool(entry.rounded_up) if entry else False,
                        "evidence_refs": list(cluster.evidence_refs),
                        "candidates": candidates.get(cluster.key, []),
                        "state": "pending",
                    }
                )
            # `refuse_submitted=True`：上面那道守門在掃 git／打 ZPlanner **之前**
            # 就跑完了，中間可能過了數十秒。這段時間若有另一條路徑把工時送出去，
            # 整組取代就會把剛拿到的 `zplanner_worklog_id` 抹掉——所以真正的
            # 判定要跟 DELETE 在同一個交易裡再做一次（見 repository 的 docstring）。
            saved = repo.replace_worklog_entries(
                viewer_id, draft["id"], rows, refuse_submitted=True
            )
            assert saved is not None

            yield sse(
                {
                    "type": "done",
                    "draft_id": draft["id"],
                    "entries": [
                        {
                            "id": row["id"],
                            "seq": row["seq"],
                            "cluster_key": clusters[i].key,
                            "label": clusters[i].label,
                            "reason": clusters[i].reason,
                            "issue_id": row["issue_id"],
                            "issue_key": row["issue_key"],
                            "hours": row["hours"],
                            "allocated_hours": row["allocated_hours"],
                            "rounded_up": row["rounded_up"],
                            "weight": row["weight"],
                            "note": row["note"],
                            "evidence_refs": row["evidence_refs"],
                            "candidates": row["candidates"],
                            "state": row["state"],
                        }
                        for i, row in enumerate(saved["entries"])
                    ],
                    "allocation": allocation.to_dict() if allocation else None,
                }
            )
        except ChatPulseError as exc:
            yield sse(exc.to_sse_event())
        except Exception as exc:  # 串流已開始，只能用事件回報
            log.exception("工時草稿串流失敗")
            yield sse(
                {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": f"工時草稿產生失敗：{exc}",
                }
            )

    return sse_response(generate())


# --------------------------------------------------------------------------
# 送出：把一天份的草稿逐筆寫進 ZPlanner
#
# 這一段的每個決定都繞著同一件事轉：**重複的工時在月結之後刪不掉**，
# 只能走 correction-request（要人審核）。所以整段的偏好一律是
# 「寧可漏送，不要重送」——漏送看得見（草稿還停在那裡），重送看不見。
# --------------------------------------------------------------------------


class WorklogSubmitRequest(BaseModel):
    """把某一天的工時草稿送進 ZPlanner。

    `entry_ids` 省略 ＝ 送整天。給了就只處理那幾筆——`failed`／`unknown` 的
    單筆重打走的是**同一支端點**，不另開路徑：重打與整批的流程完全一樣，
    另開等於把同一段邏輯寫兩次，然後在其中一份忘記修 bug。

    ⚠️ 帶了 `entry_ids` 時，「有筆沒選專案就擋下整批」那道閘**只檢查選中的
    那幾筆**。不這樣做的話，當天別處只要有一筆還沒選專案，`failed` 的那筆
    就永遠重打不了——而重打正是這個欄位存在的唯一理由。
    """

    entry_ids: Optional[List[int]] = None


def _worklog_submit_targets(
    entries: List[Dict[str, Any]], entry_ids: Optional[List[int]]
) -> List[Dict[str, Any]]:
    """挑出這次要處理的幾筆，並把「指錯 id」變成 400。

    挑選本身在 `worklog_submit.select_entries`（含「照 seq 排、不照 entry_ids
    的順序」那條規則）。這裡只做兩件屬於 HTTP 層的事：把空陣列與指錯的 id
    翻成錯誤信封。

    指到不屬於這份草稿的 id 一律回 **400，不是 404**。404 的語意是「這一天
    還沒有草稿」，前端收到它會去畫「還沒產草稿」的空狀態——但草稿明明就在，
    錯的是送過來的 id。兩種情況畫面不一樣，共用一個碼會讓人以為草稿不見了。
    """
    if entry_ids is not None and not entry_ids:
        raise InvalidParameter(
            "entry_ids 是空陣列。要送整天請把這個欄位整個省略——空陣列會送出 0 筆"
            "卻回成功，而那種「成功」分不出是前端漏帶了 id，還是真的沒東西要送。"
        )
    selection = worklog_submit.select_entries(entries, entry_ids)
    if selection.unknown_ids:
        missing = list(selection.unknown_ids)
        raise InvalidParameter(
            f"entry_ids 裡的 {', '.join(str(i) for i in missing)} 不屬於這一天的草稿。",
            detail={
                "unknown_entry_ids": missing,
                "draft_entry_ids": sorted(e["id"] for e in entries),
            },
        )
    return list(selection.selected)


def _refuse_incomplete_worklog_entries(
    date: str, entries: List[Dict[str, Any]], entry_ids: Optional[List[int]]
) -> None:
    """送出前的守門：**有任何一筆送不出去，就一筆都不送**（AC-1／D-3）。

    擋兩種「這筆還沒準備好」，它們的成因與補救動作完全不同，所以分開數、
    分開講——訊息只寫「有 3 筆不能送」的話，使用者不知道要去選專案還是去
    填時數：

      1. **沒選 ZPlanner 專案**（`issue_id` 是空的）。ADR-0008 的唯一硬規則是
         歸屬由人選，系統不會替他填。
      2. **沒有時數**。⚠️ 這不是罕見的邊界情況：產草稿時省略 `total_hours`
         就不分配，那時候**整份草稿的 `hours` 一律是 null**（見
         `WorklogDraftRequest`）——那是正常流程的中間狀態，不是壞掉的資料。

    為什麼要擋在前面，而不是讓它們在迴圈裡各自變成 `failed`：兩種筆送到
    `create_worklog` 都會在**發出 HTTP 之前**被擋下（`_require_int`／
    `_require_hours`），所以確實不會造成重複工時。但那樣做等於**部分送出**——
    D-3 明文否決過：那天的工時會變成不完整，而畫面上沒有任何訊號說明少了什麼。

    帶 `entry_ids` 時只檢查選中的那幾筆（判斷在 `worklog_submit`，這裡只是
    把它翻成 400）。否則當天別處有一筆沒選專案，`failed` 的那筆就永遠重打不了。

    兩個計數都放進 `detail`（dict → 會進回應的 `error.detail`，見
    `chatpulse_error_handler`），前端才不必從中文句子裡用正則挖數字。
    **兩個鍵一律都在**，即使是 0——形狀穩定比少幾個位元組重要。
    """
    unassigned = worklog_submit.find_unassigned(entries, entry_ids=entry_ids)
    missing_hours = worklog_submit.find_missing_hours(entries, entry_ids=entry_ids)
    if not unassigned and not missing_hours:
        return

    reasons = []
    if unassigned:
        reasons.append(f"{len(unassigned)} 筆還沒選 ZPlanner 專案（去挑一個候選 issue）")
    if missing_hours:
        reasons.append(f"{len(missing_hours)} 筆沒有時數（先輸入當天的總時數讓它分配）")
    raise InvalidParameter(
        f"{date} 的草稿還不能送出：{'、'.join(reasons)}。整批都不會送出——"
        "先送一半會讓這天的工時變成不完整，而且看不出少了哪幾筆。",
        detail={
            "unassigned_count": len(unassigned),
            "unassigned_entries": [e.to_dict() for e in unassigned],
            "missing_hours_count": len(missing_hours),
            "missing_hours_entries": [e.to_dict() for e in missing_hours],
        },
    )


def _count_state(entries: List[Dict[str, Any]], state: str) -> int:
    """整份草稿裡有幾筆是這個 state。鍵名沿用週檢視，兩個畫面才講同一套話。"""
    return sum(1 for e in entries if e["state"] == state)


# --------------------------------------------------------------------------
# AC-9：同一天同時只准有一個送出在跑
# --------------------------------------------------------------------------

#: 正在送出的 `(viewer_id, work_date)`。用 set 而不是「每個 key 一把 Lock」的
#: dict：後者會隨著使用過的日期無界成長（沒有人會回頭刪），而我們要的資訊只有
#: 「這個 key 在不在飛」這一個位元。
_worklog_submit_running: set = set()
#: 只保護上面那個 set 的讀寫，**不是**拿來讓請求排隊的。持有時間是幾個指令，
#: 不涵蓋任何網路或 DB 動作。
_worklog_submit_lock = threading.Lock()


@contextmanager
def _claim_worklog_submit(viewer_id: int, date: str) -> Generator[None, None, None]:
    """搶下「這個 Viewer 的這一天」的送出權；搶不到就**立刻**拋 409（AC-9／D-10）。

    🔴 **為什麼需要這道鎖。** 兩個重疊的送出會各自查一次「ZPlanner 這天已經有
    哪些工時」當去重底稿，而兩份底稿都是在對方寫入之前拍的——於是去重對兩邊
    都說「沒有重複」，同一筆草稿被送出兩次。2026-09-14 實測（兩條執行緒相隔
    20ms 打同一天、草稿只有 2 筆）：`create_worklog` 被呼叫 **4 次**，ZPlanner
    多出 2 筆工時，而先回的那個請求回報 `submitted=2 failed=0 unknown=0
    orphans=0`——**重複工時在它身上完全隱形**。重複工時月結後刪不掉，只能走
    correction-request（要人審核）。這支端點存在的唯一理由就是防這件事。

    🔴 **非阻塞：搶不到就拒絕，不排隊等。** 排隊的版本看起來比較友善，實際上
    是把第二個請求卡住最長一整個時間預算（90 秒），而使用者等不到回應時最常做
    的事就是再按一次送出鈕——要防的動作被原地放大，而且放大成排隊中的第三、
    第四個請求。立刻回 409 讓前端能馬上說「正在送，等它跑完」。

    ⚠️ **已知邊界：這道鎖只在單一行程內有效。** 部署模型是一人一部署、單 worker
    uvicorn（見本檔最後的 `uvicorn.run`），所以涵蓋得住。但**多 worker／多行程
    部署時，兩個請求會各自拿到自己行程的鎖，這道防護等於不存在**——那時必須改
    成 DB 認領（在 `worklog_draft_entries` 上用一句帶守門條件的 UPDATE 把要送的
    筆認領下來，讓 SQLite 的交易當仲裁者）。這是 D-10 刻意選的取捨，不是疏漏：
    DB 認領會新增一個失敗模式（行程崩潰時那些筆永久卡在認領中，需要另做回收
    機制）來防另一個失敗模式。要多 worker 就得先付那筆帳。

    `finally` 一定要把 key 移掉，否則一次例外就讓那一天**永遠**送不出去
    （只能重啟行程）。所以整段用 contextmanager 包起來，不留「某條 return 路徑
    忘了還鎖」的縫。

    🔴 **`date` 必須是已經正規化過的 `YYYY-MM-DD`**（呼叫端先跑
    `parse_work_date(...).strftime("%Y-%m-%d")`）。把 claim 移到正規化之前的話，
    `2026-9-13` 與 `2026-09-13` 會變成兩把不同的鎖，同一天就能靠「不補零」繞過
    整道防護——而繞過之後的症狀就是這個 docstring 開頭講的那件事，沒有任何
    錯誤訊息。
    """
    key = (viewer_id, date)
    with _worklog_submit_lock:
        if key in _worklog_submit_running:
            raise WorklogSubmitInProgress(
                f"{date} 的工時正在送出中，這次請求一筆都沒有送。"
                "同一天同時送兩次會讓兩邊各自拿到過期的去重底稿，"
                "在 ZPlanner 造出重複工時，而重複工時月結後刪不掉。"
                # ⚠️ **不要在這裡寫死秒數。** 預算只擋「開始新的一筆」，最後一筆
                # 可以在 t=89.9s 起跑再吃一次 `(5, 20)`，實際上限約兩分鐘；慢速
                # 吐 chunk 時更久。寫「最多 90 秒」的話，等滿 90 秒仍拿到 409 的
                # 人會以為卡死了——那正是他再按一次的理由。
                "等前一次跑完（約一到兩分鐘）再看結果。",
                detail={"work_date": date},
            )
        _worklog_submit_running.add(key)
    try:
        yield
    finally:
        with _worklog_submit_lock:
            _worklog_submit_running.discard(key)


# --------------------------------------------------------------------------
# AC-10：整批的時間預算
# --------------------------------------------------------------------------

#: 整批送出的時間上限（秒）。到點就停送剩下的，已經送出去的不動。
#:
#: 🔴 **這個預算不能靠 client 的 timeout 代替。** `ZPlannerClient` 的
#: `(5, 20)` 是 connect 與**每一次 read** 的上限，不是單次請求的總長，更不是
#: 整批的總長：對方慢速吐 chunk 的話單次請求可以無上限地拖下去，而 N=10 的
#: 一批最壞情況約 275 秒。
#:
#: 🔴 **也不准改用「縮短 read timeout」來達成。** 那會讓首 byte 比較慢的**正常**
#: 請求被砍斷，而被砍斷的寫入是 `unknown`（不知道寫進去沒有、要人工對帳），
#: 比乾脆沒送出去更糟。要限制的是整批，不是單筆。
WORKLOG_SUBMIT_BUDGET_SECONDS = 90.0


def _worklog_submit_client() -> ZPlannerClient:
    """送出用的 ZPlanner client。**不可用就拋**，不像週檢視那樣降級。

    週檢視沒設 ZPlanner 也該看得到本地草稿，所以那邊回 `available: false`
    照常把草稿吐出來。這支端點整個存在的目的就是對 ZPlanner 寫入——降級之後
    一件事都做不成，回一個「送出 0 筆」的 200 會讓人以為工時已經填好了。
    """
    try:
        client = zplanner_client()
        ok, reason = client.available()
    except ChatPulseError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("建立 ZPlanner client 失敗（送出工時）")
        raise ConfigurationError(f"ZPlanner client 無法建立：{exc}") from exc
    if not ok:
        raise ConfigurationError(f"沒辦法把工時送進 ZPlanner：{reason}")
    return client


def _submit_one_worklog_entry(
    client: ZPlannerClient,
    entry: Dict[str, Any],
    *,
    viewer_id: int,
    draft_id: int,
    date: str,
) -> Optional[Dict[str, Any]]:
    """送出一筆，並**立刻**把結果落 DB。回傳孤兒描述，沒有孤兒就回 None。

    立刻落地是 D-4：等整批跑完再一次寫，第 N 筆失敗時前面那幾筆的
    `zplanner_worklog_id` 還在記憶體裡就沒了——ZPlanner 那側的工時仍然存在，
    但本地永遠不知道它是誰的，下次重送會再建一次。

    `update_worklog_entry_state` 回 `False` ＝ 影響 0 列 ＝ 這一筆已經不符合
    UPDATE 的守門條件了（守門併在 WHERE 裡，不先查再寫）。此時 ZPlanner 那側
    **已經有一筆工時**而本地沒有任何東西指向它，這就是孤兒。
    🔴 孤兒一定要把 `zplanner_worklog_id` 原封不動回報出去——那是唯一能再找到
    它的線索，吞掉的話那筆工時就永遠只能靠人翻 ZPlanner 網頁找。

    ⚠️ **不要在 reason 裡替它斷定成因**（AC-11）。這段原本寫死成「送出期間草稿
    被重新產生」，而 2026-09-14 的實測顯示真正跑出孤兒的是**並行送出**（兩個
    重疊的送出，先跑完的那個把 id 寫上去，後到的那個守門條件就不成立了）。
    寫死一個成因的代價不是話講得不夠準，是**人照那句去查重產紀錄，永遠查不到
    東西**，然後把一筆真實存在的遠端工時當成誤報放掉。
    """
    try:
        created = client.create_worklog(
            entry["issue_id"],
            date,
            entry["hours"],
            # 空字串的 note 不送。`None ＝ 整個不送` 是 client 的約定，
            # 送一個空字串上去只是讓 ZPlanner 那側多一筆看起來像壞掉的資料。
            note=entry["note"] or None,
        )
    except BaseException as exc:  # noqa: BLE001 - 分類完就繼續跑，單筆失敗不中斷整批
        # 「這筆到底寫進去了沒有」的判斷全在 worklog_submit（判準只有一條：
        # 只有拿到 `code != 200` 的回應信封才證明沒寫進去）。這裡只負責把
        # 例外身上的碼與訊息取出來落 DB——那是搬字串，不是判斷。
        state = worklog_submit.classify_write_error(exc)
        if isinstance(exc, ChatPulseError):
            error_code, error_message = exc.code, exc.message
        else:
            error_code = "INTERNAL_ERROR"
            error_message = f"送出時發生未預期的錯誤（{type(exc).__name__}）：{exc}"
        if state == worklog_submit.STATE_UNKNOWN:
            error_message = (
                f"{error_message}｜這筆**可能已經寫進 ZPlanner 了**，所以不會自動重試。"
                "請先用 ZPlanner 的匯出查這一天對帳，確認沒有之後再重送。"
            )
            log.error(
                "送出工時結果不明（draft=%s entry=%s issue=%s）：%s",
                draft_id, entry["id"], entry["issue_id"], error_message,
            )
        else:
            log.warning(
                "送出工時失敗（draft=%s entry=%s issue=%s）：%s",
                draft_id, entry["id"], entry["issue_id"], error_message,
            )
        repo.update_worklog_entry_state(
            viewer_id, draft_id, entry["id"],
            state=state, error_code=error_code, error_message=error_message,
        )
        return None

    worklog_id = created.get("id")
    if worklog_id is None:
        # 🔴 這一筆算 `unknown`，**不是 `failed`**。
        #
        # 走到這裡代表 ZPlanner 的應用層回了成功（`_request` 已經確認過信封的
        # `code == 200`），只是回應主體裡沒有 id。也就是說**那筆工時很可能已經
        # 建好了**，我們只是拿不到它的號碼。標成 `failed` 的話，重打會再建
        # 一次——那正是這整段最想避免的事。`unknown` 的語意就是「不知道有沒有
        # 寫進去，不准自動重試，請先對帳」。
        message = (
            f"ZPlanner 回報建立成功，但回應裡沒有 worklog id（拿到的欄位："
            f"{sorted(created)[:10]}）。這筆工時很可能已經建好了，只是對不回來——"
            "請用 ZPlanner 的匯出功能查這一天，確認之後再決定要不要重送。"
        )
        log.error("建立工時後拿不到 id（draft=%s entry=%s）：%r", draft_id, entry["id"], created)
        repo.update_worklog_entry_state(
            viewer_id, draft_id, entry["id"],
            state=worklog_submit.STATE_UNKNOWN,
            error_code="ZPLANNER_API_ERROR",
            error_message=message,
        )
        return None

    ok = repo.update_worklog_entry_state(
        viewer_id, draft_id, entry["id"],
        state=worklog_submit.STATE_SUBMITTED,
        zplanner_worklog_id=worklog_id,
        # 回應的 issue_key 優先於本地那份：本地那份是選候選時抄下來的，
        # 期間 issue 有可能被改名，而對帳時要用的是 ZPlanner 現在的說法。
        zplanner_issue_key=created.get("issue_key") or entry["issue_key"],
        # 直接抄回應的欄位。「送出時沒鎖、之後月結才鎖」這支端點看不到，
        # 那要靠對帳補——這裡只記錄送出當下的事實。
        is_locked=bool(created.get("is_locked")),
        error_code=None,
        error_message=None,
    )
    if ok:
        return None
    return {
        "entry_id": entry["id"],
        "zplanner_worklog_id": worklog_id,
        "issue_key": created.get("issue_key") or entry["issue_key"],
        "hours": entry["hours"],
        "reason": (
            "這筆工時已經建進 ZPlanner，但要寫回本地草稿時，那一筆已經不符合"
            "寫回條件了。ZPlanner 那側的工時仍然存在，"
            f"編號 {worklog_id}——本地沒有任何東西指向它，要改要刪都得直接去 ZPlanner。"
        ),
        # 🔴 **列出可能成因，不挑一個當定論**（AC-11）。這個欄位曾經寫死成
        # 「送出期間草稿被重新產生」，而實測跑出孤兒的其實是並行送出——人照那句
        # 去查重產紀錄會查不到任何東西，於是把一筆真的存在的遠端工時當成誤報。
        # 三個成因在這一層都分不出來（UPDATE 只回「影響 0 列」，不回為什麼）。
        "possible_causes": [
            "同一天另一個送出同時在跑，它先把這一筆寫成 submitted 了",
            "送出期間草稿被重新產生，這一筆已經不存在",
            "這一筆被其他路徑改成了別的狀態",
        ],
    }


@app.post("/api/v1/worklogs/{work_date}/submit")
def submit_worklog(
    work_date: str,
    req: WorklogSubmitRequest,
    viewer: Dict[str, Any] = ViewerDep,
):
    """把某一天的工時草稿逐筆送進 ZPlanner。

    ⚠️ **最容易誤讀的一點：HTTP 200 不代表每一筆都送成功。** 只要這一批開始
    送了，回的就是 200，逐筆的成敗在 `entries[].state` 與 `summary` 裡。
    這是刻意的——用 4xx 表示「有筆失敗」會讓前端顯示成「整批沒送出」，
    使用者就會再按一次送出鈕，而 ZPlanner 那側已經有的那幾筆會變成重複工時，
    月結之後刪不掉。錯誤信封只留給**一筆都沒送**的整批拒絕（沒選專案或沒填
    時數的 400、這一天沒有草稿的 404、這一天正在送出中的 409、ZPlanner 整個
    連不上）。

    第二點：`summary` 的四個計數描述的是**重讀之後整份草稿**的狀態，不是
    「這一次送了幾筆」。partial 重打時 `submitted_count` 會含上次就已經送成功
    的那幾筆——它跟回應裡的 `entries[]` 是同一份帳，前端拿它直接重畫那一天。
    只有 `skipped_duplicates` 與 `orphans` 是**這一次**才有的資訊：它們在 DB 裡
    沒有對應欄位可以重算，這個回應是它們唯一的出口（見下）。

    三個「安靜地做了但你看不到」的風險，都有對應的欄位頂住：

      1. **去重跳過的筆**進 `summary.skipped_duplicates`。去重比對 issue ＋
         時數、不比對 note，所以「同一天、同一個 issue、同樣時數」的第二筆
         合法工時會被誤殺。這個取捨是刻意的（重送的代價不可逆），但誤殺
         **必須看得見**——這份清單就是它唯一的出口，`state=skipped` 那一筆
         身上刻意不寫遠端 id（認領一筆不是我們建的工時，會讓誤殺變成永久）。
      2. **孤兒**進 `summary.orphans`：工時進了 ZPlanner，但要寫回草稿時那筆
         已經不符合寫回條件了。裡面帶著 `zplanner_worklog_id`，那是唯一還找得
         到它的線索，另附 `possible_causes`——**成因在這一層分不出來，所以列
         出來而不是挑一個當定論**（AC-11）。
      3. **`unknown`** 是「不知道送進去沒有」，不是「失敗」。這種筆**不會**被
         自動重試，要先對帳（`export_worklogs` 查那一天）再決定。
      4. **超時停送的筆**進 `summary.not_attempted_count`（AC-10）。整批有 90
         秒預算，到點就不再開始新的一筆（**不砍進行中的那一筆**——砍掉會變成
         要人工對帳的 `unknown`）。停送的筆維持 `pending`，在 DB 裡與「從未送
         過」長得一樣，所以這個數字是它唯一的訊號。

    最後：同一天同時只准有一個送出在跑，第二個**立刻**回 409
    （`WORKLOG_SUBMIT_IN_PROGRESS`，不排隊等）。兩個重疊的送出會各自拿到一份
    在對方寫入前拍的去重底稿，於是同一筆被送兩次——見 `_claim_worklog_submit`
    的實測數字，以及它寫明的「多 worker 部署時這道防護不存在」。
    """
    viewer_id = viewer["id"]
    date = worklog_evidence.parse_work_date(work_date).strftime("%Y-%m-%d")

    # AC-9：整段送出（去重底稿的查詢、逐筆送出、重讀與 status 寫回）都在同一個
    # claim 底下——鎖要蓋住的不只是迴圈，還有那份底稿（查完到用完之間被別人寫了
    # 東西，底稿就過期了）。claim 擺在讀草稿**之前**，讓兩個送出重疊的窗口小到
    # 塞不進東西；搶不到就立刻 409、一筆都不送（為什麼不排隊等見該函式）。
    with _claim_worklog_submit(viewer_id, date):
        return _submit_worklog_batch(viewer_id, date, req.entry_ids)


def _submit_worklog_batch(
    viewer_id: int, date: str, entry_ids: Optional[List[int]]
) -> Dict[str, Any]:
    """送出一天份草稿的編排本體。🔴 **呼叫端必須先拿到 `_claim_worklog_submit`。**

    單獨抽出來不是為了讓函式短一點，是為了讓「整段都在鎖裡面」這件事在路由那
    一層一眼看得出來。回應的形狀與各欄位語意見 `submit_worklog` 的 docstring。
    """
    draft = repo.get_worklog_draft(viewer_id, date)
    if draft is None:
        raise WorklogDraftNotFound(
            f"{date} 還沒有工時草稿，沒有東西可以送出。先產一份草稿再送。"
        )

    targets = _worklog_submit_targets(draft["entries"], entry_ids)

    # AC-1／D-3：有任何一筆送不出去（沒選專案、或沒有時數）就擋下整批。
    _refuse_incomplete_worklog_entries(date, draft["entries"], entry_ids)

    # 已經結束的筆不再送。`submitted`／有 worklog id ＝ 送過了（AC-2，重打就是
    # 製造重複工時）；`skipped` ＝ 人或去重已經判過「不要送」，重新算進來等於
    # 推翻那個決定。判準與 `worklog_submit.find_duplicates` 用的是同一套。
    done = (worklog_submit.STATE_SUBMITTED, worklog_submit.STATE_SKIPPED)
    pending = [
        e for e in targets if not e["zplanner_worklog_id"] and e["state"] not in done
    ]

    skipped_duplicates: List[Dict[str, Any]] = []
    orphans: List[Dict[str, Any]] = []
    not_attempted: List[Dict[str, Any]] = []

    if pending:
        client = _worklog_submit_client()

        # AC-10：整批的碼表從這裡開始跑，**含下面那支 export**。它一樣是網路
        # 往返、一樣吃 `(5, 20)` 的 read timeout、一樣可能被慢速回應拖住，
        # 把它排除在預算外等於預算漏了一個口。
        deadline = time.monotonic() + WORKLOG_SUBMIT_BUDGET_SECONDS

        # 去重的底稿：這一天在 ZPlanner 那側已經有哪些工時。
        #
        # 🔴 **查不到就整批中止，不准「查不到就當沒重複照送」。** 這支查詢
        # 失敗時我們對「那邊已經有什麼」一無所知，照送的期望值是造出一批
        # 重複工時——而重複工時在月結後刪不掉。中止的代價只是使用者再按一次。
        #
        # 一天只查一次、不逐筆查：逐筆查會把 N 次網路往返疊在送出流程上，
        # 而它們拿到的是同一份答案。
        existing = client.export_worklogs(date, date)
        duplicates = {
            match.entry_id: match
            for match in worklog_submit.find_duplicates(
                draft["entries"], existing, entry_ids=entry_ids
            )
        }

        for index, entry in enumerate(pending):
            # AC-10 的檢查點。
            #
            # 🔴 **判斷在每一筆開始之前，不是中斷進行中的 HTTP。** 砍掉一個已經
            # 發出去的寫入只會得到 `unknown`——「不知道寫進去沒有、不准自動重試、
            # 要人工去 ZPlanner 對帳」，那比乾脆沒送更糟。停在這裡的筆維持
            # `pending`，重打一次就好。
            #
            # ⚠️ 停送的筆與「從未送過」在 DB 裡分不出來（都是 `pending`），
            # 所以 `not_attempted_count` 是使用者唯一看得出來的訊號——它一定要
            # 出現在 summary 裡，而不是只寫進 log。
            if time.monotonic() >= deadline:
                not_attempted = list(pending[index:])
                log.warning(
                    # %g 不是 %.0f：測試會把預算調成 0.4 之類的小數，%.0f 會把它
                    # 印成「0 秒」，查 log 的人會以為預算被設成 0 了。
                    "送出工時超出整批時間預算 %g 秒，停送剩下的 %d 筆"
                    "（draft=%s date=%s）；那幾筆維持 pending，可以重打。",
                    WORKLOG_SUBMIT_BUDGET_SECONDS, len(not_attempted), draft["id"], date,
                )
                break

            match = duplicates.get(entry["id"])
            if match is not None:
                # D-9(b)：標 skipped，但**不**把比對到的遠端工時認領到
                # `zplanner_worklog_id` 上。那筆不是我們建的，認領它會讓下面
                # 講的誤殺變成永久而且再也看不見；改由這份清單交代。
                repo.update_worklog_entry_state(
                    viewer_id, draft["id"], entry["id"],
                    state=worklog_submit.STATE_SKIPPED,
                    error_code=None,
                    error_message=None,
                )
                skipped_duplicates.append(
                    {
                        **match.to_dict(),
                        "reason": (
                            "ZPlanner 這一天同一個 issue 已經有同樣時數的工時了，"
                            "所以這筆沒有送出。比對只看 issue 與時數、不看說明文字"
                            "（說明會被改過，比對它會漏判而造出重複工時）——如果這"
                            "真的是另一件事，請直接到 ZPlanner 補。"
                        ),
                    }
                )
                continue

            orphan = _submit_one_worklog_entry(
                client, entry, viewer_id=viewer_id, draft_id=draft["id"], date=date
            )
            if orphan is not None:
                orphans.append(orphan)

    # 重讀。逐筆一交易＝整批沒有一致快照，回應裡的 entries 只能以 DB 現況為準，
    # 不能拿迴圈裡累積的東西湊——湊出來的那份會漏掉「送出期間被別條路徑改掉」的筆。
    fresh = repo.get_worklog_draft(viewer_id, date)
    if fresh is None:
        # 整份草稿在送出期間被刪掉了。已經送進 ZPlanner 的工時仍然存在，
        # 但本地連草稿都沒了——這件事必須講出來，不能回一個空殼 200。
        raise WorklogDraftNotFound(
            f"{date} 的草稿在送出過程中被刪掉了。已經送進 ZPlanner 的工時仍然存在，"
            "請直接到 ZPlanner 確認那一天的內容。"
        )

    entries = fresh["entries"]
    # D-9(c)：每一筆都 submitted 或 skipped 才算這一天送完了。少了這一步，
    # 週檢視的 `totals.days_submitted` 會永遠是 0。
    status = worklog_submit.derive_draft_status(entries)
    if status != fresh["status"]:
        fresh = repo.upsert_worklog_draft(
            viewer_id, date, tz_offset=fresh["tz_offset"], status=status
        )
        entries = fresh["entries"]

    return {
        "work_date": date,
        "status": status,
        "entries": entries,
        "summary": {
            # 四個計數都是**整份草稿**的現況，與上面的 entries 是同一份帳。
            # 鍵名沿用週檢視（`list_worklog_drafts_in_range`），兩個畫面才
            # 講同一套話。
            "submitted_count": _count_state(entries, worklog_submit.STATE_SUBMITTED),
            "skipped_count": _count_state(entries, worklog_submit.STATE_SKIPPED),
            "failed_count": _count_state(entries, worklog_submit.STATE_FAILED),
            "unknown_count": _count_state(entries, worklog_submit.STATE_UNKNOWN),
            # 以下三個是**這一次**才有的資訊，DB 裡沒有對應欄位可以重算。
            "skipped_duplicates": skipped_duplicates,
            "orphans": orphans,
            # AC-10：超出時間預算而沒有嘗試的筆數。⚠️ **一律都在，即使是 0**
            # ——形狀穩定比少幾個位元組重要（同 `_refuse_incomplete_worklog_entries`
            # 的 detail）。這些筆維持 `pending`，與「從未送過」在 DB 裡長得一樣，
            # 這個數字是使用者唯一看得出「這批沒跑完」的地方。
            "not_attempted_count": len(not_attempted),
        },
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
