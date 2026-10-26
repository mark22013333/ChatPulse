import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from core.ado_client import AdoApiProfile, AdoClient, AdoConnection, AdoLimits, AdoReadError, AdoReason
from tests.fixtures.ado_offline import COMMENT, ITEM, REVISION


def reply(payload, status=200, content_type="application/json"):
    body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    return httpx.Response(status, headers={"Content-Type": content_type}, stream=httpx.ByteStream(body))


def client(responses, **options):
    responses = iter(responses)
    calls = []

    def handle(request):
        calls.append(request)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    instance = AdoClient(
        AdoConnection("https://ado.example.test/Collection", "Demo Project"),
        AdoApiProfile("7.1", "7.1", "7.1-preview.4"),
        "FAKE-PAT-ONLY",
        transport=httpx.MockTransport(handle),
        **options,
    )
    return instance, calls


class AdoClientTests(unittest.IsolatedAsyncioTestCase):
    async def assert_reason(self, expected, operation):
        with self.assertRaises(AdoReadError) as caught:
            await operation()
        self.assertEqual(caught.exception.reason, expected)
        return caught.exception

    async def test_existing_invalid_ca_is_domain_failure_without_a_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'FAKE-CA-PATH.pem'
            path.write_text('FAKE-INVALID-CA-CONTENT')
            instance, calls = client([], verify=str(path))
            error = await self.assert_reason(AdoReason.UNAVAILABLE, lambda: instance.get_work_item(42))
            self.assertEqual(calls, [])
            for output in (str(error), repr(error), error.detail, str(error.to_dict())):
                for private in (str(path), 'FAKE-INVALID-CA-CONTENT', 'FAKE-PAT-ONLY'):
                    self.assertNotIn(private, output)

    async def test_fixed_get_and_domain_result(self):
        instance, calls = client([reply(ITEM)])
        item = await instance.get_work_item(42)
        self.assertEqual((item.id, item.rev, item.relations), (42, 3, ()))
        self.assertEqual(item.fields["System.Title"], "假工單")
        request = calls[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?", 1)[0], "https://ado.example.test/Collection/Demo%20Project/_apis/wit/workItems/42")
        self.assertEqual(dict(request.url.params), {"$expand": "relations", "api-version": "7.1"})
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(request.headers["Accept-Encoding"], "identity")

    async def test_relations_absence_is_unknown(self):
        item = copy.deepcopy(ITEM)
        del item["relations"]
        instance, _ = client([reply(item)])
        self.assertIsNone((await instance.get_work_item(42)).relations)

    async def test_revisions_page_count_and_terminal_empty_page(self):
        row1 = copy.deepcopy(REVISION)
        row2 = dict(row1, rev=2)
        instance, calls = client(
            [reply({"count": 1, "value": [row1]}), reply({"count": 1, "value": [row2]}), reply({"count": 0, "value": []})],
            limits=AdoLimits(page_size=1),
        )
        self.assertEqual([row.rev for row in await instance.list_revisions(42)], [1, 2])
        self.assertEqual([call.url.params["$skip"] for call in calls], ["0", "1", "2"])

    async def test_comments_follow_token_only_and_accept_both_id_spellings(self):
        second = dict(COMMENT, id=8, commentId=8)
        instance, calls = client([
            reply({"count": 1, "comments": [COMMENT], "continuationToken": "opaque-a", "nextPage": "https://evil.example/", "totalCount": 2}),
            reply({"count": 1, "comments": [second], "continuationToken": None, "totalCount": 2}),
        ], limits=AdoLimits(page_size=1))
        self.assertEqual([row.id for row in await instance.list_comments(42)], [7, 8])
        self.assertTrue(all("evil.example" not in str(call.url) for call in calls))
        self.assertEqual(calls[1].url.params["continuationToken"], "opaque-a")

    async def test_bundle_uses_all_three_endpoints(self):
        instance, calls = client([
            reply(ITEM),
            reply({"count": 1, "value": [REVISION]}),
            reply({"count": 1, "comments": [COMMENT], "continuationToken": None}),
        ])
        bundle = await instance.read_item_bundle(42)
        self.assertEqual((bundle.item.id, bundle.revisions[0].rev, bundle.comments[0].id), (42, 1, 7))
        self.assertEqual(len(calls), 3)

    async def test_http_failures_and_redirect_are_never_followed(self):
        for status, reason in [(401, AdoReason.AUTH_REQUIRED), (403, AdoReason.FORBIDDEN), (404, AdoReason.NOT_FOUND), (302, AdoReason.UNAVAILABLE), (503, AdoReason.UNAVAILABLE)]:
            with self.subTest(status=status):
                instance, calls = client([reply({"secret": "raw-body"}, status)])
                error = await self.assert_reason(reason, lambda: instance.get_work_item(42))
                self.assertEqual(error.status, status)
                self.assertEqual(len(calls), 1)

    async def test_timeout_and_malformed_response(self):
        instance, _ = client([httpx.ReadTimeout("FAKE-PAT-ONLY https://internal.example")])
        await self.assert_reason(AdoReason.TIMEOUT, lambda: instance.get_work_item(42))
        for response in [reply(b"<html>login</html>", content_type="text/html"), reply(b"{"), reply({"value": []}), reply({"id": 99, "rev": 1, "fields": {}})]:
            instance, _ = client([response])
            await self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: instance.get_work_item(42))

    async def test_ambiguous_or_nonstandard_json_is_rejected(self):
        for payload in [
            b'{"id":42,"id":99,"rev":1,"fields":{}}',
            b'{"id":42,"rev":1,"fields":{"value":NaN}}',
            b'{"id":42,"rev":1,"fields":{"value":Infinity}}',
            b'{"id":42,"rev":1,"fields":{"value":-Infinity}}',
            b'{"id":42,"rev":1,"fields":{"state":"Done","state":"Active"}}',
        ]:
            with self.subTest(payload=payload):
                instance, calls = client([reply(payload)])
                await self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: instance.get_work_item(42))
                self.assertEqual(len(calls), 1)

    async def test_incomplete_pages_and_conflicting_comment_ids(self):
        scenarios = [
            ([reply({"count": 1, "value": [REVISION]})], "revisions", AdoLimits(page_size=1, max_pages=1), AdoReason.PARTIAL),
            ([reply({"count": 0, "comments": [], "continuationToken": "more"})], "comments", AdoLimits(), AdoReason.PARTIAL),
            ([reply({"count": 1, "comments": [COMMENT]})], "comments", AdoLimits(), AdoReason.PARTIAL),
            ([reply({"count": 1, "comments": [dict(COMMENT, id=8)], "continuationToken": None})], "comments", AdoLimits(), AdoReason.INVALID_RESPONSE),
        ]
        for responses, method, limits, expected in scenarios:
            with self.subTest(method=method, expected=expected):
                instance, _ = client(responses, limits=limits)
                await self.assert_reason(expected, lambda: getattr(instance, "list_" + method)(42))

    async def test_repeated_continuation_and_budget(self):
        pages = [reply({"count": 1, "comments": [COMMENT], "continuationToken": "same"}) for _ in range(2)]
        instance, _ = client(pages, limits=AdoLimits(page_size=1))
        await self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_comments(42))
        instance, _ = client([reply(ITEM)], limits=AdoLimits(max_bytes=10))
        await self.assert_reason(AdoReason.PARTIAL, lambda: instance.get_work_item(42))
        instance, _ = client([reply({"count": 1, "value": [REVISION]})], limits=AdoLimits(page_size=2, max_items=1))
        self.assertEqual(len(await instance.list_revisions(42)), 1)
        instance, _ = client([reply({"count": 2, "value": [REVISION, dict(REVISION, rev=2)]})], limits=AdoLimits(page_size=2, max_items=1))
        await self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_revisions(42))
        with self.assertRaises(ValueError):
            AdoLimits(max_items=0)

    async def test_repeated_page_data_and_deadline(self):
        instance, _ = client([
            reply({"count": 1, "value": [REVISION]}),
            reply({"count": 1, "value": [REVISION]}),
        ], limits=AdoLimits(page_size=1))
        await self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_revisions(42))

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(0.02)
                yield json.dumps(ITEM).encode("utf-8")

        slow = httpx.Response(200, headers={"Content-Type": "application/json"}, stream=SlowBody())

        instance, _ = client([slow], limits=AdoLimits(deadline_seconds=0.001))
        await self.assert_reason(AdoReason.TIMEOUT, lambda: instance.get_work_item(42))

    async def test_secret_scrub_and_connection_validation(self):
        for url in ["http://ado.example.test/Collection", "https://u:p@ado.example.test/Collection", "https://ado.example.test/Collection?x=1", "https://ado.example.test/Collection#x", "https://ado.example.test/../Collection", "https://ado.example.test:8443/Collection"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                AdoConnection(url, "Demo")
        with self.assertRaises(ValueError):
            client([], verify=False)
        instance, _ = client([httpx.LocalProtocolError("FAKE-PAT-ONLY https://internal.example raw-body")])
        error = await self.assert_reason(AdoReason.UNAVAILABLE, lambda: instance.get_work_item(42))
        for exposed in (str(error), repr(error), error.detail, str(error.to_dict()), repr(instance)):
            self.assertNotIn("FAKE-PAT-ONLY", exposed)
            self.assertNotIn("internal.example", exposed)
            self.assertNotIn("raw-body", exposed)
            self.assertNotIn("ado.example.test", exposed)

    async def test_bundle_shares_byte_budget_and_rejects_before_return(self):
        payloads = [ITEM, {"count": 1, "value": [REVISION]}, {"count": 0, "comments": [], "continuationToken": None}]
        largest_body = max(len(json.dumps(payload).encode("utf-8")) for payload in payloads)
        instance, calls = client([reply(payload) for payload in payloads], limits=AdoLimits(max_bytes=largest_body))
        await self.assert_reason(AdoReason.PARTIAL, lambda: instance.read_item_bundle(42))
        self.assertEqual(len(calls), 2)

    async def test_synchronous_json_parse_cannot_return_expired_result(self):
        original_loads = json.loads

        def slow_loads(*args, **kwargs):
            time.sleep(0.03)
            return original_loads(*args, **kwargs)

        instance, _ = client([reply(ITEM)], limits=AdoLimits(deadline_seconds=0.01))
        with patch("core.ado_client.json.loads", slow_loads):
            await self.assert_reason(AdoReason.TIMEOUT, lambda: instance.get_work_item(42))

    async def test_unknown_transport_cleanup_failure_is_not_success(self):
        class BrokenCleanup(httpx.MockTransport):
            async def aclose(self):
                raise RuntimeError("假清理失敗")

        instance = AdoClient(
            AdoConnection("https://ado.example.test/Collection", "Demo"),
            AdoApiProfile("7.1", "7.1", "7.1"),
            "FAKE-PAT-ONLY",
            transport=BrokenCleanup(lambda request: reply(ITEM)),
        )
        with self.assertRaises(RuntimeError):
            await instance.get_work_item(42)


if __name__ == "__main__":
    unittest.main()
