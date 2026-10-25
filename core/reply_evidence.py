from dataclasses import dataclass, asdict
from enum import StrEnum
import hashlib
import json
import re
from typing import Literal

POLICY_VERSION = "model_assisted_v1"
MAX_MODEL_BYTES = 100_000
MAX_REPLY_CHARS = 20_000


class BlockReason(StrEnum):
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    CAPACITY = "capacity"
    READ_BUDGET = "read_budget"
    MODEL_BUDGET = "model_budget"
    CONTEXT_BUDGET = "context_budget"
    SCHEMA_ERROR = "schema_error"
    PARTIAL = "partial"
    SOURCE_ERROR = "source_error"
    REVIEW_REQUIRED = "review_required"
    SCOPE_STALE = "scope_stale"
    POLICY = "policy"
    DISPATCH_UNKNOWN = "dispatch_unknown"


class EvidenceError(ValueError):
    pass


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(value) -> str:
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def strict_json(raw: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("模型 JSON 包含重複欄位")
            result[key] = value
        return result
    def invalid_constant(_):
        raise EvidenceError("模型 JSON 包含非有限數值")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MODEL_BYTES:
        raise EvidenceError("模型回應超過大小限制")
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise EvidenceError("模型沒有回傳合法 JSON") from exc
    if not isinstance(result, dict):
        raise EvidenceError("模型 JSON 必須是物件")
    return result


def fields(value: dict, required: set[str]):
    if not isinstance(value, dict) or set(value) != required:
        raise EvidenceError("模型 JSON 欄位不符合契約")


def short_text(value, limit=1200, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise EvidenceError("模型文字欄位無效")
    return value


@dataclass(frozen=True)
class Query:
    source_id: str
    operation: Literal["anchor", "chat_recent", "git_search"]
    terms: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def json(self):
        return dict(source_id=self.source_id, operation=self.operation,
                    terms=list(self.terms), paths=list(self.paths))


def parse_queries(raw: dict, sources: dict) -> tuple[Query, ...]:
    fields(raw, {"queries"})
    rows = raw["queries"]
    if not isinstance(rows, list) or len(rows) > 12:
        raise EvidenceError("補查數量無效")
    result = []
    for row in rows:
        fields(row, {"source_id", "operation", "terms", "paths"})
        source = sources.get(row["source_id"]) if isinstance(row["source_id"], str) else None
        if not source or source["use"] == "metadata_only":
            raise EvidenceError("補查來源未獲本題允許")
        operation = "chat_recent" if source["kind"] == "chat" else "git_search"
        if row["operation"] != operation:
            raise EvidenceError("不支援的來源操作")
        terms, paths = row["terms"], row["paths"]
        if (not isinstance(terms, list) or len(terms) > 8
            or any(not isinstance(t, str) or not re.fullmatch(r"[\w\u3400-\u9fff .-]{1,80}", t) for t in terms)
            or not isinstance(paths, list) or len(paths) > 20
            or any(not isinstance(p, str) or not p or len(p) > 240
                   or p.startswith(("/", "-")) or "\\" in p or ":" in p
                   or ".." in p.split("/") or any(c in p for c in "\x00\n\r*?[]") for p in paths)):
            raise EvidenceError("補查只接受有限搜尋詞與相對路徑")
        if operation == "chat_recent" and (terms or paths):
            raise EvidenceError("Chat 初版只支援固定近期查詢")
        if source.get("paths") and any(p not in source["paths"] for p in paths):
            raise EvidenceError("補查超出登錄路徑")
        result.append(Query(row["source_id"], operation, tuple(terms), tuple(paths)))
    return tuple(result)


@dataclass(frozen=True)
class Evidence:
    id: str
    source_id: str
    use: str
    locator: str
    revision: str
    text: str
    environment: str | None
    event_time: str | None
    kind: str

    def prompt(self) -> dict:
        return asdict(self)

    def reference(self) -> dict:
        return {"evidence_id": self.id, "locator": self.locator, "revision": self.revision}


@dataclass(frozen=True)
class SourceRead:
    query: Query
    status: str
    evidence: tuple[Evidence, ...] = ()
    error: str | None = None
    images_omitted: bool = False

    def ledger(self) -> dict:
        refs = [{**e.reference(), "digest": text_hash(e.text)} for e in self.evidence]
        return {"query": self.query.json(), "status": self.status, "error": self.error,
                "images_omitted": self.images_omitted,
                "digest": fingerprint({"refs": refs, "status": self.status,
                                       "images_omitted": self.images_omitted}), "references": refs}


def public_evidence(reads: list[SourceRead]) -> dict[str, Evidence]:
    return {e.id: e for read in reads if read.status == "complete"
            for e in read.evidence if e.use == "reply_context"}


def parse_pack(raw: dict, evidence: dict[str, Evidence], environment: str | None) -> dict:
    fields(raw, {"facts", "gaps"})
    if not isinstance(raw["facts"], list) or len(raw["facts"]) > 40:
        raise EvidenceError("事實包數量無效")
    gaps = parse_gaps(raw["gaps"])
    facts, ids = [], set()
    for fact in raw["facts"]:
        fields(fact, {"id", "text", "kind", "evidence_ids", "relation", "currency", "scope"})
        ident = short_text(fact["id"], 80)
        if ident in ids:
            raise EvidenceError("事實包識別值重複")
        ids.add(ident)
        short_text(fact["text"])
        _enums(fact)
        refs = _references(fact["evidence_ids"], evidence)
        if (fact["relation"] == "supported" and fact["currency"] == "current"
            and fact["scope"] == "match" and _scope_matches(refs, environment)):
            facts.append({"id": ident, "text": fact["text"], "kind": fact["kind"],
                          "citations": [e.reference() for e in refs]})
    return {"facts": facts, "gaps": gaps}


def parse_gaps(value):
    if not isinstance(value, list) or len(value) > 30:
        raise EvidenceError("缺口清單無效")
    return [short_text(item, 500) for item in value]


def _enums(row):
    for key, allowed in {"kind": ("status", "cause", "recommendation", "other_fact"),
                         "relation": ("supported", "contradicted", "insufficient"),
                         "currency": ("current", "superseded", "unknown"),
                         "scope": ("match", "mismatch", "unknown")}.items():
        if row[key] not in allowed:
            raise EvidenceError("模型評估值無效")


def _references(ids, evidence):
    if (not isinstance(ids, list) or not ids or len(ids) > 20
        or any(not isinstance(i, str) or i not in evidence for i in ids)
        or len(set(ids)) != len(ids)):
        raise EvidenceError("引用不存在或未允許揭露")
    return [evidence[i] for i in ids]


def _scope_matches(refs, environment):
    return environment is not None and all(
        e.environment is None or e.environment == environment for e in refs)


def parse_assessment(raw: dict, text: str, evidence: dict[str, Evidence],
                     environment: str | None) -> dict:
    fields(raw, {"claims", "segments", "gaps"})
    if not isinstance(raw["claims"], list) or len(raw["claims"]) > 60:
        raise EvidenceError("陳述數量無效")
    claims, claim_ids = [], set()
    for row in raw["claims"]:
        fields(row, {"id", "text", "kind", "relation", "reference_validity", "currency", "scope", "reason", "evidence_ids"})
        ident = short_text(row["id"], 80)
        if ident in claim_ids:
            raise EvidenceError("陳述識別值重複")
        claim_ids.add(ident)
        short_text(row["text"], MAX_REPLY_CHARS)
        short_text(row["reason"], 500)
        _enums(row)
        if row["reference_validity"] not in ("valid", "invalid", "unresolved"):
            raise EvidenceError("引用狀態無效")
        refs = _references(row["evidence_ids"], evidence) if row["evidence_ids"] else []
        if row["evidence_ids"] != [] and not refs:
            raise EvidenceError("引用清單無效")
        claim = {k: v for k, v in row.items() if k != "evidence_ids"}
        if not refs:
            claim["reference_validity"] = "unresolved"
        if not _scope_matches(refs, environment):
            claim["scope"] = "unknown" if environment is None else "mismatch"
        claim["citations"] = [e.reference() for e in refs]
        claims.append(claim)
    segments = raw["segments"]
    if not isinstance(segments, list) or not segments or len(segments) > 120:
        raise EvidenceError("評估必須涵蓋完整回話")
    cursor, covered = 0, set()
    by_id = {c["id"]: c for c in claims}
    for segment in segments:
        fields(segment, {"start", "end", "kind", "claim_id"})
        start, end = segment["start"], segment["end"]
        if type(start) is not int or type(end) is not int or start != cursor or end <= start or end > len(text):
            raise EvidenceError("評估文字範圍不連續或越界")
        cursor = end
        if segment["kind"] == "claim":
            claim = by_id.get(segment["claim_id"]) if isinstance(segment["claim_id"], str) else None
            if not claim or claim["text"] != text[start:end]:
                raise EvidenceError("陳述與原樣文字範圍不符")
            covered.add(claim["id"])
        elif segment["kind"] not in ("non_fact", "gap") or segment["claim_id"] is not None:
            raise EvidenceError("未知文字範圍分類")
    if cursor != len(text) or covered != claim_ids:
        raise EvidenceError("評估未涵蓋完整回話與陳述")
    gaps = parse_gaps(raw["gaps"])
    if any(s["kind"] == "gap" for s in segments) and not gaps:
        raise EvidenceError("保留說法必須列出缺口")
    return {"status": "evaluated", "method": POLICY_VERSION, "text_hash": text_hash(text),
            "claims": claims, "gaps": gaps, "stop_reason": None}


def eligible(assessment: dict) -> bool:
    return (assessment.get("status") == "evaluated" and assessment.get("method") == POLICY_VERSION
            and assessment.get("stop_reason") is None
            and all(c["relation"] == "supported" and c["reference_validity"] == "valid"
                    and c["currency"] == "current" and c["scope"] == "match"
                    for c in assessment["claims"]))


def unevaluated(reason: BlockReason, text="") -> dict:
    return {"status": "unevaluated", "method": POLICY_VERSION, "text_hash": text_hash(text),
            "claims": [], "gaps": [], "stop_reason": reason.value}
