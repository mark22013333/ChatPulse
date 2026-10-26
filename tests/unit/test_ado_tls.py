import asyncio
import gc
import os
import time
import unittest
from unittest.mock import patch

from core.ado_client import AdoConnection, AdoLimits, AdoReadError, AdoReason
from tests.fixtures.ado_offline import COMMENT, ITEM, REVISION as REV
from tests.fixtures.ado_tls import service


class AdoTlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if gc.isenabled():
            gc.disable()
            self.addCleanup(gc.enable)

    async def assert_reason(self, reason, operation):
        with self.assertRaises(AdoReadError) as caught:
            await operation()
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception

    async def test_bundle_real_tls_follows_only_fixed_paths_and_tokens(self):
        steps = [
            {'payload': {key: value for key, value in ITEM.items() if key != 'relations'}},
            {'payload': {'count': 1, 'value': [REV]}},
            {'payload': {'count': 1, 'value': [dict(REV, rev=2)]}},
            {'payload': {'count': 0, 'value': []}},
            {'payload': {'count': 1, 'comments': [COMMENT], 'continuationToken': 'opaque /?=&token', 'nextPage': 'https://should-never-connect.invalid/', 'totalCount': 2}},
            {'payload': {'count': 1, 'comments': [dict(COMMENT, commentId=8)], 'continuationToken': None, 'totalCount': 2}},
        ]
        async with service(steps) as (server, make_client):
            result = await make_client(limits=AdoLimits(page_size=1)).read_item_bundle(42)
            self.assertEqual([r.rev for r in result.revisions], [1, 2])
            self.assertEqual([c.id for c in result.comments], [7, 8])
            self.assertIsNone(result.item.relations)
            self.assertEqual(len(server.seen), 6)
            self.assertTrue(all(e['auth_ok'] for e in server.seen))
            self.assertTrue(all(e['path'].startswith('/Collection/Demo%20Project/_apis/wit/workItems/42') for e in server.seen))
            self.assertEqual([e['query']['$skip'][0] for e in server.seen[1:4]], ['0', '1', '2'])
            self.assertEqual(server.seen[-1]['query']['continuationToken'], ['opaque /?=&token'])

    async def test_unknown_empty_and_present_relations_remain_distinct_and_unfollowed(self):
        relation = {'rel': 'Unknown.Future.Relation', 'url': 'https://should-never-connect.invalid/private', 'attributes': {'custom': 'retained'}}
        async with service([{'payload': {key: value for key, value in ITEM.items() if key != 'relations'}}, {'payload': dict(ITEM, relations=[])}, {'payload': dict(ITEM, relations=[relation])}]) as (server, make_client):
            client = make_client()
            self.assertIsNone((await client.get_work_item(42)).relations)
            self.assertEqual((await client.get_work_item(42)).relations, ())
            observed = (await client.get_work_item(42)).relations[0]
            self.assertEqual((observed.rel, observed.url, observed.attributes), (relation['rel'], relation['url'], relation['attributes']))
            self.assertEqual(len(server.seen), 3)

    async def test_malformed_json_duplicate_keys_nonstandard_number_and_shape(self):
        bodies = [b'{', b'{"id":42,"id":99,"rev":1,"fields":{}}', b'{"id":42,"rev":1,"fields":{"n":NaN}}', b'[]', b'\xff']
        async with service([{'raw': body} for body in bodies]) as (server, make_client):
            for _ in bodies:
                error = await self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: make_client().get_work_item(42))
                self.assertEqual(error.endpoint, 'work_item')
            self.assertEqual(len(server.seen), len(bodies))

    async def test_redirect_is_not_followed_even_when_same_local_tls_server(self):
        async with service([{'status': 302, 'location': '/not-allowed'}, {'payload': {key: value for key, value in ITEM.items() if key != 'relations'}}]) as (server, make_client):
            error = await self.assert_reason(AdoReason.UNAVAILABLE, lambda: make_client().get_work_item(42))
            self.assertEqual(error.status, 302)
            self.assertEqual(len(server.seen), 1)
            self.assertEqual(len(server.steps), 1)

    async def test_untrusted_tls_rejected_before_request(self):
        async with service([{'payload': {key: value for key, value in ITEM.items() if key != 'relations'}}]) as (server, make_client):
            await self.assert_reason(AdoReason.UNAVAILABLE, lambda: make_client(verify=True).get_work_item(42))
            self.assertEqual(server.seen, [])

    async def test_environment_ca_does_not_override_explicit_trust_policy(self):
        async with service([{'payload': ITEM}]) as (server, make_client):
            with patch.dict(os.environ, {'SSL_CERT_FILE': str(server.certificate), 'SSL_CERT_DIR': str(server.certificate.parent)}):
                await self.assert_reason(AdoReason.UNAVAILABLE, lambda: make_client(verify=True).get_work_item(42))
            self.assertEqual(server.seen, [])

    async def test_environment_keylog_is_not_created_for_default_or_explicit_ca(self):
        async with service([{'payload': ITEM}]) as (server, make_client):
            keylog = server.certificate.parent / 'fake-session-keys.log'
            with patch.dict(os.environ, {
                'SSLKEYLOGFILE': str(keylog),
                'SSL_CERT_FILE': str(server.certificate),
                'SSL_CERT_DIR': str(server.certificate.parent),
            }):
                await self.assert_reason(AdoReason.UNAVAILABLE, lambda: make_client(verify=True).get_work_item(42))
                self.assertEqual((await make_client().get_work_item(42)).id, 42)
            self.assertFalse(keylog.exists())
            self.assertEqual(len(server.seen), 1)

    async def test_partial_pages_cannot_return_as_complete(self):
        scenarios = [
            ([{'payload': {'count': 1, 'comments': [COMMENT]}}], 'list_comments', AdoLimits()),
            ([{'payload': {'count': 1, 'comments': [COMMENT], 'continuationToken': 'x'}}, {'payload': {'count': 1, 'comments': [dict(COMMENT, commentId=8)], 'continuationToken': 'x'}}], 'list_comments', AdoLimits(page_size=1)),
            ([{'payload': {'count': 1, 'value': [REV]}}, {'payload': {'count': 1, 'value': [REV]}}], 'list_revisions', AdoLimits(page_size=1)),
            ([{'payload': {'count': 1, 'value': [REV]}}], 'list_revisions', AdoLimits(page_size=1, max_pages=1)),
            ([{'payload': {key: value for key, value in ITEM.items() if key != 'relations'}}], 'get_work_item', AdoLimits(max_bytes=5)),
        ]
        for steps, operation, limits in scenarios:
            with self.subTest(operation=operation):
                async with service(steps) as (_, make_client):
                    await self.assert_reason(AdoReason.PARTIAL, lambda: getattr(make_client(limits=limits), operation)(42))

    async def test_http_failures_are_classified_and_do_not_expose_response(self):
        cases = [(401, AdoReason.AUTH_REQUIRED), (403, AdoReason.FORBIDDEN), (404, AdoReason.NOT_FOUND), (503, AdoReason.UNAVAILABLE)]
        async with service([{'status': status, 'raw': b'FAKE-PAT-ONLY https://secret.invalid raw-response'} for status, _ in cases]) as (_, make_client):
            for status, reason in cases:
                error = await self.assert_reason(reason, lambda: make_client().get_work_item(42))
                self.assertEqual(error.status, status)
                for output in (str(error), repr(error), error.detail, str(error.to_dict())):
                    self.assertNotIn('FAKE-PAT-ONLY', output)
                    self.assertNotIn('secret.invalid', output)
                    self.assertNotIn('raw-response', output)

    async def test_environment_proxy_is_not_used(self):
        async with service([{'payload': {key: value for key, value in ITEM.items() if key != 'relations'}}]) as (server, make_client):
            with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
                self.assertEqual((await make_client().get_work_item(42)).id, 42)
            self.assertEqual(len(server.seen), 1)

    async def test_invalid_inputs_do_not_issue_requests(self):
        async with service([]) as (server, make_client):
            for item_id in (True, 0, -1, '42'):
                with self.assertRaises(ValueError):
                    await make_client().get_work_item(item_id)
            with self.assertRaises(ValueError):
                make_client(verify=False)
            for collection in ('http://127.0.0.1/Collection', 'https://user:password@127.0.0.1/Collection', 'https://127.0.0.1/../Collection'):
                with self.assertRaises(ValueError):
                    AdoConnection(collection, 'Demo')
            self.assertEqual(server.seen, [])

    async def test_total_deadline_during_slow_continuous_body(self):
        body = b'{"id":42,"rev":1,"fields":{}}'
        async with service([{'raw': body, 'drip': 0.025}]) as (_, make_client):
            client = make_client(limits=AdoLimits(deadline_seconds=0.1, read_timeout=0.1))
            started = time.monotonic()
            await self.assert_reason(AdoReason.TIMEOUT, lambda: client.get_work_item(42))
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.4, '總 deadline 應在慢速持續傳輸時仍能中止讀取')

    async def test_idle_read_timeout_is_classified_as_timeout(self):
        body = b'{"id":42,"rev":1,"fields":{}}'
        async with service([{'raw': body, 'drip': 0.2}]) as (_, make_client):
            client = make_client(limits=AdoLimits(deadline_seconds=2, read_timeout=0.1))
            await self.assert_reason(AdoReason.TIMEOUT, lambda: client.get_work_item(42))

    async def test_compressed_response_is_explicitly_rejected(self):
        async with service([{'raw': b'not-an-identity-body', 'content_encoding': 'gzip'}]) as (_, make_client):
            await self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: make_client().get_work_item(42))

    async def assert_connections_closed(self, server, *, require_eof=False):
        await server.wait_idle()
        self.assertEqual(server.errors, [])
        self.assertTrue(server.events)
        for event in server.events:
            self.assertTrue(event.get('eof') or not require_eof and event.get('disconnect_type'), event)
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        self.assertEqual(pending, [])

    async def test_deadline_covers_headers_chunk_extensions_and_trailers(self):
        for mode in ('slow_header', 'slow_extension', 'slow_trailer'):
            with self.subTest(mode=mode):
                async with service([{'mode': mode}]) as (server, make_client):
                    client = make_client(limits=AdoLimits(deadline_seconds=0.1, read_timeout=0.1))
                    started = time.monotonic()
                    await self.assert_reason(AdoReason.TIMEOUT, lambda: client.get_work_item(42))
                    self.assertLess(time.monotonic() - started, 0.4)
                    self.assertEqual(len(server.seen), 1)
                    self.assertTrue(server.seen[0]['identity'])
                    await self.assert_connections_closed(server)
                    self.assertFalse(server.events[0].get('fully_sent', False))

    async def test_bundle_and_paging_share_one_deadline(self):
        cases = [
            ('read_item_bundle', [
                {'payload': ITEM},
                {'payload': {'count': 1, 'value': [REV]}},
                {'payload': {'count': 1, 'comments': [COMMENT], 'continuationToken': None}},
            ], AdoLimits(deadline_seconds=1.0)),
            ('list_revisions', [
                {'payload': {'count': 1, 'value': [REV]}},
                {'payload': {'count': 1, 'value': [dict(REV, rev=2)]}},
                {'payload': {'count': 0, 'value': []}},
            ], AdoLimits(deadline_seconds=1.0, page_size=1)),
            ('list_comments', [
                {'payload': {'count': 1, 'comments': [COMMENT], 'continuationToken': 'next'}},
                {'payload': {'count': 1, 'comments': [dict(COMMENT, commentId=8)], 'continuationToken': 'last'}},
                {'payload': {'count': 0, 'comments': [], 'continuationToken': None}},
            ], AdoLimits(deadline_seconds=1.0, page_size=1)),
        ]
        for method, steps, limits in cases:
            with self.subTest(method=method):
                async with service([dict(step, delay=0.3) for step in steps]) as (server, make_client):
                    client = make_client(limits=limits)
                    started = time.monotonic()
                    await self.assert_reason(AdoReason.TIMEOUT, lambda: getattr(client, method)(42))
                    self.assertLess(time.monotonic() - started, 1.4)
                    self.assertEqual(len(server.seen), 2)
                    await self.assert_connections_closed(server)

    async def test_tls_handshake_timeout_closes_stream_before_gc(self):
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            async with service([]) as (server, make_client):
                client = make_client(stalled_tls=True, limits=AdoLimits(deadline_seconds=0.1, connect_timeout=1))
                started = time.monotonic()
                await self.assert_reason(AdoReason.TIMEOUT, lambda: client.get_work_item(42))
                self.assertLess(time.monotonic() - started, 0.4)
                await self.assert_connections_closed(server, require_eof=True)
                self.assertGreater(server.events[0]['client_hello_bytes'], 0)
                self.assertTrue(server.events[0]['eof'])
        finally:
            if gc_was_enabled:
                gc.enable()

    async def test_external_cancellation_preserves_cancelled_error_and_closes_before_gc(self):
        for mode in ('tls_handshake', 'slow_header', 'slow_extension', 'slow_trailer', 'slow_body'):
            with self.subTest(mode=mode):
                gc_was_enabled = gc.isenabled()
                gc.disable()
                try:
                    steps = [] if mode == 'tls_handshake' else [{'mode': mode, 'drip': 0.025}]
                    async with service(steps) as (server, make_client):
                        client = make_client(stalled_tls=mode == 'tls_handshake', limits=AdoLimits(deadline_seconds=5))
                        request = asyncio.create_task(client.get_work_item(42))
                        try:
                            async with asyncio.timeout(1):
                                while not server.events or not server.events[0].get('client_hello_bytes' if mode == 'tls_handshake' else 'sent_bytes'):
                                    await asyncio.sleep(0.005)
                            request.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await request
                            await self.assert_connections_closed(server, require_eof=mode == 'tls_handshake')
                        finally:
                            request.cancel()
                            await asyncio.gather(request, return_exceptions=True)
                finally:
                    if gc_was_enabled:
                        gc.enable()

    async def test_chunked_success_and_truncated_responses(self):
        async with service([{'mode': 'chunked'}, {'mode': 'truncated_chunk'}, {'mode': 'truncated_length'}]) as (_, make_client):
            client = make_client()
            self.assertEqual((await client.get_work_item(42)).id, 42)
            for _ in range(2):
                await self.assert_reason(AdoReason.UNAVAILABLE, lambda: client.get_work_item(42))


if __name__ == '__main__':
    unittest.main()
