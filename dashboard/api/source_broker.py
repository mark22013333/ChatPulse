"""單題來源能力；模型只能選已登錄來源與固定讀取操作。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import threading
import time

from core import code_search
from core.errors import ChatPulseError
from core.reply_evidence import (BlockReason, Evidence, EvidenceError, Query, SourceRead,
                                 fingerprint, parse_queries, text_hash)

_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="reply-evidence")
_SLOTS = threading.BoundedSemaphore(4)


class RunStopped(Exception):
    def __init__(self, reason: BlockReason):
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class Limits:
    seconds: float = 90
    reads: int = 12
    followups: int = 3
    models: int = 10
    result_bytes: int = 100_000
    call_seconds: float = 30

    def __post_init__(self):
        for value, low, high in ((self.seconds, 0.01, 90), (self.reads, 1, 12),
                                 (self.followups, 0, 3), (self.models, 1, 10),
                                 (self.result_bytes, 100, 100_000), (self.call_seconds, 0.01, 30)):
            if isinstance(value, bool) or not low <= value <= high:
                raise ValueError("查證執行預算超出範圍")
        if any(type(value) is not int for value in (self.reads, self.followups, self.models, self.result_bytes)):
            raise ValueError("查證次數與位元組限制必須是整數")


class RunControl:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.deadline = time.monotonic() + self.limits.seconds
        self.cancelled = threading.Event()
        self.counts = {"reads": 0, "followups": 0, "model_calls": 0}

    def stop(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise RunStopped(BlockReason.CANCELLED)
        if time.monotonic() >= self.deadline:
            raise RunStopped(BlockReason.DEADLINE)

    def consume(self, kind):
        self.check()
        maximum, reason = {"reads": (self.limits.reads, BlockReason.READ_BUDGET),
                           "model_calls": (self.limits.models, BlockReason.MODEL_BUDGET)}[kind]
        if self.counts[kind] >= maximum:
            raise RunStopped(reason)
        self.counts[kind] += 1

    async def call(self, function):
        self.check()
        if not _SLOTS.acquire(blocking=False):
            raise RunStopped(BlockReason.CAPACITY)
        def work():
            try:
                self.check()
                return function()
            finally:
                _SLOTS.release()
        try:
            future = asyncio.wrap_future(_WORKERS.submit(work))
        except BaseException:
            _SLOTS.release()
            raise
        until = min(self.deadline, time.monotonic() + self.limits.call_seconds)
        try:
            while not future.done():
                self.check()
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise RunStopped(BlockReason.DEADLINE)
                await asyncio.wait({future}, timeout=min(remaining, 0.05))
            self.check()
            return future.result()
        finally:
            # generate 無取消 API。撤權只停止等待與採用，實際 worker 仍占用容量直到返回。
            if not future.done():
                future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)


class SourceBroker:
    def __init__(self, scope, mention, client, projects, control: RunControl, check_access):
        self.scope, self.mention, self.client = scope, mention, client
        self.projects, self.control = projects, control
        self.check_access = check_access
        self.sources = {s["id"]: s for s in scope["sources"]}
        self.sources["$anchor"] = {"id": "$anchor", "kind": "chat", "use": "reply_context",
                                    "space_id": mention["space_id"]}
        self.sources["$target"] = {"id": "$target", "kind": "chat", "use": "reply_context",
                                    "space_id": mention["space_id"]}

    def initial_queries(self):
        yield Query("$anchor", "anchor")
        yield Query("$target", "chat_recent")
        for source in self.scope["sources"]:
            if source["use"] != "metadata_only":
                yield Query(source["id"], "chat_recent" if source["kind"] == "chat" else "git_search",
                            tuple(self.scope["code_terms"]) if source["kind"] == "git" else (),
                            tuple(source.get("paths", ())))

    def parse_queries(self, raw):
        return parse_queries(raw, self.sources)

    async def read(self, query: Query):
        self.check_access()
        self.control.consume("reads")
        source = self.sources.get(query.source_id)
        if not source or source["use"] == "metadata_only":
            raise EvidenceError("此來源未獲本題允許")
        if query.operation != "anchor":
            self.parse_queries({"queries": [query.json()]})
        elif query.source_id != "$anchor":
            raise EvidenceError("錨點來源無效")
        try:
            result = await self.control.call(lambda: self._read(query, source))
            self.check_access()
            return result
        except RunStopped:
            raise
        except Exception as exc:
            if isinstance(exc, (ValueError, TypeError, KeyError)):
                code = "invalid_response"
            elif isinstance(exc, ChatPulseError):
                code = {401: "auth_required", 403: "forbidden", 404: "not_found"}.get(exc.http_status, "unavailable")
            else:
                code = "unavailable"
            return SourceRead(query, "failed", error=code)

    def _read(self, query, source):
        if source["kind"] == "chat":
            if query.operation == "anchor":
                messages = [self.client.read_reply_message(self.mention["message_name"])]
            else:
                if query.source_id == "$target" and self.mention.get("thread_name"):
                    messages = self.client.read_reply_recent(
                        source["space_id"], self.scope["limit"], thread_name=self.mention["thread_name"])
                else:
                    messages = self.client.read_reply_recent(source["space_id"], self.scope["limit"])
            if not isinstance(messages, list):
                raise EvidenceError("Chat 訊息清單格式不符")
            evidence, seen, images = [], set(), False
            for msg in messages:
                if not isinstance(msg, dict):
                    raise EvidenceError("Chat 訊息格式不符")
                name, text = msg.get("name"), msg.get("text", "")
                if (not isinstance(name, str) or not name.startswith(source["space_id"] + "/messages/")
                    or name in seen or not isinstance(text, str)):
                    raise EvidenceError("Chat 訊息位置或內容不符")
                if query.operation == "anchor" and name != self.mention["message_name"]:
                    raise EvidenceError("Chat 回傳不同錨點")
                if (query.source_id in ("$anchor", "$target") and self.mention.get("thread_name")
                    and (not isinstance(msg.get("thread"), dict)
                         or msg["thread"].get("name") != self.mention["thread_name"])):
                    raise EvidenceError("Chat 訊息不屬於本題討論串")
                seen.add(name)
                images = images or bool(msg.get("attachment") or msg.get("cardsV2") or msg.get("cards"))
                revision = fingerprint({"text": text, "update": msg.get("lastUpdateTime"),
                                        "created": msg.get("createTime"), "deleted": msg.get("deletionMetadata"),
                                        "attachments": msg.get("attachment")})
                evidence.append(Evidence("e" + fingerprint([query.source_id, name, revision])[:24],
                                         query.source_id, source["use"], name, revision, text,
                                         None, msg.get("createTime"), "text_report"))
            status = "complete"
        else:
            project = self.projects[source["code_project_id"]]
            context, skipped = code_search.collect(
                repo_path=project["repo_path"], project_name=project["name"],
                environment=source["environment"], branch=source["resolved_branch"],
                terms=query.terms, explicit_paths=query.paths or source.get("paths", []),
                include_globs=project.get("include_globs", []), exclude_globs=project.get("exclude_globs", []))
            if len(context.commit_sha) not in (40, 64):
                raise EvidenceError("Git 必須提供完整 SHA")
            evidence = [Evidence("e" + fingerprint([query.source_id, context.commit_sha, h.path, h.start_line, h.end_line])[:24],
                                 query.source_id, source["use"], f"{h.path}:{h.start_line}-{h.end_line}",
                                 context.commit_sha, h.text, source["environment"], context.commit_date,
                                 "code_snapshot") for h in context.hits]
            status, images = ("partial" if context.truncated or skipped else "complete"), False
        total = sum(len(json.dumps(e.prompt(), ensure_ascii=False).encode("utf-8")) for e in evidence)
        if total > self.control.limits.result_bytes:
            return SourceRead(query, "partial", images_omitted=images)
        return SourceRead(query, status, tuple(evidence), images_omitted=images)

    async def replay(self, ledger):
        results = []
        if not isinstance(ledger, list) or not ledger:
            raise EvidenceError("草稿缺少重查紀錄")
        for item in ledger:
            raw = item["query"]
            if raw["source_id"] == "$anchor" and raw["operation"] == "anchor":
                if raw != Query("$anchor", "anchor").json():
                    raise EvidenceError("錨點重查紀錄無效")
                query = Query("$anchor", "anchor")
            else:
                query = self.parse_queries({"queries": [raw]})[0]
            results.append(await self.read(query))
        return results
