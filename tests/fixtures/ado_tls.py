import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import ssl
import tempfile
from urllib.parse import parse_qs, urlsplit

from core.ado_client import AdoApiProfile, AdoClient, AdoConnection
from scripts.probe_ado_client import _certificate
from tests.fixtures.ado_offline import ITEM

PROFILE = AdoApiProfile("7.1", "7.1", "7.1-preview.4")


class LocalTlsService:
    def __init__(self, steps, certificate, key):
        self.steps = list(steps)
        self.seen = []
        self.events = []
        self.errors = []
        self.handshake_failures = []
        self.tasks = set()
        self.certificate = certificate
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate, key)

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, ssl=self.context, ssl_handshake_timeout=1, ssl_shutdown_timeout=1)
        self.raw_server = await asyncio.start_server(self.stall_tls, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.raw_port = self.raw_server.sockets[0].getsockname()[1]

    def client(self, *, stalled_tls=False, **options):
        port = self.raw_port if stalled_tls else self.port
        return AdoClient(
            AdoConnection(f"https://127.0.0.1:{port}/Collection", "Demo Project", expected_port=port),
            PROFILE,
            "FAKE-PAT-ONLY",
            verify=options.pop("verify", str(self.certificate)),
            **options,
        )

    async def stall_tls(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        event = {"case": "tls_handshake", "started": asyncio.get_running_loop().time()}
        self.events.append(event)
        try:
            event["client_hello_bytes"] = len(await reader.read(65536))
            event["eof"] = await reader.read() == b""
            event["elapsed"] = asyncio.get_running_loop().time() - event["started"]
        finally:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        children = []
        event = {"started": asyncio.get_running_loop().time()}
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            request_line, *headers = request.decode("ascii").split("\r\n")
            method, target, _ = request_line.split(" ")
            parsed = urlsplit(target)
            headers = dict(line.split(": ", 1) for line in headers if line)
            headers = {key.lower(): value for key, value in headers.items()}
            self.seen.append({
                "method": method, "path": parsed.path, "query": parse_qs(parsed.query),
                "auth_ok": headers.get("authorization") == "Basic OkZBS0UtUEFULU9OTFk=",
                "identity": headers.get("accept-encoding") == "identity",
            })
            step = self.steps.pop(0)
            event["case"] = step.get("mode", "normal")
            self.events.append(event)

            async def send():
                for data, interval in response_parts(step):
                    pieces = (bytes([byte]) for byte in data) if interval else [data]
                    for piece in pieces:
                        if step.get("delay"):
                            await asyncio.sleep(step["delay"])
                        writer.write(piece)
                        await writer.drain()
                        event["sent_bytes"] = event.get("sent_bytes", 0) + len(piece)
                        if interval:
                            await asyncio.sleep(interval)
                event["fully_sent"] = True
                if step.get("mode", "").startswith("truncated"):
                    writer.close()

            sender = asyncio.create_task(send())
            eof = asyncio.create_task(reader.read())
            children = [sender, eof]
            done, _ = await asyncio.wait(children, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                try:
                    await sender
                except (ConnectionError, ssl.SSLError):
                    event["send_disconnected"] = True
            event["eof"] = await eof == b""
            event["elapsed"] = asyncio.get_running_loop().time() - event["started"]
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError) as exc:
            event["disconnect_type"] = type(exc).__name__
        except Exception as exc:
            self.errors.append(type(exc).__name__)
        finally:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.5)
            except (ConnectionError, ssl.SSLError, TimeoutError):
                pass
            self.tasks.discard(task)

    async def wait_idle(self):
        async with asyncio.timeout(1):
            while self.tasks:
                await asyncio.sleep(0.005)

    async def close(self):
        for server in (self.server, self.raw_server):
            server.close()
        active = list(self.tasks)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        for server in (self.server, self.raw_server):
            await server.wait_closed()


def response_parts(step):
    body = step.get("raw", json.dumps(step.get("payload", ITEM)).encode("utf-8"))
    status = step.get("status", 200)
    header = f"HTTP/1.1 {status} Fake\r\nContent-Type: {step.get('content_type', 'application/json')}\r\n".encode()
    for key, name in (("content_encoding", "Content-Encoding"), ("location", "Location")):
        if key in step:
            header += f"{name}: {step[key]}\r\n".encode()
    for name, value in step.get("headers", {}).items():
        header += f"{name}: {value}\r\n".encode()
    length = f"Content-Length: {len(body)}\r\n".encode()
    chunked = header + b"Transfer-Encoding: chunked\r\n\r\n"
    size = f"{len(body):x}".encode()
    mode = step.get("mode", "normal")
    interval = step.get("drip", 0.025)
    if mode == "slow_header":
        return [(header + length, 0), (b"X-Slow: " + b"x" * 40 + b"\r\n\r\n", interval), (body, 0)]
    if mode == "slow_extension":
        return [(chunked, 0), (size + b";" + b"x" * 40 + b"\r\n", interval), (body + b"\r\n0\r\n\r\n", 0)]
    if mode == "slow_trailer":
        return [(chunked + size + b"\r\n" + body + b"\r\n0\r\nX-Slow: ", 0), (b"x" * 40 + b"\r\n\r\n", interval)]
    if mode == "chunked":
        return [(chunked + size + b"\r\n" + body + b"\r\n0\r\n\r\n", 0)]
    if mode == "truncated_chunk":
        return [(chunked + size + b"\r\n" + body[:-2], 0)]
    if mode == "truncated_length":
        return [(header + length + b"\r\n" + body[:-2], 0)]
    return [(header + length + b"\r\n", 0), (body, step.get("drip", 0))]


@asynccontextmanager
async def service(steps):
    with tempfile.TemporaryDirectory(prefix="ado-local-tls-") as directory:
        certificate, key = _certificate(Path(directory))
        server = LocalTlsService(steps, certificate, key)
        await server.start()
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def connection_error(loop, context):
            error = context.get("exception")
            if context.get("message") == "Error on transport creation for incoming connection" and isinstance(error, (ConnectionResetError, ssl.SSLError)):
                server.handshake_failures.append(type(error).__name__)
            elif previous_handler is not None:
                previous_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(connection_error)
        try:
            yield server, server.client
        finally:
            await server.close()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_handler)
