"""Mention 採集器。

規格對應：SPECIFICATION.md 六節（判定條件、採集策略、輪詢頻率、狀態模型）。

採集器定義為介面，兩種實作可互換：
  - `SearchCollector`（實作 A，跨群搜尋）——**實測不可用**，見 docs/R1-findings.md
  - `PollingCollector`（實作 B，逐群輪詢）——**預設實作**

實作 B 的成本遠低於規格書 6.2 的估算（原估一輪 2 分鐘、吃 48% 配額），因為實測
發現兩個槓桿：`spaces.list` 回傳 `lastActiveTime`（436/436 皆有，近 24 小時只有 13 個
Space 有活動），且 `messages.list` 接受 `filter=createTime > "..."`。所以每輪是
「1 次 spaces.list + 每個活躍 Space 各 1 次 messages.list」，典型 1~3 次呼叫。
"""

import concurrent.futures as futures
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import directory
from . import repository as repo
from .chat_client import GoogleChatClient, parse_rfc3339, rfc3339

log = logging.getLogger("chatpulse.mentions")

# lastActiveTime 的比對留一點餘裕，避免時鐘或索引延遲造成漏抓
LAST_ACTIVE_MARGIN = timedelta(minutes=5)
# 每 N 輪做一次較寬的掃描，作為 lastActiveTime 若更新不及時的保險
FULL_SWEEP_EVERY = 20


# --------------------------------------------------------------------------
# 判定條件（6.1）
# --------------------------------------------------------------------------


def is_mention_of(message: Dict[str, Any], google_user_id: str) -> bool:
    """判斷一則訊息是否構成對指定 Viewer 的 Mention。

    三個條件必須**同時**成立：
        annotations[].type                   == "USER_MENTION"
        annotations[].userMention.type       == "MENTION"
        annotations[].userMention.user.name  == "users/{Viewer 自己的 id}"

    只判斷 USER_MENTION 是錯的：`userMention.type` 另有 `ADD` 值，代表
    「某人被加進 Space」的系統訊息。漏掉第二個條件，每次有人被拉進群都會
    被算成一則待回覆。
    """
    for ann in message.get("annotations") or []:
        if ann.get("type") != "USER_MENTION":
            continue
        um = ann.get("userMention") or {}
        if um.get("type") != "MENTION":
            continue
        if (um.get("user") or {}).get("name") == google_user_id:
            return True
    return False


def to_mention_row(
    message: Dict[str, Any], space_display_name: Optional[str]
) -> Dict[str, Any]:
    """把 Chat 訊息轉成 mentions 表的資料列。**只取識別資訊，不存訊息內容**（九節）。"""
    name = message.get("name") or ""
    # message name 形如 spaces/XXX/messages/YYY，space_id 取前兩段
    parts = name.split("/")
    space_id = "/".join(parts[:2]) if len(parts) >= 2 else ""
    sender = message.get("sender") or {}
    return {
        "space_id": message.get("space", {}).get("name") or space_id,
        "space_name": space_display_name,
        "message_name": name,
        "thread_name": (message.get("thread") or {}).get("name"),
        "sender_name": sender.get("name"),
        "sender_display": sender.get("displayName"),
        "create_time": message.get("createTime") or "",
    }


# --------------------------------------------------------------------------
# 結果型別
# --------------------------------------------------------------------------


@dataclass
class CollectResult:
    new_mentions: int = 0
    total_matched: int = 0
    spaces_polled: int = 0
    spaces_total: int = 0
    api_calls: int = 0
    elapsed_seconds: float = 0.0
    full_sweep: bool = False
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "new_mentions": self.new_mentions,
            "total_matched": self.total_matched,
            "spaces_polled": self.spaces_polled,
            "spaces_total": self.spaces_total,
            "api_calls": self.api_calls,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "full_sweep": self.full_sweep,
            "errors": self.errors[:5],
        }


# --------------------------------------------------------------------------
# 介面
# --------------------------------------------------------------------------


class MentionCollector(ABC):
    """採集器介面。實作可互換，避免在帳號等級未確認前把架構賭進去（6.2）。"""

    name = "base"

    @abstractmethod
    def collect(
        self,
        client: GoogleChatClient,
        viewer_id: int,
        google_user_id: str,
        *,
        cycle: int = 0,
    ) -> CollectResult:
        raise NotImplementedError


class SearchCollector(MentionCollector):
    """實作 A：一次呼叫取得所有 Space 的 Mention。

    **2026-09-05 實測不可用**：端點回 HTTP 200 但恆 0 筆，正對照（先發一則真的
    @ 自己的訊息再搜）也搜不到。保留此實作是為了帳號條件改變後能一行切回去
    （CHATPULSE_COLLECTOR=search），不是因為它現在能用。
    """

    name = "search"

    def collect(
        self,
        client: GoogleChatClient,
        viewer_id: int,
        google_user_id: str,
        *,
        cycle: int = 0,
    ) -> CollectResult:
        started = time.monotonic()
        result = CollectResult()
        state = repo.get_collector_state(viewer_id)
        since = parse_rfc3339(state.get("last_polled_at") or "") or (
            datetime.now(timezone.utc)
            - timedelta(hours=cfg.MENTION_INITIAL_LOOKBACK_HOURS)
        )
        run_started_at = datetime.now(timezone.utc)

        messages = client.search_mentions(google_user_id)
        result.api_calls += 1

        space_names = {}
        for msg in messages:
            created = parse_rfc3339(msg.get("createTime") or "")
            # search 的 filter 不支援 createTime（實測 400），時間範圍只能在本地過濾
            if created and created <= since:
                continue
            if not is_mention_of(msg, google_user_id):
                continue
            result.total_matched += 1
            row = to_mention_row(msg, space_names.get(msg.get("space", {}).get("name")))
            if repo.upsert_mention(viewer_id, row):
                result.new_mentions += 1

        repo.set_collector_state(
            viewer_id,
            last_polled_at=run_started_at.isoformat(timespec="seconds"),
            last_error=None,
            last_run_stats=result.as_dict(),
        )
        result.elapsed_seconds = time.monotonic() - started
        return result


class PollingCollector(MentionCollector):
    """實作 B：逐群輪詢，但只輪詢近期有活動的 Space 子集。

    `spaces.messages.list` 的 filter 只支援 createTime 與 thread.name，
    **沒有任何 mention 相關條件**，故 6.1 的判定必須在本地做。
    """

    name = "polling"

    def collect(
        self,
        client: GoogleChatClient,
        viewer_id: int,
        google_user_id: str,
        *,
        cycle: int = 0,
    ) -> CollectResult:
        started = time.monotonic()
        result = CollectResult()

        state = repo.get_collector_state(viewer_id)
        last_polled = parse_rfc3339(state.get("last_polled_at") or "")
        initial = last_polled is None
        since = last_polled or (
            datetime.now(timezone.utc)
            - timedelta(hours=cfg.MENTION_INITIAL_LOOKBACK_HOURS)
        )
        run_started_at = datetime.now(timezone.utc)

        # 首輪與每 FULL_SWEEP_EVERY 輪做一次較寬的掃描，作為 lastActiveTime
        # 若更新不及時的保險
        full_sweep = initial or (cycle > 0 and cycle % FULL_SWEEP_EVERY == 0)
        result.full_sweep = full_sweep
        sweep_since = (
            datetime.now(timezone.utc)
            - timedelta(hours=cfg.MENTION_INITIAL_LOOKBACK_HOURS)
            if full_sweep
            else since - LAST_ACTIVE_MARGIN
        )

        spaces = client.list_spaces()
        result.api_calls += 1
        result.spaces_total = len(spaces)

        by_id = {s.get("name"): s for s in spaces}
        candidates = []
        for s in spaces:
            last_active = parse_rfc3339(s.get("lastActiveTime") or "")
            if last_active is None or last_active > sweep_since:
                candidates.append(s.get("name"))
        result.spaces_polled = len(candidates)

        # 訊息查詢的時間下界：全量掃描時也不必回頭超過 lookback
        message_since = sweep_since if full_sweep else since

        def poll_one(space_id: str):
            try:
                msgs = client.list_messages_since(space_id, message_since)
                return space_id, msgs, None
            except Exception as exc:  # 單一 Space 失敗不該讓整輪掛掉
                return space_id, [], f"{space_id}: {exc}"

        matched_rows: List[Dict[str, Any]] = []
        if candidates:
            workers = min(cfg.MENTION_POLL_WORKERS, max(1, len(candidates)))
            # 名錄在迴圈外載入一次。迴圈裡每個 space 都重載會變成上百次 DB 查詢，
            # 而本輪新學到的名字下一輪才用得到也無妨。
            resolve_peer = directory.make_resolver()
            with futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for space_id, msgs, err in pool.map(poll_one, candidates):
                    result.api_calls += 1
                    if err:
                        result.errors.append(err)
                        continue
                    space_obj = by_id.get(space_id) or {}
                    display = space_obj.get("displayName")
                    # 私訊沒有 displayName，順手從訊息認出對方是誰。
                    # 這一輪本來就讀了這些訊息，所以是零額外 API 成本；
                    # 認出來之後 Space 清單就不會再顯示「（私訊）」。
                    if not display and space_obj.get("spaceType") == "DIRECT_MESSAGE" and msgs:
                        try:
                            peer_id = directory.peer_id_from_messages(msgs, google_user_id)
                            if peer_id:
                                directory.link_dm_peer(space_id, peer_id)
                                name = resolve_peer(peer_id)
                                if name and not name.startswith("成員…"):
                                    display = name
                        except Exception:
                            log.exception("私訊對象辨識失敗（不影響採集）")
                    hits = [m for m in msgs if is_mention_of(m, google_user_id)]
                    if hits:
                        # 被 @ 的訊息必然帶 USER_MENTION annotation，是名錄最可靠的來源
                        try:
                            directory.learn_from_messages(hits)
                        except Exception:
                            log.exception("名錄學習失敗（不影響採集）")
                    for msg in hits:
                        matched_rows.append(to_mention_row(msg, display))

        # DB 寫入集中在呼叫端執行緒做——sqlite 連線是 thread-local，
        # 在 worker 執行緒寫會各自開連線、增加鎖競爭
        resolve = directory.make_resolver()
        for row in matched_rows:
            if not row.get("sender_display"):
                row["sender_display"] = resolve(row.get("sender_name"))
            result.total_matched += 1
            if repo.upsert_mention(viewer_id, row):
                result.new_mentions += 1

        repo.set_collector_state(
            viewer_id,
            last_polled_at=run_started_at.isoformat(timespec="seconds"),
            last_error="; ".join(result.errors[:3]) if result.errors else None,
            last_run_stats=result.as_dict(),
        )
        result.elapsed_seconds = time.monotonic() - started
        return result


def build_collector(kind: Optional[str] = None) -> MentionCollector:
    kind = (kind or cfg.MENTION_COLLECTOR).lower()
    if kind == "search":
        return SearchCollector()
    return PollingCollector()


# --------------------------------------------------------------------------
# 常駐工作（6.3：30~60 秒一次）
# --------------------------------------------------------------------------


class CollectorRunner:
    """背景執行緒，週期性對每位有憑證的 Viewer 跑一次採集。

    不使用 APScheduler——規格 3.1 已把它移除，這裡只需要一個 sleep 迴圈。
    """

    def __init__(self, client_factory, interval: Optional[int] = None):
        """client_factory: (viewer_row) -> GoogleChatClient | None"""
        self._client_factory = client_factory
        self._interval = interval or cfg.MENTION_POLL_INTERVAL_SECONDS
        self._collector = build_collector()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cycle = 0
        self.last_results: Dict[int, Dict[str, Any]] = {}

    @property
    def collector_name(self) -> str:
        return self._collector.name

    @property
    def interval(self) -> int:
        return self._interval

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="chatpulse-mention-collector", daemon=True
        )
        self._thread.start()
        log.info(
            "Mention 採集器已啟動（實作=%s，間隔=%ss）", self._collector.name, self._interval
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> Dict[int, Dict[str, Any]]:
        """同步跑一輪，供手動刷新與測試使用。"""
        self._cycle += 1
        out: Dict[int, Dict[str, Any]] = {}
        for viewer in repo.list_viewers():
            try:
                client = self._client_factory(viewer)
            except Exception as exc:
                log.warning("Viewer %s 無法建立 Chat client：%s", viewer["id"], exc)
                repo.set_collector_state(viewer["id"], last_error=str(exc))
                continue
            if client is None:
                continue
            try:
                res = self._collector.collect(
                    client, viewer["id"], viewer["google_user_id"], cycle=self._cycle
                )
                out[viewer["id"]] = res.as_dict()
            except Exception as exc:
                log.exception("Viewer %s 採集失敗", viewer["id"])
                repo.set_collector_state(viewer["id"], last_error=str(exc))
                out[viewer["id"]] = {"error": str(exc)}
        self.last_results = out
        return out

    def _loop(self) -> None:
        # 啟動後先等一小段時間，讓 FastAPI 完成啟動
        if self._stop.wait(3):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("採集器迴圈發生未預期錯誤")
            finally:
                # sqlite 連線是 thread-local，長駐執行緒用完就留著重用即可
                pass
            if self._stop.wait(self._interval):
                break
