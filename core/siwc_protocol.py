"""SIWC 授權碼與 OIDC 邊界。回傳候選身分，不啟用 Viewer 綁定。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import jwt

from core.private_http import private_http_client

_ISSUER = "https://auth.openai.com"
_AUTHORIZE = f"{_ISSUER}/api/accounts/authorize"
_TOKEN = f"{_ISSUER}/api/accounts/oauth/token"
_DISCOVERY = f"{_ISSUER}/.well-known/openid-configuration"
_JWKS = f"{_ISSUER}/.well-known/jwks.json"
_REVOKE = f"{_ISSUER}/api/accounts/oauth/revoke"
_RESOURCE = "https://api.openai.com/v1"
_PLAN_SCOPE = "chatgpt.tokens.use.direct"
_SCOPES = f"openid profile email offline_access resource.invoke {_PLAN_SCOPE}"
_DYNAMIC_CLIENT = "dynamic_agent_client"
_RESPONSE_LIMIT = 131_072
_OPERATION_LIMIT = 262_144
_ATTEMPT_TTL = timedelta(minutes=5)


class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_CALLBACK = "invalid_callback"
    ATTEMPT_EXPIRED = "attempt_expired"
    ACCESS_DENIED = "access_denied"
    CLAIM_REJECTED = "claim_rejected"
    INVALID_GRANT = "invalid_grant"
    INVALID_TOKEN_RESPONSE = "invalid_token_response"
    INVALID_ID_TOKEN = "invalid_id_token"
    ACCOUNT_MISMATCH = "account_mismatch"
    INVALID_METADATA = "invalid_metadata"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_MESSAGES = {
    ErrorCode.INVALID_REQUEST: "登入準備資料格式錯誤。",
    ErrorCode.INVALID_CALLBACK: "登入回傳資料不符合本次授權要求。",
    ErrorCode.ATTEMPT_EXPIRED: "本次登入已到期，請重新登入。",
    ErrorCode.ACCESS_DENIED: "本次授權已取消。",
    ErrorCode.CLAIM_REJECTED: "本次登入無法取得處理權，請重新登入。",
    ErrorCode.INVALID_GRANT: "授權碼無法交換，請保留已核發的註冊並重新登入。",
    ErrorCode.INVALID_TOKEN_RESPONSE: "授權服務未回傳完整且有效的憑證資料。",
    ErrorCode.INVALID_ID_TOKEN: "無法驗證 ChatGPT 身分，請重新登入。",
    ErrorCode.ACCOUNT_MISMATCH: "回傳的 ChatGPT 身分與原註冊不符。",
    ErrorCode.INVALID_METADATA: "授權服務的公開設定不符合預期。",
    ErrorCode.PROVIDER_UNAVAILABLE: "授權服務目前無法完成驗證。",
    ErrorCode.DEADLINE_EXCEEDED: "本次身分驗證逾時，請重新登入。",
}


class SiwcProtocolError(Exception):
    """固定錯誤碼讓呼叫端決定下一步，不攜帶 OAuth 或傳輸的原始內容。"""

    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    client_id: str
    email_label: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Registration:
    client_id: str
    identity: VerifiedIdentity | None = None


@dataclass(frozen=True)
class AuthorizationStart:
    attempt_id: str
    authorization_url: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class PendingAuthorization:
    """只能留在伺服器；repr 隱藏秘密不代表可以直接序列化給瀏覽器。"""

    attempt_id: str
    state_digest: str = field(repr=False)
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    redirect_uri: str
    requested_client_id: str | None
    previous_identity: VerifiedIdentity | None
    expires_at: datetime


@dataclass(frozen=True)
class CallbackAccepted:
    attempt_id: str
    state_digest: str = field(repr=False)
    issued_client_id: str


@dataclass(frozen=True)
class CallbackDenied:
    attempt_id: str
    state_digest: str = field(repr=False)


@dataclass(frozen=True)
class AccessCredentials:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime
    earliest_refresh_at: str | int | float | None = field(default=None, repr=False)


@dataclass(frozen=True)
class VerifiedGrant:
    identity: VerifiedIdentity
    scopes: frozenset[str]
    received_at: datetime
    retained_id_token: str = field(repr=False)
    credentials: AccessCredentials | None = field(repr=False)

    @property
    def has_plan_grant(self) -> bool:
        """只表示 token response 的授權，不表示已驗證模型資格或推論可用。"""
        return _PLAN_SCOPE in self.scopes


ClaimCallback = Callable[[CallbackAccepted | CallbackDenied], Awaitable[None]]


def _valid_client(value: object) -> bool:
    return (isinstance(value, str) and value != _DYNAMIC_CLIENT
            and re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", value) is not None)


def _strict_json(raw: bytes, error: ErrorCode) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError()
        return parsed

    def integer(value):
        parsed = int(value)
        if not math.isfinite(parsed):
            raise ValueError()
        return parsed

    def constant(_value):
        raise ValueError()

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=number, parse_int=integer, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OverflowError, UnicodeError, RecursionError):
        raise SiwcProtocolError(error) from None


def _callback(pending: PendingAuthorization, raw: bytes) -> tuple[CallbackAccepted | CallbackDenied, str | None]:
    error = ErrorCode.INVALID_CALLBACK
    if len(raw) > 8192 or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
        raise SiwcProtocolError(error)
    try:
        pairs = parse_qsl(raw.decode("ascii"), keep_blank_values=True,
                          strict_parsing=True, encoding="utf-8", errors="strict", max_num_fields=20)
    except (ValueError, UnicodeError):
        raise SiwcProtocolError(error) from None
    values = dict(pairs)
    if len(values) != len(pairs) or not values.get("state"):
        raise SiwcProtocolError(error)
    digest = hashlib.sha256(values["state"].encode()).hexdigest()
    if not hmac.compare_digest(digest, pending.state_digest):
        raise SiwcProtocolError(error)
    if datetime.now(timezone.utc) >= pending.expires_at:
        raise SiwcProtocolError(ErrorCode.ATTEMPT_EXPIRED)
    if "error" in values:
        if values["error"] != "access_denied" or "code" in values or "client_id" in values:
            raise SiwcProtocolError(error)
        return CallbackDenied(pending.attempt_id, digest), None
    client_id = values.get("client_id", pending.requested_client_id)
    code = values.get("code")
    if (not _valid_client(client_id) or not code or any(ord(c) < 32 or ord(c) == 127 for c in code)
            or (pending.requested_client_id is not None and client_id != pending.requested_client_id)):
        raise SiwcProtocolError(error)
    return CallbackAccepted(pending.attempt_id, digest, client_id), code


class _Responses:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.remaining = _OPERATION_LIMIT

    async def json(self, url: str, *, form: dict[str, str] | None = None) -> dict:
        error = ErrorCode.INVALID_TOKEN_RESPONSE if url == _TOKEN else ErrorCode.INVALID_METADATA
        async with self.client.stream(
            "GET" if form is None else "POST", url, data=form,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise SiwcProtocolError(error)
            body = bytearray()
            async for chunk in response.aiter_raw():
                self.remaining -= len(chunk)
                if self.remaining < 0 or len(body) + len(chunk) > _RESPONSE_LIMIT:
                    raise SiwcProtocolError(error)
                body.extend(chunk)
            if response.status_code != 200:
                if url == _TOKEN and response.status_code == 400:
                    data = _strict_json(bytes(body), error)
                    if data.get("error") == "invalid_grant":
                        raise SiwcProtocolError(ErrorCode.INVALID_GRANT)
                raise SiwcProtocolError(ErrorCode.PROVIDER_UNAVAILABLE)
            return _strict_json(bytes(body), error)


def _token_fields(data: dict, received_at: datetime) -> tuple[frozenset[str], AccessCredentials | None, str]:
    error = ErrorCode.INVALID_TOKEN_RESPONSE
    scope = data.get("scope")
    token = data.get("id_token")
    if (not isinstance(scope, str) or re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e ]*", scope) is None
            or not isinstance(token, str) or not token or len(token) > 32768):
        raise SiwcProtocolError(error)
    scopes = frozenset(scope.split())
    if "openid" not in scopes:
        raise SiwcProtocolError(error)
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if access is None:
        if ("offline_access" in scopes or _PLAN_SCOPE in scopes
                or any(name in data for name in ("access_token", "refresh_token", "token_type", "expires_in", "earliest_refresh_at"))):
            raise SiwcProtocolError(error)
        return scopes, None, token
    seconds = data.get("expires_in")
    if (not isinstance(access, str) or len(access) > 32768
            or re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", access) is None
            or not isinstance(data.get("token_type"), str) or data["token_type"].lower() != "bearer"
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 31_536_000
            or ("refresh_token" in data and (not isinstance(refresh, str) or not refresh or len(refresh) > 32768))
            or ("offline_access" in scopes and not refresh)):
        raise SiwcProtocolError(error)
    earliest = data.get("earliest_refresh_at")
    if "earliest_refresh_at" in data and not (
        (type(earliest) in (int, float) and math.isfinite(earliest))
        or (isinstance(earliest, str) and 0 < len(earliest) <= 256)
    ):
        raise SiwcProtocolError(error)
    return scopes, AccessCredentials(access, refresh, received_at + timedelta(seconds=seconds), earliest), token


async def _identity(responses: _Responses, token: str, client_id: str, nonce: str) -> VerifiedIdentity:
    error = ErrorCode.INVALID_ID_TOKEN
    try:
        segments = token.split(".")
        if len(segments) != 3 or any(re.fullmatch(r"[A-Za-z0-9_-]+", part) is None for part in segments):
            raise ValueError()
        header = _strict_json(jwt.utils.base64url_decode(segments[0]), error)
        claims = _strict_json(jwt.utils.base64url_decode(segments[1]), error)
        if (header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]
                or len(header["kid"]) > 200 or "jku" in header or "x5u" in header
                or "crit" in header or "b64" in header):
            raise ValueError()
        for name in ("exp", "iat", "nbf"):
            if name in claims and (type(claims[name]) not in (int, float) or not math.isfinite(claims[name])):
                raise ValueError()
    except (ValueError, TypeError, jwt.PyJWTError):
        raise SiwcProtocolError(error) from None
    metadata = await responses.json(_DISCOVERY)
    expected = {"issuer": _ISSUER, "authorization_endpoint": _AUTHORIZE, "token_endpoint": _TOKEN,
                "jwks_uri": _JWKS, "revocation_endpoint": _REVOKE,
                "id_token_signing_alg_values_supported": ["RS256"]}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise SiwcProtocolError(ErrorCode.INVALID_METADATA)
    key = None
    for _ in range(2):
        jwks = await responses.json(_JWKS)
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise SiwcProtocolError(ErrorCode.INVALID_METADATA)
        seen = set()
        for item in keys:
            if (not isinstance(item, dict) or not isinstance(item.get("kid"), str) or not item["kid"]
                    or item["kid"] in seen or item.get("kty") != "RSA" or item.get("alg") != "RS256"
                    or item.get("use") != "sig" or "d" in item or "jku" in item or "x5u" in item
                    or ("key_ops" in item and item["key_ops"] != ["verify"])):
                raise SiwcProtocolError(ErrorCode.INVALID_METADATA)
            seen.add(item["kid"])
            if item["kid"] == header["kid"]:
                try:
                    key = jwt.PyJWK.from_dict(item, algorithm="RS256").key
                    if not 2048 <= key.key_size <= 8192:
                        raise ValueError()
                except (ValueError, TypeError, KeyError, jwt.PyJWTError):
                    raise SiwcProtocolError(ErrorCode.INVALID_METADATA) from None
        if key is not None:
            break
    if key is None:
        raise SiwcProtocolError(error)
    try:
        verified = jwt.decode(token, key, algorithms=["RS256"], audience=client_id,
                              issuer=_ISSUER, leeway=5,
                              options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]})
        aud = verified["aud"]
        if (not isinstance(verified["sub"], str) or not verified["sub"].strip()
                or not isinstance(verified["nonce"], str)
                or not hmac.compare_digest(verified["nonce"].encode(), nonce.encode())
                or ("azp" in verified and verified["azp"] != client_id)
                or (isinstance(aud, list) and len(aud) > 1 and verified.get("azp") != client_id)
                or verified["exp"] <= verified["iat"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError, jwt.PyJWTError):
        raise SiwcProtocolError(error) from None
    email = verified.get("email")
    return VerifiedIdentity(_ISSUER, verified["sub"], client_id, email if isinstance(email, str) else None)


class OpenAIProtocol:
    """transport 與 CA 僅供受信任的組裝程式；不能從 API 請求提供。"""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None,
                 ca_file: Path | None = None, timeout_seconds: float = 20):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise SiwcProtocolError(ErrorCode.INVALID_REQUEST)
        self._transport = transport
        self._ca_file = ca_file
        self._timeout_seconds = timeout_seconds

    def prepare(self, host_id: str, redirect_uri: str,
                registration: Registration | None = None) -> tuple[AuthorizationStart, PendingAuthorization]:
        match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/auth/callback", redirect_uri)
        if (match is None or int(match[1]) > 65535 or not isinstance(host_id, str)
                or not 1 <= len(host_id) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in host_id)):
            raise SiwcProtocolError(ErrorCode.INVALID_REQUEST)
        if registration is not None and (not _valid_client(registration.client_id)
                or (registration.identity is not None and
                    (registration.identity.client_id != registration.client_id or registration.identity.issuer != _ISSUER))):
            raise SiwcProtocolError(ErrorCode.INVALID_REQUEST)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        attempt_id = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + _ATTEMPT_TTL
        client_id = registration.client_id if registration else None
        query = {"client_id": client_id or _DYNAMIC_CLIENT, "response_type": "code", "redirect_uri": redirect_uri,
                 "scope": _SCOPES, "resource": _RESOURCE, "state": state, "nonce": nonce,
                 "code_challenge_method": "S256", "ext_agent_host_id": host_id,
                 "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()}
        if registration is None:
            query["agent_name_hint"] = "ChatPulse"
        pending = PendingAuthorization(attempt_id, hashlib.sha256(state.encode()).hexdigest(), nonce, verifier,
                                       redirect_uri, client_id, registration.identity if registration else None, expires_at)
        return AuthorizationStart(attempt_id, f"{_AUTHORIZE}?{urlencode(query)}", expires_at), pending

    async def complete(self, pending: PendingAuthorization, raw_query: bytes,
                       *, claim_callback: ClaimCallback) -> VerifiedGrant:
        claim, code = _callback(pending, raw_query)
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                try:
                    await claim_callback(claim)
                except Exception:
                    raise SiwcProtocolError(ErrorCode.CLAIM_REJECTED) from None
                if isinstance(claim, CallbackDenied):
                    raise SiwcProtocolError(ErrorCode.ACCESS_DENIED)
                async with private_http_client(deadline, ca_file=self._ca_file, transport=self._transport) as client:
                    responses = _Responses(client)
                    data = await responses.json(_TOKEN, form={"grant_type": "authorization_code",
                        "client_id": claim.issued_client_id, "code": code, "code_verifier": pending.verifier,
                        "redirect_uri": pending.redirect_uri, "resource": _RESOURCE})
                    received_at = datetime.now(timezone.utc)
                    scopes, credentials, token = _token_fields(data, received_at)
                    identity = await _identity(responses, token, claim.issued_client_id, pending.nonce)
                    if pending.previous_identity is not None and identity != pending.previous_identity:
                        raise SiwcProtocolError(ErrorCode.ACCOUNT_MISMATCH)
                    return VerifiedGrant(identity, scopes, received_at, token, credentials)
        except TimeoutError:
            raise SiwcProtocolError(ErrorCode.DEADLINE_EXCEEDED) from None
        except (httpx.HTTPError, OSError):
            raise SiwcProtocolError(ErrorCode.PROVIDER_UNAVAILABLE) from None
