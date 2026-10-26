import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import ssl
from typing import Any, AsyncIterator, Iterator

import certifi
import httpx


_private_http = ContextVar("private_http", default=False)


def _install_record_factory() -> None:
    previous_factory = logging.getLogRecordFactory()

    def private_record_factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        if _private_http.get() and (
            record.name in ("httpx", "httpcore")
            or record.name.startswith(("httpx.", "httpcore."))
        ):
            record.msg = "私密 HTTP 操作紀錄已隱藏"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    # 父 logger 的 filter 不會處理子 logger；factory 在所有 handler 前遮罩。
    logging.setLogRecordFactory(private_record_factory)


_install_record_factory()


@contextmanager
def private_http_logs() -> Iterator[None]:
    """保留其他 task 的紀錄設定，離開或取消時恢復呼叫端的範圍。"""
    token = _private_http.set(True)
    try:
        yield
    finally:
        _private_http.reset(token)


@asynccontextmanager
async def private_http_client(
    deadline: float,
    *,
    ca_file: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    connect_timeout: float = 3.0,
    read_timeout: float = 8.0,
) -> AsyncIterator[httpx.AsyncClient]:
    """以單一期限持有私密 HTTP 操作，取消時關閉尚未交接的 TLS stream。"""
    pending_stream = None

    async def trace(event: str, info: dict[str, Any]) -> None:
        nonlocal pending_stream
        if event == "connection.connect_tcp.complete":
            pending_stream = info["return_value"]
        elif event == "connection.start_tls.complete":
            pending_stream = None

    async def install_trace(request: httpx.Request) -> None:
        request.extensions["trace"] = trace

    with private_http_logs():
        async with asyncio.timeout_at(deadline):
            # create_default_context 會讀 SSLKEYLOGFILE，不能用於私密連線。
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(cafile=str(ca_file) if ca_file is not None else certifi.where())
            except (OSError, ValueError):
                raise httpx.ConnectError("TLS 信任鏈設定無效") from None
            try:
                async with httpx.AsyncClient(
                    verify=context,
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
                    event_hooks={"request": [install_trace]},
                ) as client:
                    yield client
            finally:
                # 2026-10-25 本機實測，TLS 握手取消時 pool 尚未持有 stream，須在 GC 前關閉。
                if pending_stream is not None:
                    await pending_stream.aclose()
