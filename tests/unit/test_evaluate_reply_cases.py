"""透過離線 CLI 驗證候選判斷與輸入失敗，不依賴模型或憑證。"""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/evaluate_reply_cases.py'
CORPUS = ROOT / 'tests/fixtures/reply_cases.jsonl'
GOOD = ROOT / 'tests/fixtures/reply_candidates.good.jsonl'


class EvaluateReplyCasesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.candidate = [json.loads(line) for line in GOOD.read_text().splitlines()]

    def invoke(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                cwd=self.directory, text=True, capture_output=True)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def score(self, rows=None):
        path = self.directory / 'candidate.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in
                                (self.candidate if rows is None else rows)))
        return self.invoke('score', '--candidate', path)

    def claim(self, case_id):
        return next(row for row in self.candidate if row['case_id'] == case_id)['claims'][0]

    def report_claim(self, report, case_id):
        return next(row for row in report['cases'] if row['case_id'] == case_id)['claims'][0]

    def test_default_is_lint_without_score(self):
        code, report = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report['mode'], 'lint')
        self.assertEqual(report['case_count'], 10)
        self.assertNotIn('scores', report)
        self.assertNotIn('cases', report)

    def test_handwritten_independent_candidate_passes(self):
        code, report = self.score()
        self.assertEqual(code, 0)
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['cases']), 10)
        for row in report['cases']:
            self.assertEqual(row['claims'][0]['issues'], [])

    def test_wrong_deployment_is_unsafe_despite_valid_reference(self):
        claim = self.claim('done_not_deployed')
        claim.update(decision='assert', relation='supported', currency='current', scope='match')
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'done_not_deployed')
        self.assertIn('unsafe_assertion', scored['issues'])
        self.assertFalse(scored['scores']['relation'])
        self.assertFalse(scored['scores']['currency'])
        self.assertFalse(scored['scores']['scope'])
        self.assertTrue(scored['scores']['reference_validity'])

    def test_guarantee_strength_is_a_separate_unsafe_claim(self):
        self.claim('planned_not_guaranteed')['decision'] = 'assert'
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'planned_not_guaranteed')
        self.assertIn('unsafe_assertion', scored['issues'])
        self.assertTrue(scored['scores']['relation'])
        self.assertTrue(scored['scores']['reference_validity'])

    def test_omitting_answerable_control_is_over_refusal(self):
        claim = self.claim('production_timeout')
        claim.update(decision='omit', references=[])
        code, report = self.score()
        self.assertEqual(code, 1)
        self.assertIn('over_refusal', self.report_claim(report, 'production_timeout')['issues'])

    def test_wrong_location_fails_without_changing_semantic_scores(self):
        self.claim('merged_and_deployed')['references'][0]['lines'] = [3, 4]
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'merged_and_deployed')
        self.assertFalse(scored['scores']['reference_validity'])
        self.assertTrue(scored['scores']['relation'])
        self.assertNotIn('unsafe_assertion', scored['issues'])

    def test_rollback_currency_is_scored_separately(self):
        self.claim('deployment_rolled_back')['currency'] = 'current'
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'deployment_rolled_back')
        self.assertFalse(scored['scores']['currency'])
        self.assertTrue(scored['scores']['scope'])

    def test_omit_still_requires_a_complete_judgment(self):
        claim = self.claim('done_not_deployed')
        claim.update(decision='omit', references=[])
        self.assertEqual(self.score()[0], 0)
        del claim['relation']
        self.assertEqual(self.score()[0], 2)

    def test_invalid_candidate_is_rejected_instead_of_scored(self):
        original = copy.deepcopy(self.candidate)
        mutations = {
            'missing_case': lambda rows: rows.pop(),
            'duplicate_case': lambda rows: rows.append(copy.deepcopy(rows[0])),
            'unknown_case': lambda rows: rows[0].update(case_id='unknown'),
            'missing_claim': lambda rows: rows[0].update(claims=[]),
            'duplicate_claim': lambda rows: rows[0]['claims'].append(copy.deepcopy(rows[0]['claims'][0])),
            'unknown_claim': lambda rows: rows[0]['claims'][0].update(claim_id='unknown'),
            'illegal_decision': lambda rows: rows[0]['claims'][0].update(decision='maybe'),
            'illegal_relation': lambda rows: rows[0]['claims'][0].update(relation='probably'),
            'illegal_currency': lambda rows: rows[0]['claims'][0].update(currency='recent'),
            'illegal_scope': lambda rows: rows[0]['claims'][0].update(scope='same'),
            'gold_leak': lambda rows: rows[0].update(gold={}),
            'evidence_copy': lambda rows: rows[0]['claims'][0].update(text='工單狀態為 Done。'),
            'unknown_evidence': lambda rows: rows[0]['claims'][0]['references'][0].update(evidence_id='unknown'),
            'boolean_line': lambda rows: rows[0]['claims'][0]['references'][0].update(lines=[True, 2]),
            'reverse_line': lambda rows: rows[0]['claims'][0]['references'][0].update(lines=[2, 1]),
            'duplicate_reference': lambda rows: rows[0]['claims'][0]['references'].append(copy.deepcopy(rows[0]['claims'][0]['references'][0])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                rows = copy.deepcopy(original)
                mutate(rows)
                code, report = self.score(rows)
                self.assertEqual(code, 2)
                self.assertEqual(report['error']['code'], 'invalid_input')
                self.assertNotIn('cases', report)

    def test_score_requires_candidate_and_forbids_same_file(self):
        self.assertEqual(self.invoke('score')[0], 2)
        self.assertEqual(self.invoke('score', '--candidate', CORPUS)[0], 2)

    def test_malformed_json_duplicate_keys_and_nonstandard_numbers_are_rejected(self):
        path = self.directory / 'bad.jsonl'
        for raw in ('{', '{"case_id":"x","case_id":"y"}', '{"case_id":NaN}', '[]', ''):
            with self.subTest(raw=raw):
                path.write_text(raw)
                self.assertEqual(self.invoke('score', '--candidate', path)[0], 2)

    def test_corpus_schema_is_validated_before_scoring(self):
        corpus = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        mutations = [lambda rows: rows[0]['gold']['claims'][0].update(required='yes'),
                     lambda rows: rows[0]['gold']['claims'][0].update(relation='probably'),
                     lambda rows: rows[0]['evidence'][0].update(lines=[1, 3]),
                     lambda rows: rows[0]['scope'].update(as_of='yesterday'),
                     lambda rows: rows[0]['gold']['claims'][0].update(claim_id='unknown')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                rows = copy.deepcopy(corpus)
                mutate(rows)
                path = self.directory / 'corpus.jsonl'
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                self.assertEqual(self.invoke('lint', '--corpus', path)[0], 2)

    def test_empty_references_cannot_support_an_assertion(self):
        self.claim('done_and_verified')['references'] = []
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'done_and_verified')
        self.assertEqual(set(scored['issues']), {'invalid_reference', 'unsupported_citation'})

    def test_complete_cases_still_require_every_claim(self):
        corpus = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        corpus[0]['claims'].append({'id': 'c2', 'text': '正式環境已完成驗證。'})
        corpus[0]['gold']['claims'].append({
            'claim_id': 'c2', 'relation': 'insufficient', 'currency': 'unknown',
            'scope': 'mismatch', 'allowed_decisions': ['qualify', 'omit'], 'required': False,
            'citation_spans': [{'evidence_id': 'e1', 'lines': [2, 2]}]})
        corpus_path = self.directory / 'two-claims.jsonl'
        corpus_path.write_text(''.join(json.dumps(row) + '\n' for row in corpus))
        candidate_path = self.directory / 'candidate.jsonl'
        candidate_path.write_text(''.join(json.dumps(row) + '\n' for row in self.candidate))
        self.assertEqual(self.invoke('score', '--corpus', corpus_path,
                                     '--candidate', candidate_path)[0], 2)
        self.candidate[0]['claims'].append({
            'claim_id': 'c2', 'decision': 'omit', 'relation': 'insufficient',
            'currency': 'unknown', 'scope': 'mismatch', 'references': []})
        candidate_path.write_text(''.join(json.dumps(row) + '\n' for row in self.candidate))
        self.assertEqual(self.invoke('score', '--corpus', corpus_path,
                                     '--candidate', candidate_path)[0], 0)

    def test_valid_location_without_judgment_evidence_fails_support(self):
        self.claim('done_and_verified')['references'][0]['lines'] = [1, 1]
        code, report = self.score()
        self.assertEqual(code, 1)
        scored = self.report_claim(report, 'done_and_verified')
        self.assertTrue(scored['scores']['reference_validity'])
        self.assertTrue(scored['scores']['relation'])
        self.assertFalse(scored['scores']['citation_support'])
        self.assertEqual(scored['issues'], ['unsupported_citation'])

    def test_all_necessary_spans_and_each_extra_reference_are_checked(self):
        claim = self.claim('production_timeout')
        claim['references'][0]['lines'] = [2, 2]
        code, report = self.score()
        self.assertEqual(code, 1)
        self.assertFalse(self.report_claim(report, 'production_timeout')['scores']['citation_support'])
        claim = self.claim('done_and_verified')
        claim['references'] = [{'evidence_id': 'e1', 'lines': [2, 2]},
                               {'evidence_id': 'e1', 'lines': [1, 1]}]
        code, report = self.score()
        self.assertEqual(code, 1)
        self.assertFalse(self.report_claim(report, 'done_and_verified')['scores']['citation_support'])

    def test_invalid_citation_span_annotations_are_rejected(self):
        corpus = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        for span in ({'evidence_id': 'unknown', 'lines': [1, 1]},
                     {'evidence_id': 'e1', 'lines': [1, 3]}):
            with self.subTest(span=span):
                rows = copy.deepcopy(corpus)
                rows[0]['gold']['claims'][0]['citation_spans'] = [span]
                path = self.directory / 'corpus.jsonl'
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                self.assertEqual(self.invoke('lint', '--corpus', path)[0], 2)

    def test_invalid_input_report_can_be_saved_without_overwriting_inputs(self):
        malformed = self.directory / 'malformed.jsonl'
        malformed.write_text('{')
        output = self.directory / 'error.json'
        code, report = self.invoke('score', '--candidate', malformed, '--output', output)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.read_text()), report)
        self.assertEqual(self.invoke('lint', '--output', CORPUS)[0], 2)
        self.assertEqual(self.invoke('lint')[0], 0)

    def test_output_file_matches_stdout(self):
        output = self.directory / 'report.json'
        code, report = self.invoke('lint', '--output', output)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.read_text()), report)


if __name__ == '__main__':
    unittest.main()
