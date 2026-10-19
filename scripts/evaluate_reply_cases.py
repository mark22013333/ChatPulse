#!/usr/bin/env python3
"""離線比較結構化 judgment；不從自由文字推論，也不產生 candidate。"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / 'tests/fixtures/reply_cases.jsonl'
RELATIONS = {'supported', 'contradicted', 'insufficient'}
CURRENCIES = {'current', 'superseded', 'unknown'}
SCOPES = {'match', 'mismatch', 'unknown'}
DECISIONS = {'assert', 'qualify', 'omit'}
ENVIRONMENTS = {'production', 'uat', 'dev'}


class InvalidInput(ValueError):
    """輸入無法比較時回 2，避免把未評估誤算為通過或判斷錯誤。"""


def _object(value, keys, location):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InvalidInput(f'{location} 的欄位不符合 schema')
    return value


def _list(value, location, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise InvalidInput(f'{location} 必須是陣列' + ('且不可為空' if nonempty else ''))
    return value


def _string(value, location):
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput(f'{location} 必須是非空字串')
    return value


def _identifier(value, location):
    _string(value, location)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise InvalidInput(f'{location} 不是有效識別字')
    return value


def _label(value, labels, location):
    if not isinstance(value, str) or value not in labels:
        raise InvalidInput(f'{location} 不在允許值內')


def _timestamp(value, location):
    _string(value, location)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise InvalidInput(f'{location} 必須是含時區的 ISO 8601 時間') from exc


def _lines(value, location):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(number) is not int or number < 1 for number in value)
            or value[0] > value[1]):
        raise InvalidInput(f'{location} 必須是遞增的正整數起訖行號')


def _unique(rows, field, location):
    result = {}
    for row in rows:
        key = _identifier(row[field], location)
        if key in result:
            raise InvalidInput(f'{location} 有重複 ID {key}')
        result[key] = row
    return result


def _same_ids(actual, expected, location):
    if set(actual) != set(expected):
        raise InvalidInput(f'{location} 的 ID 有未知或缺漏項目')


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput(f'JSON 欄位重複 {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise InvalidInput(f'JSON 不允許 {value}')


def _read_jsonl(path):
    rows = []
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise InvalidInput(f'第 {number} 列不可空白')
                try:
                    rows.append(json.loads(line, object_pairs_hook=_json_object,
                                           parse_constant=_invalid_constant))
                except (ValueError, RecursionError) as exc:
                    raise InvalidInput(f'第 {number} 列不是有效 JSON 物件') from exc
    except (OSError, UnicodeError) as exc:
        raise InvalidInput('無法讀取 UTF-8 JSONL 檔案') from exc
    return _list(rows, 'JSONL')


def load_corpus(path):
    """在 corpus 邊界一次驗證形狀、gold 完整度與定位資料。"""
    rows = _read_jsonl(path)
    for case in rows:
        _object(case, {'case_id', 'question', 'scope', 'evidence', 'claims', 'gold'}, 'case')
        _string(case['question'], 'question')
        scope = _object(case['scope'], {'project', 'environment', 'as_of'}, 'scope')
        _string(scope['project'], 'scope.project')
        _label(scope['environment'], ENVIRONMENTS, 'scope.environment')
        _timestamp(scope['as_of'], 'scope.as_of')
        for evidence in _list(case['evidence'], 'evidence'):
            _object(evidence, {'id', 'locator', 'lines', 'text', 'project',
                               'environment', 'event_at'}, 'evidence')
            for field in ('locator', 'text', 'project'):
                _string(evidence[field], f'evidence.{field}')
            _label(evidence['environment'], ENVIRONMENTS, 'evidence.environment')
            _timestamp(evidence['event_at'], 'evidence.event_at')
            _lines(evidence['lines'], 'evidence.lines')
            start, end = evidence['lines']
            if end - start + 1 != len(evidence['text'].splitlines()):
                raise InvalidInput('evidence 的行號範圍與原文行數不符')
        _unique(case['evidence'], 'id', 'evidence')
        for claim in _list(case['claims'], 'claims'):
            _object(claim, {'id', 'text'}, 'claim')
            _string(claim['text'], 'claim.text')
        claims = _unique(case['claims'], 'id', 'claim')
        gold = _object(case['gold'], {'claims'}, 'gold')
        for judgment in _list(gold['claims'], 'gold.claims'):
            _object(judgment, {'claim_id', 'relation', 'currency', 'scope',
                              'allowed_decisions', 'required', 'citation_spans'}, 'gold.claim')
            evidence_by_id = {item['id']: item for item in case['evidence']}
            seen_spans = set()
            for span in _list(judgment['citation_spans'], 'citation_spans'):
                _object(span, {'evidence_id', 'lines'}, 'citation_span')
                _identifier(span['evidence_id'], 'citation_span.evidence_id')
                _lines(span['lines'], 'citation_span.lines')
                if span['evidence_id'] not in evidence_by_id:
                    raise InvalidInput('citation_span 有未知 evidence_id')
                start, end = evidence_by_id[span['evidence_id']]['lines']
                low, high = span['lines']
                key = (span['evidence_id'], low, high)
                if not start <= low <= high <= end or key in seen_spans:
                    raise InvalidInput('citation_span 超出證據範圍或重複')
                seen_spans.add(key)
            _validate_dimensions(judgment)
            allowed = _list(judgment['allowed_decisions'], 'allowed_decisions')
            for decision in allowed:
                _label(decision, DECISIONS, 'allowed_decisions')
            if len(set(allowed)) != len(allowed) or type(judgment['required']) is not bool:
                raise InvalidInput('gold 的 allowed_decisions 或 required 不合法')
            if judgment['required'] and ('omit' in allowed or 'assert' not in allowed):
                raise InvalidInput('必答的 gold 必須允許 assert 且不得允許 omit')
            if 'assert' in allowed and not _safe_to_assert(judgment):
                raise InvalidInput('gold 不得允許不安全的 assert')
        _same_ids(_unique(gold['claims'], 'claim_id', 'gold.claim'), claims, 'gold.claim')
    return _unique(rows, 'case_id', 'case')


def _validate_dimensions(judgment):
    _label(judgment['relation'], RELATIONS, 'relation')
    _label(judgment['currency'], CURRENCIES, 'currency')
    _label(judgment['scope'], SCOPES, 'scope')


def _safe_to_assert(judgment):
    return (judgment['relation'] == 'supported' and judgment['currency'] == 'current'
            and judgment['scope'] == 'match')


def load_candidate(path, corpus):
    """只接受完整判斷與定位 ID，拒絕 gold、原文副本及未宣告欄位。"""
    rows = _read_jsonl(path)
    for row in rows:
        _object(row, {'case_id', 'claims'}, 'candidate.case')
        _identifier(row['case_id'], 'candidate.case_id')
        if row['case_id'] not in corpus:
            raise InvalidInput('candidate 有未知 case_id')
        case = corpus[row['case_id']]
        evidence_ids = {evidence['id'] for evidence in case['evidence']}
        for claim in _list(row['claims'], 'candidate.claims'):
            _object(claim, {'claim_id', 'decision', 'relation', 'currency', 'scope',
                            'references'}, 'candidate.claim')
            _label(claim['decision'], DECISIONS, 'decision')
            _validate_dimensions(claim)
            seen = set()
            for reference in _list(claim['references'], 'references', nonempty=False):
                _object(reference, {'evidence_id', 'lines'}, 'reference')
                _identifier(reference['evidence_id'], 'evidence_id')
                if reference['evidence_id'] not in evidence_ids:
                    raise InvalidInput('reference 有未知 evidence_id')
                _lines(reference['lines'], 'reference.lines')
                key = (reference['evidence_id'], *reference['lines'])
                if key in seen:
                    raise InvalidInput('reference 重複')
                seen.add(key)
        _same_ids(_unique(row['claims'], 'claim_id', 'candidate.claim'),
                  {claim['id'] for claim in case['claims']}, 'candidate.claim')
    result = _unique(rows, 'case_id', 'candidate.case')
    _same_ids(result, corpus, 'candidate.case')
    return result


def score_case(case, candidate):
    """純比較已驗證資料；有效定位不代表原文支持陳述。"""
    evidence = {item['id']: item for item in case['evidence']}
    gold = {item['claim_id']: item for item in case['gold']['claims']}
    results = []
    for claim in candidate['claims']:
        expected = gold[claim['claim_id']]
        references = claim['references']
        valid = bool(references) or claim['decision'] == 'omit'
        for reference in references:
            start, end = evidence[reference['evidence_id']]['lines']
            low, high = reference['lines']
            valid = valid and start <= low <= high <= end
        required_spans = expected['citation_spans']
        def covers(reference, span):
            return (reference['evidence_id'] == span['evidence_id']
                    and reference['lines'][0] <= span['lines'][0]
                    and reference['lines'][1] >= span['lines'][1])
        citation_support = (
            claim['decision'] == 'omit' and not references
            or bool(references)
            and all(any(covers(reference, span) for reference in references)
                    for span in required_spans)
            and all(any(covers(reference, span) for span in required_spans)
                    for reference in references)
        )
        unsafe = claim['decision'] == 'assert' and not _safe_to_assert(expected)
        refusal = expected['required'] and claim['decision'] == 'omit'
        scores = {dimension: claim[dimension] == expected[dimension]
                  for dimension in ('relation', 'currency', 'scope')}
        scores.update(decision=claim['decision'] in expected['allowed_decisions'],
                      reference_validity=valid, citation_support=citation_support, unsafe_assertion=not unsafe,
                      over_refusal=not refusal)
        issue_names = {'decision': 'decision_mismatch', 'relation': 'relation_mismatch',
                       'currency': 'currency_mismatch', 'scope': 'scope_mismatch',
                       'reference_validity': 'invalid_reference', 'citation_support': 'unsupported_citation',
                       'unsafe_assertion': 'unsafe_assertion', 'over_refusal': 'over_refusal'}
        results.append({'claim_id': claim['claim_id'], 'scores': scores,
                        'issues': [issue_names[key] for key, passed in scores.items() if not passed]})
    return {'case_id': case['case_id'], 'claims': results}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidInput('命令列參數不合法，請使用 --help 查看用法')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('lint', 'score'), default='lint')
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path)
    output = None
    try:
        args = parser.parse_args(argv)
        if args.command == 'score' and args.candidate is None:
            raise InvalidInput('score 必須提供獨立 candidate 檔案')
        if args.command == 'lint' and args.candidate is not None:
            raise InvalidInput('lint 不接受 candidate，評分請使用 score')
        if args.candidate is not None and (args.candidate.resolve() == args.corpus.resolve()
                or args.candidate.exists() and args.corpus.exists()
                and args.candidate.samefile(args.corpus)):
            raise InvalidInput('candidate 必須與 corpus 使用不同檔案')
        if args.output is not None:
            for source in (args.corpus, args.candidate):
                if source is not None and (args.output.resolve() == source.resolve()
                        or args.output.exists() and source.exists()
                        and args.output.samefile(source)):
                    raise InvalidInput('output 不得覆寫輸入檔案')
            output = args.output
        corpus = load_corpus(args.corpus)
        report = {'mode': args.command, 'case_count': len(corpus),
                  'claim_count': sum(len(case['claims']) for case in corpus.values())}
        code = 0
        if args.command == 'score':
            candidate = load_candidate(args.candidate, corpus)
            cases = [score_case(case, candidate[key]) for key, case in corpus.items()]
            claims = [claim for case in cases for claim in case['claims']]
            report.update(cases=cases, passed=all(not claim['issues'] for claim in claims),
                          scores={dimension: {'passed': sum(claim['scores'][dimension] for claim in claims),
                                              'total': len(claims)}
                                  for dimension in claims[0]['scores']})
            code = 0 if report['passed'] else 1
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    except (InvalidInput, OSError) as exc:
        report = {'error': {'code': 'invalid_input', 'message': str(exc)}}
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        code = 2
    if output is not None:
        try:
            output.write_text(rendered, encoding='utf-8')
        except OSError:
            rendered = json.dumps({'error': {'code': 'invalid_input',
                                           'message': '無法寫入 output 檔案'}},
                                  ensure_ascii=False, indent=2) + '\n'
            code = 2
    sys.stdout.write(rendered)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
