import gc
import json
from pathlib import Path
import socket
import ssl
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from siwc_oidc import DISCOVERY_PATH, FakeOIDC, LocalTLS


class SiwcFixtureTests(unittest.TestCase):
    def assert_closes_with_idle_client(self, complete_handshake):
        server = LocalTLS(FakeOIDC())
        client = socket.create_connection(("127.0.0.1", server.server.server_port), timeout=2)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(server.ca_file)
        if complete_handshake:
            client = context.wrap_socket(client, server_hostname="127.0.0.1")
            client.sendall(b"GET / HTTP/1.1\r\n")
        errors = []
        closed = threading.Event()

        def close():
            try:
                server.close()
            except Exception as error:
                errors.append(error)
            finally:
                closed.set()

        thread = threading.Thread(target=close, daemon=True)
        gc_enabled = gc.isenabled()
        gc.disable()
        try:
            with socket.create_connection(("127.0.0.1", server.server.server_port), timeout=2) as raw:
                with context.wrap_socket(raw, server_hostname="127.0.0.1") as active:
                    active.sendall(f"GET {DISCOVERY_PATH} HTTP/1.0\r\n\r\n".encode())
                    response = bytearray()
                    while data := active.recv(65536):
                        response.extend(data)
            self.assertTrue(response.startswith(b"HTTP/1.0 200"))
            metadata = json.loads(response.partition(b"\r\n\r\n")[2])
            self.assertEqual(metadata["issuer"], "https://auth.openai.com")
            thread.start()
            self.assertTrue(closed.wait(2), "保留 idle client 時 fixture 必須能關閉")
            self.assertEqual(errors, [])
            self.assertFalse(server.thread.is_alive())
            try:
                remaining = client.recv(1)
            except ConnectionResetError:
                remaining = b""
            self.assertEqual(remaining, b"")
            self.assertFalse(gc.isenabled())
        finally:
            client.close()
            if thread.ident is None:
                thread.start()
            thread.join(3)
            if gc_enabled:
                gc.enable()
        self.assertFalse(thread.is_alive())

    def test_incomplete_handshake_does_not_block_serving_or_cleanup(self):
        self.assert_closes_with_idle_client(False)

    def test_incomplete_http_request_does_not_block_cleanup(self):
        self.assert_closes_with_idle_client(True)


if __name__ == "__main__":
    unittest.main()
