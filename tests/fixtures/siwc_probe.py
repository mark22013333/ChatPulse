import asyncio
import io
import json
import logging
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.siwc_protocol import OpenAIProtocol, SiwcProtocolError
from siwc_oidc import (ACCESS_TOKEN, CLIENT_ID, CODE, FakeOIDC, LocalTLS,
                       REFRESH_TOKEN, RecordingClaim, TOKEN_PATH)


async def probe():
    fake = FakeOIDC()
    server = LocalTLS(fake)
    claim = RecordingClaim(fake)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger()
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    findings = {}
    try:
        protocol = OpenAIProtocol(transport=server.transport(), ca_file=server.ca_file)
        start, pending = protocol.prepare("urn:uuid:probe-host", "http://127.0.0.1:54321/auth/callback")
        state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
        fake.nonce = pending.nonce
        callback = urlencode({"state": state, "code": CODE, "client_id": CLIENT_ID}).encode()
        try:
            await protocol.complete(pending, b"state=wrong&code=fake&client_id=fake", claim_callback=claim)
        except SiwcProtocolError as error:
            findings["wrong_state"] = {"error": error.code.value, "token_calls": fake.token_calls, "claims": len(claim.claims)}
        grant = await protocol.complete(pending, callback, claim_callback=claim)
        findings["valid_exchange"] = {"token_calls": fake.token_calls, "claim_before_http": fake.events[:2] == ["claim", TOKEN_PATH],
                                      "has_plan_grant": grant.has_plan_grant, "has_credentials": grant.credentials is not None}
        try:
            await protocol.complete(pending, callback, claim_callback=claim)
        except SiwcProtocolError as error:
            findings["replay"] = {"error": error.code.value, "token_calls": fake.token_calls}
        denied_start, denied_pending = protocol.prepare("urn:uuid:probe-host", pending.redirect_uri)
        denied_state = parse_qs(urlsplit(denied_start.authorization_url).query)["state"][0]
        before = fake.token_calls
        try:
            await protocol.complete(denied_pending, urlencode({"state": denied_state, "error": "access_denied"}).encode(), claim_callback=claim)
        except SiwcProtocolError as error:
            findings["denial"] = {"error": error.code.value, "additional_token_calls": fake.token_calls - before,
                                  "consumed": denied_pending.attempt_id in claim.consumed}
        identity_start, identity_pending = protocol.prepare("urn:uuid:probe-host", pending.redirect_uri)
        identity_state = parse_qs(urlsplit(identity_start.authorization_url).query)["state"][0]
        fake.nonce = identity_pending.nonce
        fake.token_changes = {"scope": "openid profile email"}
        fake.drop_fields = {"access_token", "refresh_token", "token_type", "expires_in"}
        identity = await protocol.complete(identity_pending, urlencode({"state": identity_state, "code": CODE, "client_id": CLIENT_ID,
                                         "scope": "chatgpt.tokens.use.direct"}).encode(), claim_callback=claim)
        findings["identity_only"] = {"has_plan_grant": identity.has_plan_grant, "has_credentials": identity.credentials is not None}
        visible = stream.getvalue() + repr(grant) + repr(start) + repr(pending) + repr(claim.claims)
        findings["privacy"] = {"secret_found": any(secret in visible for secret in
            (ACCESS_TOKEN, REFRESH_TOKEN, CODE, pending.nonce, pending.verifier, state, grant.retained_id_token)),
            "request_path_logged": TOKEN_PATH in stream.getvalue()}
        passed = (findings["wrong_state"]["token_calls"] == 0 and findings["wrong_state"]["claims"] == 0
                  and findings["valid_exchange"]["token_calls"] == 1 and findings["valid_exchange"]["claim_before_http"]
                  and findings["valid_exchange"]["has_plan_grant"] and findings["valid_exchange"]["has_credentials"]
                  and findings["replay"]["token_calls"] == 1 and findings["replay"]["error"] == "claim_rejected"
                  and findings["denial"]["additional_token_calls"] == 0 and findings["denial"]["consumed"]
                  and not any(findings["identity_only"].values()) and not any(findings["privacy"].values()))
        print(json.dumps({"passed": passed, "surface": "loopback TLS fake OIDC", "findings": findings,
                          "not_verified": ["real_sign_in", "durable_viewer_binding", "activation", "refresh", "inference"]}, indent=2))
        return 0 if passed else 1
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        server.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
