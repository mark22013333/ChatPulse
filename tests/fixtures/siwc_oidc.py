import ipaddress
import json
import socket
import ssl
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


ISSUER = "https://auth.openai.com"
TOKEN_PATH = "/api/accounts/oauth/token"
DISCOVERY_PATH = "/.well-known/openid-configuration"
JWKS_PATH = "/.well-known/jwks.json"
CLIENT_ID = "test_issued_client"
ACCESS_TOKEN = "SYNTHETIC_ACCESS_CANARY"
REFRESH_TOKEN = "SYNTHETIC_REFRESH_CANARY"
CODE = "SYNTHETIC_CODE_CANARY"


class FakeOIDC:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.requests = []
        self.events = []
        self.metadata = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/api/accounts/authorize",
                         "token_endpoint": ISSUER + TOKEN_PATH, "jwks_uri": ISSUER + JWKS_PATH,
                         "revocation_endpoint": ISSUER + "/api/accounts/oauth/revoke",
                         "id_token_signing_alg_values_supported": ["RS256"]}
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid="fake-key", alg="RS256", use="sig")
        self.jwks = {"keys": [self.jwk]}
        self.overrides = {}
        self.delays = {}
        self.drip = set()
        self.nonce = "unset"
        self.claim_changes = {}
        self.header_changes = {}
        self.token_changes = {}
        self.drop_fields = set()
        self.id_token_override = None

    def token(self):
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": "synthetic-subject", "aud": CLIENT_ID,
                  "iat": now, "exp": now + 600, "nonce": self.nonce, "email": "fake@example.invalid"}
        claims.update(self.claim_changes)
        token = self.id_token_override
        if token is None:
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "fake-key", **self.header_changes})
        data = {"id_token": token, "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
                "access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "token_type": "Bearer", "expires_in": 3600}
        data.update(self.token_changes)
        for key in self.drop_fields:
            data.pop(key, None)
        return data

    def response(self, path):
        if path in self.overrides:
            override = self.overrides[path]
            return override() if callable(override) else override
        data = {TOKEN_PATH: self.token, DISCOVERY_PATH: lambda: self.metadata, JWKS_PATH: lambda: self.jwks}
        if path not in data:
            return 404, {}, b""
        return 200, {"Content-Type": "application/json"}, json.dumps(data[path]()).encode()

    @property
    def token_calls(self):
        return sum(path == TOKEN_PATH for _, path, _ in self.requests)


class RecordingClaim:
    def __init__(self, fake):
        self.fake = fake
        self.claims = []
        self.consumed = set()
        self.failure = None

    async def __call__(self, claim):
        if self.failure is not None:
            raise self.failure
        if claim.attempt_id in self.consumed:
            raise ValueError("SYNTHETIC_REPLAY_SECRET")
        self.consumed.add(claim.attempt_id)
        self.claims.append(claim)
        self.fake.events.append("claim")


class LocalTLS:
    def __init__(self, fake):
        self.fake = fake
        self.directory = tempfile.TemporaryDirectory(prefix="siwc-fake-")
        directory = Path(self.directory.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SIWC synthetic test")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
        self.ca_file = directory / "ca.pem"
        key_file = directory / "key.pem"
        self.ca_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                fixture.fake.requests.append((self.command, self.path, parse_qs(body.decode())))
                fixture.fake.events.append(self.path)
                time.sleep(fixture.fake.delays.get(self.path, 0))
                status, headers, body = fixture.fake.response(self.path)
                try:
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.path in fixture.fake.drip:
                        for offset in range(0, len(body), 20):
                            self.wfile.write(body[offset:offset + 20])
                            self.wfile.flush()
                            time.sleep(0.03)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

        class Server(ThreadingHTTPServer):
            def __init__(self):
                self.connections = set()
                self.connections_lock = threading.Lock()
                super().__init__(("127.0.0.1", 0), Handler)

            def get_request(self):
                connection, address = super().get_request()
                with self.connections_lock:
                    self.connections.add(connection)
                return connection, address

            def shutdown_request(self, connection):
                try:
                    super().shutdown_request(connection)
                finally:
                    with self.connections_lock:
                        self.connections.discard(connection)

            def finish_request(self, request, client_address):
                try:
                    super().finish_request(request, client_address)
                except (ssl.SSLError, ConnectionError):
                    pass

            def close_connections(self):
                with self.connections_lock:
                    connections = tuple(self.connections)
                for connection in connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    connection.close()

        self.server = Server()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca_file, key_file)
        # 2026-10-26 實測：accept 內握手會讓未送 ClientHello 的連線卡住 shutdown。
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True,
                                                 do_handshake_on_connect=False)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def transport(self):
        fixture = self
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(self.ca_file)

        class MappedTLS(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.AsyncHTTPTransport(verify=context, trust_env=False, retries=0)

            async def handle_async_request(self, request):
                if str(request.url).split("/.well-known")[0].split("/api/")[0] != ISSUER:
                    raise AssertionError("測試傳輸拒絕固定 issuer 以外的 URL")
                mapped = httpx.Request(request.method,
                    request.url.copy_with(host="127.0.0.1", port=fixture.server.server_port),
                    headers=request.headers, stream=request.stream, extensions=request.extensions)
                return await self.inner.handle_async_request(mapped)

            async def aclose(self):
                await self.inner.aclose()

        return MappedTLS()

    def close(self):
        self.server.shutdown()
        self.server.close_connections()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()
