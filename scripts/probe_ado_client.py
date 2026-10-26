"""自給的本機 TLS ADO 假服務探針；不讀環境、設定或正式服務。"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import ssl
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ado_client import AdoApiProfile, AdoClient, AdoConnection  # noqa: E402


def _certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ADO 離線假服務")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "fake-ca.pem"
    key_path = directory / "fake-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


class Handler(BaseHTTPRequestHandler):
    requests_seen: list[tuple[str, dict[str, list[str]]]] = []

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        self.requests_seen.append((parsed.path, query))
        prefix = "/Collection/Demo/_apis/wit/workItems/42"
        if self.headers.get("Authorization") != "Basic OkZBS0UtUEFULU9OTFk=":
            self.send_error(401)
            return
        if parsed.path == prefix and query == {"$expand": ["relations"], "api-version": ["7.1"]}:
            payload = {"id": 42, "rev": 1, "fields": {"System.Title": "離線假工單"}, "relations": []}
        elif parsed.path == prefix + "/revisions" and query == {"$top": ["100"], "$skip": ["0"], "api-version": ["7.1"]}:
            payload = {"count": 1, "value": [{"id": 42, "rev": 1, "fields": {"System.Title": "離線假工單"}}]}
        elif parsed.path == prefix + "/comments" and query == {"$top": ["100"], "api-version": ["7.1-preview.4"]}:
            payload = {"count": 1, "comments": [{"commentId": 7, "workItemId": 42, "version": 1, "text": "離線假留言"}], "continuationToken": None}
        else:
            self.send_error(404)
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


async def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("此探針不接受網址、token 或任何參數")
    with tempfile.TemporaryDirectory(prefix="ado-offline-") as temp:
        cert_path, key_path = _certificate(Path(temp))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = AdoClient(
                AdoConnection(f"https://127.0.0.1:{server.server_port}/Collection", "Demo", expected_port=server.server_port),
                AdoApiProfile("7.1", "7.1", "7.1-preview.4"),
                "FAKE-PAT-ONLY",
                verify=str(cert_path),
            )
            bundle = await client.read_item_bundle(42)
            assert (bundle.item.id, len(bundle.revisions), len(bundle.comments)) == (42, 1, 1)
            assert len(Handler.requests_seen) == 3
            print("PASS：本機 TLS 假服務讀取工單、revisions、comments；憑證驗證開啟")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
