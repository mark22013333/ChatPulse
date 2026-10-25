"""Azure DevOps 工單的唯讀邊界；只有已確認的 HTTPS collection 可送出 PAT。"""

from __future__ import annotations

import base64
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import requests

from .errors import ChatPulseError


class AdoReason(StrEnum):
    AUTH_REQUIRED = "auth_required"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"


_MESSAGES = {
    AdoReason.AUTH_REQUIRED: "ADO 認證失敗，請確認 PAT 與 Server 認證設定",
    AdoReason.FORBIDDEN: "ADO 拒絕讀取這筆資料",
    AdoReason.NOT_FOUND: "ADO 找不到資料，或目前身分沒有檢視權限",
    AdoReason.TIMEOUT: "ADO 讀取逾時",
    AdoReason.INVALID_RESPONSE: "ADO 回應格式不符契約",
    AdoReason.UNAVAILABLE: "ADO 目前無法讀取",
    AdoReason.PARTIAL: "ADO 只回傳部分資料，無法當作完整證據",
}


class AdoReadError(ChatPulseError):
    """以可採取下一步行動的原因分類，且永不攜帶原始請求或回應。"""

    http_status = 502

    def __init__(self, reason: AdoReason, *, endpoint: str, status: int | None = None):
        self.reason = reason
        self.endpoint = endpoint
        self.status = status
        self.code = f"ADO_{reason.value.upper()}"
        self.http_status = 504 if reason is AdoReason.TIMEOUT else 502
        if reason is AdoReason.AUTH_REQUIRED:
            self.http_status = 401
        elif reason is AdoReason.FORBIDDEN:
            self.http_status = 403
        elif reason is AdoReason.NOT_FOUND:
            self.http_status = 404
        super().__init__(_MESSAGES[reason], detail=f"ado:{endpoint}:{status or 0}")


def _invalid_setting() -> ValueError:
    return ValueError("ADO 連線設定格式無效")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ADO JSON 欄位重複")
        result[key] = value
    return result


def _json_constant(value):
    raise ValueError("ADO JSON 含非標準數字")


@dataclass(frozen=True, repr=False)
class AdoConnection:
    collection_url: str
    project: str
    expected_port: int = 443

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.collection_url)
            segments = parsed.path.strip("/").split("/")
            host = parsed.hostname or ""
            try:
                ipaddress.ip_address(host)
                valid_host = True
            except ValueError:
                valid_host = all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split("."))
            if (
                parsed.scheme != "https"
                or not valid_host
                or any(ord(char) < 33 or ord(char) == 127 for char in self.collection_url)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.port != self.expected_port and not (parsed.port is None and self.expected_port == 443)
                or not 1 <= self.expected_port <= 65535
                or not parsed.path.startswith("/")
                or parsed.path.endswith("/")
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..") for part in segments)
                or not re.fullmatch(r"[A-Za-z0-9_. -]+", self.project)
                or self.project in (".", "..")
                or self.project.startswith(" ")
                or self.project.endswith(" ")
            ):
                raise _invalid_setting()
        except (ValueError, TypeError) as exc:
            raise _invalid_setting() from None


@dataclass(frozen=True)
class AdoApiProfile:
    work_items: str
    revisions: str
    comments: str

    def __post_init__(self) -> None:
        for version in (self.work_items, self.revisions, self.comments):
            if not isinstance(version, str) or not re.fullmatch(r"[1-9]\d*\.\d+(?:-preview\.\d+)?", version):
                raise _invalid_setting()


@dataclass(frozen=True)
class AdoLimits:
    page_size: int = 100
    max_pages: int = 20
    max_items: int = 2000
    max_bytes: int = 4_000_000
    connect_timeout: float = 3.0
    read_timeout: float = 8.0
    deadline_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int for value in (self.page_size, self.max_pages, self.max_items, self.max_bytes))
            or not 1 <= self.page_size <= 1000
            or self.max_pages < 1
            or self.max_items < 1
            or self.max_bytes < 1
            or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in (self.connect_timeout, self.read_timeout, self.deadline_seconds))
        ):
            raise _invalid_setting()


@dataclass(frozen=True, repr=False)
class AdoRelation:
    rel: str
    url: str = field(repr=False)
    attributes: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, repr=False)
class AdoWorkItem:
    id: int
    rev: int
    fields: Mapping[str, Any] = field(repr=False)
    # None 表示端點沒有提供 relations，絕不能當作「沒有關聯」。
    relations: tuple[AdoRelation, ...] | None = field(repr=False)


@dataclass(frozen=True, repr=False)
class AdoRevision:
    id: int
    rev: int
    fields: Mapping[str, Any] = field(repr=False)
    relations: tuple[AdoRelation, ...] | None = field(repr=False)


@dataclass(frozen=True, repr=False)
class AdoComment:
    id: int
    work_item_id: int
    version: int
    text: str = field(repr=False)
    created_date: str | None = None
    modified_date: str | None = None
    is_deleted: bool | None = None


@dataclass(frozen=True, repr=False)
class AdoItemBundle:
    item: AdoWorkItem
    revisions: tuple[AdoRevision, ...]
    comments: tuple[AdoComment, ...]


@dataclass
class _Budget:
    end: float
    max_bytes: int
    used_bytes: int = 0

    def remaining(self, endpoint: str) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise AdoReadError(AdoReason.TIMEOUT, endpoint=endpoint)
        return left


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _relations(payload: Mapping[str, Any], endpoint: str) -> tuple[AdoRelation, ...] | None:
    if "relations" not in payload:
        return None
    raw = payload["relations"]
    if not isinstance(raw, list):
        raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
    output = []
    for relation in raw:
        if (
            not isinstance(relation, dict)
            or not isinstance(relation.get("rel"), str)
            or not isinstance(relation.get("url"), str)
            or not isinstance(relation.get("attributes", {}), dict)
        ):
            raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
        output.append(AdoRelation(relation["rel"], relation["url"], relation.get("attributes", {})))
    return tuple(output)


def _work_item(payload: Any, item_id: int, endpoint: str) -> AdoWorkItem:
    if (
        not isinstance(payload, dict)
        or not _positive_int(payload.get("id"))
        or payload["id"] != item_id
        or not _positive_int(payload.get("rev"))
        or not isinstance(payload.get("fields"), dict)
    ):
        raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
    return AdoWorkItem(item_id, payload["rev"], payload["fields"], _relations(payload, endpoint))


def _revision(payload: Any, item_id: int) -> AdoRevision:
    endpoint = "revisions"
    item = _work_item(payload, item_id, endpoint)
    return AdoRevision(item.id, item.rev, item.fields, item.relations)


def _comment(payload: Any, item_id: int) -> AdoComment:
    endpoint = "comments"
    if not isinstance(payload, dict):
        raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
    # 官方範例為 commentId、定義為 id；兩者同時出現時必須一致。
    comment_id = payload.get("id", payload.get("commentId"))
    if "id" in payload and "commentId" in payload and payload["id"] != payload["commentId"]:
        raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
    if (
        not _positive_int(comment_id)
        or payload.get("workItemId") != item_id
        or not _positive_int(payload.get("version"))
        or not isinstance(payload.get("text"), str)
        or any(key in payload and not isinstance(payload[key], str) for key in ("createdDate", "modifiedDate"))
        or "isDeleted" in payload and type(payload["isDeleted"]) is not bool
    ):
        raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
    return AdoComment(comment_id, item_id, payload["version"], payload["text"], payload.get("createdDate"), payload.get("modifiedDate"), payload.get("isDeleted"))


class AdoClient:
    """只由可信任 collection、project 與 item ID 組成固定唯讀請求。"""

    def __init__(
        self,
        connection: AdoConnection,
        profile: AdoApiProfile,
        pat: str,
        *,
        verify: bool | str = True,
        limits: AdoLimits | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(pat, str) or not pat or any(ord(char) < 33 or ord(char) == 127 for char in pat):
            raise _invalid_setting()
        if verify is not True and not (isinstance(verify, str) and Path(verify).is_absolute() and Path(verify).is_file()):
            raise _invalid_setting()
        self._connection = connection
        self._profile = profile
        self._limits = limits or AdoLimits()
        self._verify = verify
        self._session = session if session is not None else requests.Session()
        self._session.trust_env = False
        self._auth = "Basic " + base64.b64encode((":" + pat).encode("utf-8")).decode("ascii")

    def _budget(self) -> _Budget:
        return _Budget(time.monotonic() + self._limits.deadline_seconds, self._limits.max_bytes)

    @staticmethod
    def _check_id(item_id: int) -> None:
        if not _positive_int(item_id):
            raise ValueError("ADO 工單 ID 必須是正整數")

    def _get(self, item_id: int, endpoint: str, suffix: str, params: dict[str, Any], budget: _Budget) -> dict[str, Any]:
        left = budget.remaining(endpoint)
        url = f"{self._connection.collection_url}/{quote(self._connection.project, safe='')}/_apis/wit/workItems/{item_id}{suffix}"
        timeout = (min(self._limits.connect_timeout, left), min(self._limits.read_timeout, left))
        response = None
        try:
            response = self._session.get(
                url,
                params=params,
                headers={"Accept": "application/json", "Authorization": self._auth},
                timeout=timeout,
                verify=self._verify,
                allow_redirects=False,
                stream=True,
            )
            status = response.status_code
            reasons = {401: AdoReason.AUTH_REQUIRED, 403: AdoReason.FORBIDDEN, 404: AdoReason.NOT_FOUND}
            if status in reasons:
                raise AdoReadError(reasons[status], endpoint=endpoint, status=status)
            if status != 200:
                raise AdoReadError(AdoReason.UNAVAILABLE, endpoint=endpoint, status=status)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint, status=status)
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=16_384):
                budget.remaining(endpoint)
                size += len(chunk)
                if budget.used_bytes + size > budget.max_bytes:
                    raise AdoReadError(AdoReason.PARTIAL, endpoint=endpoint)
                chunks.append(chunk)
            budget.used_bytes += size
            budget.remaining(endpoint)
            try:
                payload = json.loads(b"".join(chunks), object_pairs_hook=_json_object, parse_constant=_json_constant)
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint) from None
            if not isinstance(payload, dict):
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint=endpoint)
            return payload
        except requests.Timeout:
            raise AdoReadError(AdoReason.TIMEOUT, endpoint=endpoint) from None
        except requests.RequestException:
            raise AdoReadError(AdoReason.UNAVAILABLE, endpoint=endpoint) from None
        finally:
            if response is not None:
                response.close()

    def get_work_item(self, item_id: int) -> AdoWorkItem:
        self._check_id(item_id)
        return self._get_work_item(item_id, self._budget())

    def _get_work_item(self, item_id: int, budget: _Budget) -> AdoWorkItem:
        payload = self._get(item_id, "work_item", "", {"$expand": "relations", "api-version": self._profile.work_items}, budget)
        return _work_item(payload, item_id, "work_item")

    def list_revisions(self, item_id: int) -> tuple[AdoRevision, ...]:
        self._check_id(item_id)
        return self._list_revisions(item_id, self._budget())

    def _list_revisions(self, item_id: int, budget: _Budget) -> tuple[AdoRevision, ...]:
        output: list[AdoRevision] = []
        seen_revisions: set[int] = set()
        page_size = self._limits.page_size
        for page in range(self._limits.max_pages):
            payload = self._get(item_id, "revisions", "/revisions", {"$top": page_size, "$skip": page * page_size, "api-version": self._profile.revisions}, budget)
            value = payload.get("value")
            count = payload.get("count")
            if type(count) is not int or count < 0 or not isinstance(value, list) or count != len(value) or count > page_size:
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint="revisions")
            if len(output) + count > self._limits.max_items:
                raise AdoReadError(AdoReason.PARTIAL, endpoint="revisions")
            for row in value:
                revision = _revision(row, item_id)
                if revision.rev in seen_revisions:
                    raise AdoReadError(AdoReason.PARTIAL, endpoint="revisions")
                seen_revisions.add(revision.rev)
                output.append(revision)
            if count < page_size:
                return tuple(output)
        raise AdoReadError(AdoReason.PARTIAL, endpoint="revisions")

    def list_comments(self, item_id: int) -> tuple[AdoComment, ...]:
        self._check_id(item_id)
        return self._list_comments(item_id, self._budget())

    def _list_comments(self, item_id: int, budget: _Budget) -> tuple[AdoComment, ...]:
        output: list[AdoComment] = []
        seen_comments: set[int] = set()
        seen_tokens: set[str] = set()
        token: str | None = None
        for _ in range(self._limits.max_pages):
            params: dict[str, Any] = {"$top": self._limits.page_size, "api-version": self._profile.comments}
            if token is not None:
                params["continuationToken"] = token
            payload = self._get(item_id, "comments", "/comments", params, budget)
            comments = payload.get("comments")
            count = payload.get("count")
            if type(count) is not int or count < 0 or not isinstance(comments, list) or count != len(comments) or count > self._limits.page_size:
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint="comments")
            if "continuationToken" not in payload:
                raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")
            next_token = payload["continuationToken"]
            if next_token is not None and (not isinstance(next_token, str) or not next_token or next_token in seen_tokens or not comments):
                raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")
            if len(output) + count > self._limits.max_items:
                raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")
            for row in comments:
                comment = _comment(row, item_id)
                if comment.id in seen_comments:
                    raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")
                seen_comments.add(comment.id)
                output.append(comment)
            total = payload.get("totalCount")
            if total is not None and (type(total) is not int or total < len(output)):
                raise AdoReadError(AdoReason.INVALID_RESPONSE, endpoint="comments")
            if next_token is None:
                if total is not None and total != len(output):
                    raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")
                return tuple(output)
            seen_tokens.add(next_token)
            token = next_token
        raise AdoReadError(AdoReason.PARTIAL, endpoint="comments")

    def read_item_bundle(self, item_id: int) -> AdoItemBundle:
        self._check_id(item_id)
        budget = self._budget()
        return AdoItemBundle(self._get_work_item(item_id, budget), self._list_revisions(item_id, budget), self._list_comments(item_id, budget))
