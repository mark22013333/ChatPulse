import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import ssl
import subprocess
import sys
import unittest

import httpx

from core.ado_client import AdoLimits, AdoReadError
from core.private_http import private_http_logs
from tests.fixtures.ado_tls import service


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def capture_http_logs(*extra_names):
    handler = Capture()
    loggers = [logging.getLogger(name) for name in ('httpx', 'httpcore', *extra_names)]
    settings = [(logger, logger.level, logger.propagate, logger.disabled) for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        logger.disabled = False
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler.records
    finally:
        root.removeHandler(handler)
        for logger, level, propagate, disabled in settings:
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


class HttpPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tls_debug_is_private_while_concurrent_http_logging_remains(self):
        secret = 'FAKE-PRIVATE-COOKIE'
        async with service([{'delay': 0.02, 'headers': {'Set-Cookie': secret}}, {}]) as (server, make_client):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=str(server.certificate))
            public_url = f'https://127.0.0.1:{server.port}/ordinary-public-request'
            with capture_http_logs() as records:
                request = asyncio.create_task(make_client().get_work_item(42))
                try:
                    async with asyncio.timeout(1):
                        while not server.seen:
                            await asyncio.sleep(0.001)
                    async with httpx.AsyncClient(verify=context, trust_env=False) as normal:
                        self.assertEqual((await normal.get(public_url)).status_code, 200)
                    self.assertEqual((await request).id, 42)
                finally:
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                await server.wait_idle()
            output = '\n'.join(logging.Formatter().format(record) for record in records)
            for private in (secret, 'FAKE-PAT-ONLY', 'OkZBS0UtUEFULU9OTFk=', '/Collection/', 'Demo%20Project', 'workItems/42'):
                self.assertNotIn(private, output)
            self.assertIn(public_url, output)
            hidden = [record for record in records if not record.args and record.name.startswith(('httpx', 'httpcore'))]
            self.assertTrue(any(record.name == 'httpx' and record.levelno == logging.INFO for record in hidden))
            self.assertTrue(any(record.name.startswith('httpcore.') and record.levelno == logging.DEBUG for record in hidden))
            self.assertTrue(any('receive_response_headers.complete' in record.getMessage() for record in records))

    async def test_future_child_loggers_scrub_exception_and_stack_without_touching_others(self):
        other = logging.getLogger('httpx_other')
        with capture_http_logs('httpx_other') as records:
            with private_http_logs():
                for name in ('httpx', 'httpx.future.child', 'httpcore', 'httpcore.future.child'):
                    logger = logging.getLogger(name)
                    try:
                        raise ValueError('FAKE-EXCEPTION-SECRET')
                    except ValueError:
                        logger.debug('FAKE-MESSAGE-SECRET %s', 'FAKE-ARG-SECRET', exc_info=True, stack_info=True)
                other.warning('ordinary-other %s', 'retained')
            logging.getLogger('httpx').info('ordinary-http %s', 'retained')
        self.assertEqual(len(records), 6)
        for record in records[:4]:
            output = logging.Formatter().format(record)
            self.assertNotIn('FAKE-', output)
            self.assertEqual(record.levelno, logging.DEBUG)
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
        self.assertEqual(records[-2].getMessage(), 'ordinary-other retained')
        self.assertEqual(records[-1].getMessage(), 'ordinary-http retained')

    async def test_nested_context_and_cancellation_restore_callers_logging(self):
        started = asyncio.Event()
        logger = logging.getLogger('httpx')

        async def worker():
            try:
                with private_http_logs():
                    with private_http_logs():
                        logger.info('FAKE-INNER-SECRET')
                    logger.info('FAKE-OUTER-SECRET')
                    started.set()
                    await asyncio.Event().wait()
            finally:
                logger.info('ordinary-after-cancel')

        with capture_http_logs() as records:
            request = asyncio.create_task(worker())
            await started.wait()
            logger.info('ordinary-concurrent')
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
        messages = [record.getMessage() for record in records]
        self.assertFalse(any('FAKE-' in message for message in messages))
        self.assertIn('ordinary-concurrent', messages)
        self.assertIn('ordinary-after-cancel', messages)

    async def test_ado_timeout_restores_logging_in_same_task(self):
        async with service([{'mode': 'slow_header'}]) as (server, make_client):
            with capture_http_logs() as records:
                with self.assertRaises(AdoReadError):
                    await make_client(limits=AdoLimits(deadline_seconds=0.1)).get_work_item(42)
                logging.getLogger('httpx').info('ordinary-after-timeout')
                await server.wait_idle()
            self.assertEqual(records[-1].getMessage(), 'ordinary-after-timeout')
            self.assertFalse(any('/Collection/' in record.getMessage() for record in records))

    async def test_import_chains_preexisting_factory_and_preserves_its_metadata(self):
        script = '''
import importlib
import logging
previous = logging.getLogRecordFactory()
records = []
def existing(*args, **kwargs):
    record = previous(*args, **kwargs)
    record.application_tag = "retained-tag"
    record.exc_text = "FAKE-CACHED-EXCEPTION"
    return record
logging.setLogRecordFactory(existing)
from core.private_http import private_http_logs
installed = logging.getLogRecordFactory()
importlib.import_module("core.private_http")
assert logging.getLogRecordFactory() is installed
class Capture(logging.Handler):
    def emit(self, record):
        records.append(record)
logger = logging.getLogger("httpcore.future.chain")
logger.setLevel(logging.DEBUG)
logger.addHandler(Capture())
with private_http_logs():
    logger.debug("FAKE-SECRET %s", "FAKE-ARG")
logger.debug("ordinary %s", "retained")
assert len(records) == 2
assert all(record.application_tag == "retained-tag" for record in records)
assert all(record.name == "httpcore.future.chain" for record in records)
assert all(record.levelno == logging.DEBUG for record in records)
assert records[0].exc_text is None
assert "FAKE-" not in records[0].getMessage()
assert records[1].getMessage() == "ordinary retained"
assert records[1].exc_text == "FAKE-CACHED-EXCEPTION"
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
