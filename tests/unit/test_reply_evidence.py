import json
import unittest

from core.reply_evidence import (Evidence, EvidenceError, eligible, parse_assessment,
                                 parse_queries, strict_json, text_hash)


class ReplyEvidenceTest(unittest.TestCase):
    def test_json_rejects_duplicate_nan_oversized_and_nonobject(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '[]', 'x' * 100001):
            with self.subTest(raw=raw[:40]), self.assertRaises(EvidenceError):
                strict_json(raw)

    def test_exact_utf8_hash_does_not_normalize(self):
        self.assertNotEqual(text_hash("測試"), text_hash(" 測試"))
        self.assertNotEqual(text_hash("甲\n乙"), text_hash("甲\r\n乙"))
        self.assertEqual(text_hash("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_no_unregistered_or_executable_queries(self):
        source = {"g": {"kind": "git", "use": "reply_context", "paths": ["a.py"]}}
        good = {"source_id": "g", "operation": "git_search", "terms": ["timeout"], "paths": ["a.py"]}
        self.assertEqual(parse_queries({"queries": [good]}, source)[0].source_id, "g")
        for changes in ({"source_id": "other"}, {"operation": "shell"}, {"paths": ["../secret"]},
                        {"paths": ["/tmp/token"]}, {"terms": ["https://example.invalid"]},
                        {"terms": ["$(cat token)"]}, {"viewer_id": 2}, {"paths": ["b.py"]}):
            with self.subTest(changes=changes), self.assertRaises(EvidenceError):
                parse_queries({"queries": [{**good, **changes}]}, source)
        with self.assertRaises(EvidenceError):
            parse_queries({"queries": [good]}, {"g": {**source["g"], "use": "metadata_only"}})

    def test_complete_spans_real_references_and_environment(self):
        e = Evidence("e1", "g", "reply_context", "main.py:1-2", "a" * 40, "fake", "uat", None, "code_snapshot")
        raw = {"claims": [{"id": "c1", "text": "已完成", "kind": "status", "relation": "supported",
                "reference_validity": "valid", "currency": "current", "scope": "match", "reason": "假評估",
                "evidence_ids": ["e1"]}], "segments": [{"start": 0, "end": 3, "kind": "claim", "claim_id": "c1"}], "gaps": []}
        assessment = parse_assessment(raw, "已完成", {"e1": e}, "uat")
        self.assertTrue(eligible(assessment))
        self.assertFalse(eligible(parse_assessment(raw, "已完成", {"e1": e}, "production")))
        self.assertFalse(eligible(parse_assessment(raw, "已完成", {"e1": e}, None)))
        for changes in ({"segments": [{"start": 1, "end": 3, "kind": "claim", "claim_id": "c1"}]},
                        {"segments": [{"start": 0, "end": 4, "kind": "claim", "claim_id": "c1"}]},
                        {"segments": [{"start": 0, "end": 3, "kind": "non_fact", "claim_id": None}]}):
            with self.assertRaises(EvidenceError):
                parse_assessment({**raw, **changes}, "已完成", {"e1": e}, "uat")
        with self.assertRaises(EvidenceError):
            parse_assessment(raw, "已完成", {}, "uat")
