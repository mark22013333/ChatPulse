import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import sys
import time
import traceback
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from core.siwc_protocol import (
    CallbackAccepted, CallbackDenied, ErrorCode, OpenAIProtocol, Registration,
    SiwcProtocolError, VerifiedIdentity,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from siwc_oidc import (ACCESS_TOKEN, CLIENT_ID, CODE, DISCOVERY_PATH, FakeOIDC, ISSUER,
                       JWKS_PATH, LocalTLS, REFRESH_TOKEN, RecordingClaim, TOKEN_PATH)


class SiwcProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake = FakeOIDC()
        self.server = LocalTLS(self.fake)
        self.addCleanup(self.server.close)
        self.claim = RecordingClaim(self.fake)
        self.prepare()

    def prepare(self, registration=None, timeout_seconds=20):
        self.protocol = OpenAIProtocol(transport=self.server.transport(), ca_file=self.server.ca_file,
                                      timeout_seconds=timeout_seconds)
        self.start, self.pending = self.protocol.prepare("urn:uuid:synthetic-host", "http://127.0.0.1:54321/auth/callback", registration)
        self.query = parse_qs(urlsplit(self.start.authorization_url).query)
        self.fake.nonce = self.pending.nonce
        self.callback = urlencode({"state": self.query["state"][0], "code": CODE, "client_id": CLIENT_ID}).encode()

    async def complete(self, raw=None, pending=None):
        return await self.protocol.complete(pending or self.pending, self.callback if raw is None else raw,
                                            claim_callback=self.claim)

    async def failure(self, code, raw=None, pending=None):
        with self.assertRaises(SiwcProtocolError) as caught:
            await self.complete(raw, pending)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_prepare_dynamic_and_pkce(self):
        self.assertEqual(self.query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(self.query["agent_name_hint"], ["ChatPulse"])
        self.assertEqual(self.query["ext_agent_host_id"], ["urn:uuid:synthetic-host"])
        self.assertEqual(self.query["resource"], ["https://api.openai.com/v1"])
        self.assertEqual(self.query["code_challenge_method"], ["S256"])
        digest = base64.urlsafe_b64encode(hashlib.sha256(self.pending.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(self.query["code_challenge"], [digest])
        self.assertNotIn("id_token_hint", self.query)
        self.assertNotIn(self.query["state"][0], repr(self.start))
        self.assertNotIn(self.pending.nonce, repr(self.pending))
        self.assertNotIn(self.pending.verifier, repr(self.pending))

    def test_prepare_rejects_nonexact_loopback(self):
        for uri in ("http://localhost:54321/auth/callback", "http://127.0.0.1:0/auth/callback",
                    "http://127.0.0.1:65536/auth/callback", "http://127.0.0.1:54321/auth/callback?x=y",
                    "http://127.0.0.1:54321/auth/callback#fragment", "https://127.0.0.1:54321/auth/callback"):
            with self.subTest(uri=uri), self.assertRaises(SiwcProtocolError):
                self.protocol.prepare("fake-host", uri)
        with self.assertRaises(SiwcProtocolError):
            self.protocol.prepare("fake-host", self.pending.redirect_uri, Registration("dynamic_agent_client"))

    async def test_dynamic_exchange_claim_precedes_http_and_form_matches_pending(self):
        grant = await self.complete()
        self.assertEqual(self.fake.events, ["claim", TOKEN_PATH, DISCOVERY_PATH, JWKS_PATH])
        self.assertIsInstance(self.claim.claims[0], CallbackAccepted)
        self.assertEqual(self.claim.claims[0].issued_client_id, CLIENT_ID)
        self.assertEqual(self.fake.token_calls, 1)
        method, _, form = self.fake.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(form, {"grant_type": ["authorization_code"], "client_id": [CLIENT_ID], "code": [CODE],
            "code_verifier": [self.pending.verifier], "redirect_uri": [self.pending.redirect_uri],
            "resource": ["https://api.openai.com/v1"]})
        self.assertTrue(grant.has_plan_grant)
        self.assertEqual(grant.credentials.access_token, ACCESS_TOKEN)
        self.assertEqual(grant.credentials.refresh_token, REFRESH_TOKEN)
        self.assertEqual(grant.identity.subject, "synthetic-subject")

    async def test_reauthorize_can_omit_client_id_and_email_is_not_identity(self):
        previous = VerifiedIdentity(ISSUER, "synthetic-subject", CLIENT_ID, "old@example.invalid")
        self.prepare(Registration(CLIENT_ID, previous))
        self.assertNotIn("agent_name_hint", self.query)
        raw = urlencode({"state": self.query["state"][0], "code": CODE}).encode()
        grant = await self.complete(raw)
        self.assertEqual(grant.identity, previous)
        self.assertNotEqual(grant.identity.email_label, previous.email_label)

    async def test_new_registration_needs_issued_client(self):
        for value in (None, "", "dynamic_agent_client", "bad/client"):
            with self.subTest(value=value):
                fields = {"state": self.query["state"][0], "code": CODE}
                if value is not None:
                    fields["client_id"] = value
                await self.failure(ErrorCode.INVALID_CALLBACK, urlencode(fields).encode())
        self.assertEqual(self.claim.claims, [])
        self.assertEqual(self.fake.token_calls, 0)

    async def test_changed_client_id_fails_before_claim(self):
        self.prepare(Registration(CLIENT_ID))
        raw = urlencode({"state": self.query["state"][0], "code": CODE, "client_id": "other_client"}).encode()
        await self.failure(ErrorCode.INVALID_CALLBACK, raw)
        self.assertEqual(self.claim.claims, [])
        self.assertEqual(self.fake.token_calls, 0)

    async def test_malformed_callback_never_claims_or_requests(self):
        state = self.query["state"][0]
        invalid = [b"", b"state=wrong&code=fake&client_id=test_issued_client",
                   self.callback + b"&state=duplicate", self.callback + b"&scope=a&scope=b",
                   self.callback + b"&code=duplicate", self.callback + b"&x=%GG",
                   self.callback + b"&x=%FF", self.callback + b"&broken", b"x=" + b"a" * 8192,
                   urlencode({"state": state, "code": "\n", "client_id": CLIENT_ID}).encode(),
                   urlencode({"state": state, "code": CODE, "client_id": CLIENT_ID, "error": "access_denied"}).encode()]
        for raw in invalid:
            with self.subTest(length=len(raw)):
                await self.failure(ErrorCode.INVALID_CALLBACK, raw)
        self.assertEqual(self.claim.claims, [])
        self.assertEqual(self.fake.requests, [])

    async def test_expired_callback_does_not_claim(self):
        await self.failure(ErrorCode.ATTEMPT_EXPIRED, pending=replace(self.pending,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertEqual(self.claim.claims, [])
        self.assertEqual(self.fake.requests, [])

    async def test_denial_consumes_claim_once_without_http(self):
        raw = urlencode({"state": self.query["state"][0], "error": "access_denied"}).encode()
        await self.failure(ErrorCode.ACCESS_DENIED, raw)
        self.assertIsInstance(self.claim.claims[0], CallbackDenied)
        await self.failure(ErrorCode.CLAIM_REJECTED, raw)
        self.assertEqual(len(self.claim.claims), 1)
        self.assertEqual(self.fake.requests, [])

    async def test_claim_failure_hides_storage_exception_without_http(self):
        self.claim.failure = RuntimeError("SYNTHETIC_SQL_SECRET")
        error = await self.failure(ErrorCode.CLAIM_REJECTED)
        self.assertNotIn("SYNTHETIC_SQL_SECRET", "".join(traceback.format_exception(error)))
        self.assertEqual(self.fake.requests, [])

    async def test_replay_and_concurrent_callbacks_exchange_only_once(self):
        results = await asyncio.gather(self.complete(), self.complete(), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, SiwcProtocolError) for item in results), 1)
        self.assertEqual(self.fake.token_calls, 1)
        await self.failure(ErrorCode.CLAIM_REJECTED)
        self.assertEqual(self.fake.token_calls, 1)

    async def test_identity_only_ignores_callback_scope(self):
        self.fake.token_changes = {"scope": "openid email profile"}
        self.fake.drop_fields = {"access_token", "refresh_token", "expires_in", "token_type"}
        grant = await self.complete(self.callback + b"&scope=chatgpt.tokens.use.direct")
        self.assertIsNone(grant.credentials)
        self.assertFalse(grant.has_plan_grant)
        self.assertEqual(grant.scopes, frozenset({"openid", "email", "profile"}))

    async def test_plan_or_offline_grant_without_access_rejected(self):
        for scope in ("openid chatgpt.tokens.use.direct", "openid offline_access"):
            with self.subTest(scope=scope):
                self.prepare()
                self.fake.token_changes = {"scope": scope}
                self.fake.drop_fields = {"access_token", "refresh_token", "expires_in", "token_type"}
                await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)

    async def test_incomplete_token_fields_rejected(self):
        cases = [{"scope": None}, {"scope": "email"}, {"scope": "openid\nemail"}, {"id_token": ""},
                 {"access_token": ""}, {"refresh_token": ""}, {"token_type": "MAC"},
                 {"expires_in": 0}, {"expires_in": True}, {"expires_in": "3600"},
                 {"expires_in": 1e40}, {"earliest_refresh_at": ""}, {"earliest_refresh_at": True}]
        for fields in cases:
            with self.subTest(fields=list(fields)):
                self.prepare()
                self.fake.token_changes = fields
                await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)
        self.prepare()
        self.fake.token_changes = {}
        self.fake.drop_fields = {"refresh_token"}
        await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)

    async def test_refresh_hint_is_preserved_without_grant_inference(self):
        for hint in (1800000000, "2027-01-01T00:00:00Z"):
            with self.subTest(hint=hint):
                self.prepare()
                self.fake.token_changes = {"scope": "openid", "earliest_refresh_at": hint}
                grant = await self.complete()
                self.assertEqual(grant.credentials.earliest_refresh_at, hint)
                self.assertFalse(grant.has_plan_grant)

    async def test_reflected_refresh_hint_is_retained_without_repr_disclosure(self):
        self.fake.token_changes = {"earliest_refresh_at": ACCESS_TOKEN}
        grant = await self.complete()
        self.assertEqual(grant.credentials.earliest_refresh_at, ACCESS_TOKEN)
        self.assertNotIn(ACCESS_TOKEN, repr(grant.credentials))
        self.assertNotIn(ACCESS_TOKEN, repr(grant))

    async def test_invalid_bearer_tokens_rejected_at_response_boundary(self):
        for token in ("", "with space", "fake\r\nX-Test: injected", "非ASCII", "===", "a=b"):
            with self.subTest(token_shape=len(token)):
                self.prepare()
                self.fake.token_changes = {"access_token": token}
                self.fake.requests.clear()
                await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)
                self.assertEqual([path for _, path, _ in self.fake.requests], [TOKEN_PATH])

    async def test_bearer_alphabet_and_trailing_padding_accepted(self):
        token = "AZaz09-._~+/=="
        self.fake.token_changes = {"access_token": token}
        grant = await self.complete()
        self.assertEqual(grant.credentials.access_token, token)
        self.assertTrue(grant.has_plan_grant)

    async def test_jwt_claim_failures(self):
        now = int(time.time())
        cases = [{"iss": "https://other.invalid"}, {"aud": "other"}, {"nonce": "wrong"},
                 {"exp": now - 60}, {"iat": now + 60}, {"nbf": now + 60}, {"sub": ""}, {"sub": 1},
                 {"sub": " "}, {"exp": True}, {"iat": "123"}, {"azp": "other"},
                 {"aud": [CLIENT_ID, "other"]}, {"aud": [CLIENT_ID, "other"], "azp": "wrong"}]
        for claims in cases:
            with self.subTest(claims=list(claims)):
                self.prepare()
                self.fake.claim_changes = claims
                await self.failure(ErrorCode.INVALID_ID_TOKEN)

    async def test_multiple_audience_requires_exact_azp(self):
        self.fake.claim_changes = {"aud": [CLIENT_ID, "other"], "azp": CLIENT_ID}
        grant = await self.complete()
        self.assertEqual(grant.identity.client_id, CLIENT_ID)

    async def test_returning_subject_mismatch_rejected(self):
        self.prepare(Registration(CLIENT_ID, VerifiedIdentity(ISSUER, "previous-subject", CLIENT_ID)))
        await self.failure(ErrorCode.ACCOUNT_MISMATCH)

    async def test_none_hs256_and_wrong_signature_rejected(self):
        claims = {"iss": ISSUER, "sub": "synthetic", "aud": CLIENT_ID,
                  "iat": int(time.time()), "exp": int(time.time()) + 600, "nonce": self.pending.nonce}
        tokens = [jwt.encode(claims, "", algorithm="none"),
                  jwt.encode(claims, "fake-key-that-is-long-enough-12345", algorithm="HS256", headers={"kid": "fake-key"}),
                  jwt.encode(claims, self.fake.other_key, algorithm="RS256", headers={"kid": "fake-key"})]
        for token in tokens:
            with self.subTest(length=len(token)):
                self.prepare()
                self.fake.id_token_override = token
                await self.failure(ErrorCode.INVALID_ID_TOKEN)

    async def test_header_url_never_causes_external_request(self):
        for field in ("jku", "x5u"):
            with self.subTest(field=field):
                self.prepare()
                self.fake.header_changes = {field: "https://attacker.invalid/secret"}
                self.fake.requests.clear()
                await self.failure(ErrorCode.INVALID_ID_TOKEN)
                self.assertEqual([path for _, path, _ in self.fake.requests], [TOKEN_PATH])

    async def test_unknown_kid_retried_only_once(self):
        self.fake.header_changes = {"kid": "unknown"}
        await self.failure(ErrorCode.INVALID_ID_TOKEN)
        self.assertEqual([path for _, path, _ in self.fake.requests], [TOKEN_PATH, DISCOVERY_PATH, JWKS_PATH, JWKS_PATH])

    async def test_unknown_kid_can_recover_on_second_jwks(self):
        attempts = []
        def response():
            attempts.append(1)
            key = dict(self.fake.jwk, kid="other") if len(attempts) == 1 else self.fake.jwk
            return 200, {}, json.dumps({"keys": [key]}).encode()
        self.fake.overrides[JWKS_PATH] = response
        self.assertTrue((await self.complete()).has_plan_grant)
        self.assertEqual(len(attempts), 2)

    async def test_invalid_or_duplicate_jwks_rejected(self):
        keys = [[], [self.fake.jwk, self.fake.jwk], [dict(self.fake.jwk, kty="oct")],
                [dict(self.fake.jwk, alg="HS256")], [dict(self.fake.jwk, use="enc")],
                [dict(self.fake.jwk, n="!")], [dict(self.fake.jwk, key_ops=["sign"])]]
        for case in keys:
            with self.subTest(count=len(case)):
                self.prepare()
                self.fake.jwks = {"keys": case}
                await self.failure(ErrorCode.INVALID_METADATA)

    async def test_discovery_cannot_change_endpoints_or_algorithms(self):
        for field, value in (("issuer", "https://other.invalid"), ("jwks_uri", "https://other.invalid/keys"),
                             ("token_endpoint", ISSUER + "/different"), ("id_token_signing_alg_values_supported", ["HS256"])):
            with self.subTest(field=field):
                self.prepare()
                previous = self.fake.metadata[field]
                self.fake.metadata[field] = value
                await self.failure(ErrorCode.INVALID_METADATA)
                self.fake.metadata[field] = previous

    async def test_strict_response_json_and_response_size(self):
        for body in (b'{"scope":"openid","scope":"openid"}', b'{"x":NaN}', b'{"x":1e999}',
                     b'[]', b'\xff', b'{', b'{"x":' + b'9' * 1000 + b'}', b' ' * 131073):
            with self.subTest(length=len(body)):
                self.prepare()
                self.fake.overrides[TOKEN_PATH] = (200, {}, body)
                await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)

    async def test_jwt_duplicate_claim_and_header_rejected(self):
        claims = json.dumps({"iss": ISSUER, "aud": CLIENT_ID, "sub": "synthetic", "exp": int(time.time()) + 600,
                             "iat": int(time.time()), "nonce": self.pending.nonce})
        for header, payload in ((b'{"alg":"RS256","kid":"fake-key","kid":"fake-key"}', claims.encode()),
                                (b'{"alg":"RS256","kid":"fake-key"}', claims[:-1].encode() + b',"sub":"other"}')):
            self.prepare()
            signing = b".".join(base64.urlsafe_b64encode(part).rstrip(b"=") for part in (header, payload))
            signature = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256).sign(signing, self.fake.key)
            self.fake.id_token_override = (signing + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
            await self.failure(ErrorCode.INVALID_ID_TOKEN)

    async def test_redirect_not_followed_and_invalid_grant_keeps_claim(self):
        self.fake.overrides[TOKEN_PATH] = (302, {"Location": "https://attacker.invalid"}, b"SYNTHETIC_BODY_SECRET")
        error = await self.failure(ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertNotIn("SYNTHETIC_BODY_SECRET", str(error))
        self.assertEqual(len(self.fake.requests), 1)
        self.prepare()
        self.fake.overrides[TOKEN_PATH] = (400, {}, b'{"error":"invalid_grant","detail":"SYNTHETIC_BODY_SECRET"}')
        await self.failure(ErrorCode.INVALID_GRANT)
        self.assertEqual(self.claim.claims[-1].issued_client_id, CLIENT_ID)

    async def test_body_budget_accumulates_across_requests(self):
        self.fake.overrides[TOKEN_PATH] = lambda: (200, {}, json.dumps(dict(self.fake.token(), padding="x" * 100000)).encode())
        self.fake.overrides[DISCOVERY_PATH] = (200, {}, json.dumps(dict(self.fake.metadata, padding="x" * 100000)).encode())
        self.fake.overrides[JWKS_PATH] = (200, {}, json.dumps(dict(self.fake.jwks, padding="x" * 100000)).encode())
        await self.failure(ErrorCode.INVALID_METADATA)

    async def test_timeout_is_shared_across_token_discovery_and_jwks(self):
        self.prepare(timeout_seconds=0.3)
        self.fake.delays = {TOKEN_PATH: 0.11, DISCOVERY_PATH: 0.11, JWKS_PATH: 0.3}
        started = time.monotonic()
        await self.failure(ErrorCode.DEADLINE_EXCEEDED)
        self.assertLess(time.monotonic() - started, 0.7)
        self.assertEqual(self.fake.token_calls, 1)

    async def test_slow_body_is_bounded_by_operation_deadline(self):
        self.prepare(timeout_seconds=0.15)
        self.fake.drip.add(TOKEN_PATH)
        started = time.monotonic()
        await self.failure(ErrorCode.DEADLINE_EXCEEDED)
        self.assertLess(time.monotonic() - started, 0.5)

    async def test_cancel_preserves_cancelled_error_and_consumed_claim(self):
        self.fake.delays[TOKEN_PATH] = 0.5
        task = asyncio.create_task(self.complete())
        for _ in range(100):
            if self.fake.token_calls:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.fake.token_calls, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.failure(ErrorCode.CLAIM_REJECTED)
        self.assertEqual(self.fake.token_calls, 1)

    async def test_missing_required_jwt_claims_rejected(self):
        for field in ("iss", "aud", "exp", "iat", "sub", "nonce"):
            with self.subTest(field=field):
                self.prepare()
                claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "synthetic",
                          "iat": int(time.time()), "exp": int(time.time()) + 600, "nonce": self.pending.nonce}
                del claims[field]
                self.fake.id_token_override = jwt.encode(claims, self.fake.key, algorithm="RS256", headers={"kid": "fake-key"})
                await self.failure(ErrorCode.INVALID_ID_TOKEN)

    async def test_claim_cannot_be_omitted_and_cancellation_before_claim_has_no_http(self):
        with self.assertRaises(TypeError):
            await self.protocol.complete(self.pending, self.callback)
        async def cancel(_claim):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.protocol.complete(self.pending, self.callback, claim_callback=cancel)
        self.assertEqual(self.fake.requests, [])

    async def test_claim_timeout_prevents_http(self):
        self.prepare(timeout_seconds=0.05)
        async def slow_claim(_claim):
            await asyncio.sleep(1)
        with self.assertRaises(SiwcProtocolError) as caught:
            await self.protocol.complete(self.pending, self.callback, claim_callback=slow_claim)
        self.assertEqual(caught.exception.code, ErrorCode.DEADLINE_EXCEEDED)
        self.assertEqual(self.fake.requests, [])

    async def test_compressed_response_rejected_without_decompression(self):
        self.fake.overrides[TOKEN_PATH] = (200, {"Content-Encoding": "gzip"}, b"SYNTHETIC_BODY_SECRET")
        await self.failure(ErrorCode.INVALID_TOKEN_RESPONSE)

    async def test_secrets_absent_from_repr_and_debug_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        try:
            grant = await self.complete()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        visible = stream.getvalue() + repr(grant) + repr(grant.credentials) + repr(self.start) + repr(self.pending) + repr(self.claim.claims)
        for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CODE, grant.retained_id_token,
                       self.pending.nonce, self.pending.verifier, self.query["state"][0], self.start.authorization_url):
            self.assertNotIn(secret, visible)
        self.assertNotIn(TOKEN_PATH, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
