import copy
import io
import json
import time
import unittest

import requests

from core.ado_client import AdoApiProfile, AdoClient, AdoConnection, AdoLimits, AdoReadError, AdoReason
from tests.fixtures.ado_offline import COMMENT, ITEM, REVISION


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def reply(payload, status=200, content_type="application/json"):
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response.raw = io.BytesIO(json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload)
    return response


def client(responses, **options):
    session = FakeSession(responses)
    instance = AdoClient(
        AdoConnection("https://ado.example.test/Collection", "Demo Project"),
        AdoApiProfile("7.1", "7.1", "7.1-preview.4"),
        "FAKE-PAT-ONLY",
        session=session,
        **options,
    )
    return instance, session


class AdoClientTests(unittest.TestCase):
    def assert_reason(self, expected, operation):
        with self.assertRaises(AdoReadError) as caught:
            operation()
        self.assertEqual(caught.exception.reason, expected)
        return caught.exception

    def test_fixed_get_and_domain_result(self):
        instance, session = client([reply(ITEM)])
        item = instance.get_work_item(42)
        self.assertEqual((item.id, item.rev, item.relations), (42, 3, ()))
        self.assertEqual(item.fields["System.Title"], "假工單")
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://ado.example.test/Collection/Demo%20Project/_apis/wit/workItems/42")
        self.assertEqual(kwargs["params"], {"$expand": "relations", "api-version": "7.1"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["verify"])
        self.assertFalse(session.trust_env)
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")

    def test_relations_absence_is_unknown(self):
        item = copy.deepcopy(ITEM)
        del item["relations"]
        instance, _ = client([reply(item)])
        self.assertIsNone(instance.get_work_item(42).relations)

    def test_revisions_page_count_and_terminal_empty_page(self):
        row1 = copy.deepcopy(REVISION)
        row2 = dict(row1, rev=2)
        instance, session = client(
            [reply({"count": 1, "value": [row1]}), reply({"count": 1, "value": [row2]}), reply({"count": 0, "value": []})],
            limits=AdoLimits(page_size=1),
        )
        self.assertEqual([row.rev for row in instance.list_revisions(42)], [1, 2])
        self.assertEqual([call[1]["params"]["$skip"] for call in session.calls], [0, 1, 2])

    def test_comments_follow_token_only_and_accept_both_id_spellings(self):
        second = dict(COMMENT, id=8, commentId=8)
        instance, session = client([
            reply({"count": 1, "comments": [COMMENT], "continuationToken": "opaque-a", "nextPage": "https://evil.example/", "totalCount": 2}),
            reply({"count": 1, "comments": [second], "continuationToken": None, "totalCount": 2}),
        ], limits=AdoLimits(page_size=1))
        self.assertEqual([row.id for row in instance.list_comments(42)], [7, 8])
        self.assertTrue(all("evil.example" not in url for url, _ in session.calls))
        self.assertEqual(session.calls[1][1]["params"]["continuationToken"], "opaque-a")

    def test_bundle_uses_all_three_endpoints(self):
        instance, session = client([
            reply(ITEM),
            reply({"count": 1, "value": [REVISION]}),
            reply({"count": 1, "comments": [COMMENT], "continuationToken": None}),
        ])
        bundle = instance.read_item_bundle(42)
        self.assertEqual((bundle.item.id, bundle.revisions[0].rev, bundle.comments[0].id), (42, 1, 7))
        self.assertEqual(len(session.calls), 3)

    def test_http_failures_and_redirect_are_never_followed(self):
        for status, reason in [(401, AdoReason.AUTH_REQUIRED), (403, AdoReason.FORBIDDEN), (404, AdoReason.NOT_FOUND), (302, AdoReason.UNAVAILABLE), (503, AdoReason.UNAVAILABLE)]:
            with self.subTest(status=status):
                instance, session = client([reply({"secret": "raw-body"}, status)])
                error = self.assert_reason(reason, lambda: instance.get_work_item(42))
                self.assertEqual(error.status, status)
                self.assertEqual(len(session.calls), 1)

    def test_timeout_and_malformed_response(self):
        instance, _ = client([requests.ReadTimeout("FAKE-PAT-ONLY https://internal.example")])
        self.assert_reason(AdoReason.TIMEOUT, lambda: instance.get_work_item(42))
        for response in [reply(b"<html>login</html>", content_type="text/html"), reply(b"{"), reply({"value": []}), reply({"id": 99, "rev": 1, "fields": {}})]:
            instance, _ = client([response])
            self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: instance.get_work_item(42))

    def test_ambiguous_or_nonstandard_json_is_rejected(self):
        for payload in [
            b'{"id":42,"id":99,"rev":1,"fields":{}}',
            b'{"id":42,"rev":1,"fields":{"value":NaN}}',
            b'{"id":42,"rev":1,"fields":{"value":Infinity}}',
            b'{"id":42,"rev":1,"fields":{"value":-Infinity}}',
            b'{"id":42,"rev":1,"fields":{"state":"Done","state":"Active"}}',
        ]:
            with self.subTest(payload=payload):
                instance, session = client([reply(payload)])
                self.assert_reason(AdoReason.INVALID_RESPONSE, lambda: instance.get_work_item(42))
                self.assertEqual(len(session.calls), 1)

    def test_incomplete_pages_and_conflicting_comment_ids(self):
        scenarios = [
            ([reply({"count": 1, "value": [REVISION]})], "revisions", AdoLimits(page_size=1, max_pages=1), AdoReason.PARTIAL),
            ([reply({"count": 0, "comments": [], "continuationToken": "more"})], "comments", AdoLimits(), AdoReason.PARTIAL),
            ([reply({"count": 1, "comments": [COMMENT]})], "comments", AdoLimits(), AdoReason.PARTIAL),
            ([reply({"count": 1, "comments": [dict(COMMENT, id=8)], "continuationToken": None})], "comments", AdoLimits(), AdoReason.INVALID_RESPONSE),
        ]
        for responses, method, limits, expected in scenarios:
            with self.subTest(method=method, expected=expected):
                instance, _ = client(responses, limits=limits)
                self.assert_reason(expected, lambda: getattr(instance, "list_" + method)(42))

    def test_repeated_continuation_and_budget(self):
        pages = [reply({"count": 1, "comments": [COMMENT], "continuationToken": "same"}) for _ in range(2)]
        instance, _ = client(pages, limits=AdoLimits(page_size=1))
        self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_comments(42))
        instance, _ = client([reply(ITEM)], limits=AdoLimits(max_bytes=10))
        self.assert_reason(AdoReason.PARTIAL, lambda: instance.get_work_item(42))
        instance, _ = client([reply({"count": 1, "value": [REVISION]})], limits=AdoLimits(page_size=2, max_items=1))
        self.assertEqual(len(instance.list_revisions(42)), 1)
        instance, _ = client([reply({"count": 2, "value": [REVISION, dict(REVISION, rev=2)]})], limits=AdoLimits(page_size=2, max_items=1))
        self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_revisions(42))
        with self.assertRaises(ValueError):
            AdoLimits(max_items=0)

    def test_repeated_page_data_and_deadline(self):
        instance, _ = client([
            reply({"count": 1, "value": [REVISION]}),
            reply({"count": 1, "value": [REVISION]}),
        ], limits=AdoLimits(page_size=1))
        self.assert_reason(AdoReason.PARTIAL, lambda: instance.list_revisions(42))

        class SlowResponse:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def iter_content(self, chunk_size):
                time.sleep(0.02)
                yield json.dumps(ITEM).encode("utf-8")

            def close(self):
                pass

        instance, _ = client([SlowResponse()], limits=AdoLimits(deadline_seconds=0.001))
        self.assert_reason(AdoReason.TIMEOUT, lambda: instance.get_work_item(42))

    def test_secret_scrub_and_connection_validation(self):
        for url in ["http://ado.example.test/Collection", "https://u:p@ado.example.test/Collection", "https://ado.example.test/Collection?x=1", "https://ado.example.test/Collection#x", "https://ado.example.test/../Collection", "https://ado.example.test:8443/Collection"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                AdoConnection(url, "Demo")
        with self.assertRaises(ValueError):
            client([], verify=False)
        instance, _ = client([requests.exceptions.InvalidHeader("FAKE-PAT-ONLY https://internal.example raw-body")])
        error = self.assert_reason(AdoReason.UNAVAILABLE, lambda: instance.get_work_item(42))
        for exposed in (str(error), repr(error), error.detail, str(error.to_dict()), repr(instance)):
            self.assertNotIn("FAKE-PAT-ONLY", exposed)
            self.assertNotIn("internal.example", exposed)
            self.assertNotIn("raw-body", exposed)
            self.assertNotIn("ado.example.test", exposed)


if __name__ == "__main__":
    unittest.main()
