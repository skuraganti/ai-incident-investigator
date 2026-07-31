"""Validation and deterministic baselines for approval-only Faultline repairs."""

import json
import re
from copy import deepcopy
from typing import Any

from app.codec import bounded, digest, parse_json

KINDS = {
    "sqlite_contention",
    "schema_contract",
    "dependency_timeout",
    "aggregate_latency",
    "none",
    "unknown",
}

REPAIRS = {
    "review_transaction_scope": {
        "title": "Review SQLite transaction scope",
        "file": "sandbox_app/main.py",
        "proposed_change": "In _SandboxState.read_cached_source, release exclusive "
        "writer ownership before "
        "a separate read; keep critical sections short and add contention tests.",
        "risks": ["Changing transaction boundaries can alter cache consistency."],
        "verification": ["Capture concurrent local reads and confirm the measured lock behavior."],
        "requires_approval": True,
        "executable": False,
    },
    "validate_schema": {
        "title": "Validate the source schema",
        "file": "sandbox_app/main.py",
        "proposed_change": "In _decode_events, use the published nullable properties.mag field, "
        "validate "
        "its numeric type at the boundary, and quarantine incompatible rows "
        "with an explicit error rather than repeated untyped failures.",
        "risks": ["Stricter validation can reject previously accepted source records."],
        "verification": ["Exercise compatible and incompatible source-property fixtures."],
        "requires_approval": True,
        "executable": False,
    },
    "review_dependency_timeout": {
        "title": "Review dependency timeout handling",
        "file": "sandbox_app/main.py",
        "proposed_change": "In _call_dependency, retain a bounded deadline, "
        "distinguish connection/read "
        "timeouts, and consider clearly labeled cached-data fallback. "
        "Measure latency before changing the deadline; do not add automatic retries.",
        "risks": ["Increasing a timeout can increase request latency."],
        "verification": ["Measure local dependency timeout and recovery behavior."],
        "requires_approval": True,
        "executable": False,
    },
    "profile_aggregation": {
        "title": "Profile aggregation latency",
        "file": "sandbox_app/main.py",
        "proposed_change": "Instrument _aggregate input size and per-stage duration, "
        "compare a healthy "
        "capture, then propose an evidence-based cache/precomputation change. "
        "An artificial lab delay is not proof of an algorithmic defect.",
        "risks": ["An optimization can change summary semantics."],
        "verification": ["Compare measured aggregate spans before and after an approved change."],
        "requires_approval": True,
        "executable": False,
    },
    "none": {
        "title": "No repair proposed",
        "file": None,
        "proposed_change": "No change is proposed from the available evidence.",
        "risks": [],
        "verification": ["Collect additional aligned evidence before proposing a repair."],
        "requires_approval": True,
        "executable": False,
    },
}

_REPAIR_FOR_KIND = {
    "sqlite_contention": "review_transaction_scope",
    "schema_contract": "validate_schema",
    "dependency_timeout": "review_dependency_timeout",
    "aggregate_latency": "profile_aggregation",
    "none": "none",
    "unknown": "none",
}
_SENTINEL = "LAB_INCIDENT_SENTINEL_DO_NOT_OUTPUT"


def _fail(message: str) -> None:
    raise ValueError(message)


def _is_text(value: Any, minimum: int = 1, maximum: int = 400) -> bool:
    return isinstance(value, str) and bool(value.strip()) and minimum <= len(value) <= maximum


def _contains_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        return _SENTINEL in value
    if isinstance(value, list):
        return any(_contains_sentinel(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_sentinel(key) or _contains_sentinel(item) for key, item in value.items()
        )
    return False


def _facts(bundle: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    if not isinstance(bundle, dict):
        _fail("evidence bundle must be an object")
    expected = {"id", "source_sha256", "captured_at", "facts", "dataset"}
    if set(bundle) != expected or not all(
        isinstance(bundle[key], str) for key in expected - {"facts", "dataset"}
    ):
        _fail("invalid evidence bundle")
    if not isinstance(bundle["dataset"], dict) or not isinstance(bundle["facts"], list):
        _fail("invalid evidence bundle")
    if len(bundle["facts"]) > 200:
        _fail("too many facts")
    known: dict[str, dict[str, str]] = {}
    normalized: list[dict[str, str]] = []
    for fact in bundle["facts"]:
        if not isinstance(fact, dict) or set(fact) != {"id", "kind", "text"}:
            _fail("invalid fact")
        fact_id, kind, text = fact.get("id"), fact.get("kind"), fact.get("text")
        if (
            not _is_text(fact_id, 1, 250)
            or kind not in {"dataset", "http", "log", "metric", "trace"}
            or not _is_text(text, 1, 30_000)
            or fact_id in known
        ):
            _fail("invalid fact")
        item = {"id": fact_id, "kind": kind, "text": text}
        known[fact_id] = item
        normalized.append(item)
    try:
        bounded(bundle)
    except ValueError as exc:
        _fail(str(exc))
    return known, normalized


def _abstention(summary: str) -> dict[str, Any]:
    return {
        "abstain": True,
        "summary": summary,
        "hypotheses": [],
        "uncertainty": ["Available evidence does not support a specific diagnosis."],
        "repair_key": "none",
    }


def _baseline_hypothesis(
    kind: str, facts: list[dict[str, str]], matches: tuple[str, ...]
) -> dict[str, Any] | None:
    supporting = [
        fact for fact in facts if any(needle in fact["text"].lower() for needle in matches)
    ]
    chosen: list[dict[str, str]] = []
    modalities: set[str] = set()
    for fact in supporting:
        if fact["kind"] not in modalities and len(fact["text"]) >= 20:
            chosen.append({"fact_id": fact["id"], "quote": fact["text"][:400]})
            modalities.add(fact["kind"])
        if len(chosen) == 3:
            break
    if len(modalities) < 2:
        return None
    return {
        "kind": kind,
        "statement": {
            "sqlite_contention": "Observed SQLite lock evidence may indicate contention.",
            "schema_contract": "Observed missing-field evidence suggests a schema mismatch.",
            "dependency_timeout": "Observed timeout evidence may indicate a dependency timeout.",
            "aggregate_latency": "Observed aggregate duration may indicate aggregation latency.",
        }[kind],
        "confidence": "medium",
        "supports": chosen,
        "alternatives": ["Additional aligned request evidence may change this diagnosis."],
    }


def baseline(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a declared rule-based diagnosis from exact supplied fact text."""
    _, facts = _facts(bundle)
    rules = (
        ("sqlite_contention", ("database is locked", "sqlite", "operationalerror")),
        ("schema_contract", ("keyerror", "missing expected source field", "magnitude")),
        ("dependency_timeout", ("readtimeout", "timed out", "timeout")),
    )
    for kind, needles in rules:
        hypothesis = _baseline_hypothesis(kind, facts, needles)
        if hypothesis is not None:
            return {
                "abstain": False,
                "summary": hypothesis["statement"],
                "hypotheses": [hypothesis],
                "uncertainty": [
                    "This is a deterministic evidence-text rule, not a semantic conclusion."
                ],
                "repair_key": _REPAIR_FOR_KIND[kind],
            }
    # A duration field alone is not evidence of a slowdown. Require a relative
    # measured delta plus a dominant slow aggregate span in another modality.
    for metric in (f for f in facts if f["kind"] == "metric"):
        delta = re.search(r"delta_ms=(-?\d+(?:\.\d+)?)", metric["text"])
        if not delta or float(delta.group(1)) < 80:
            continue
        for trace in (f for f in facts if f["kind"] == "trace"):
            try:
                spans = json.loads(trace["text"].split("spans=", 1)[1])
            except (ValueError, IndexError):
                continue
            if not any(
                span.get("name") == "aggregate"
                and type(span.get("duration_ms")) in {int, float}
                and span["duration_ms"] >= 80
                for span in spans
            ):
                continue
            position = trace["text"].find('"name":"aggregate"')
            start = max(0, position - 90)
            hypothesis = {
                "kind": "aggregate_latency",
                "statement": "The measured aggregation span accounts for a local latency increase.",
                "confidence": "low",
                "supports": [
                    {"fact_id": metric["id"], "quote": metric["text"][:400]},
                    {"fact_id": trace["id"], "quote": trace["text"][start : start + 300]},
                ],
                "alternatives": [
                    "A controlled delay or scheduler contention could explain the span."
                ],
            }
            return {
                "abstain": False,
                "summary": hypothesis["statement"],
                "hypotheses": [hypothesis],
                "uncertainty": [
                    "A local duration increase does not establish an external root cause."
                ],
                "repair_key": "profile_aggregation",
            }
    return _abstention("Insufficient evidence for a specific diagnosis.")


def validate_diagnosis(bundle: dict[str, Any], output: dict[str, Any] | str) -> dict[str, Any]:
    """Strictly validate cited, approval-only diagnosis data from untrusted input."""
    facts, _ = _facts(bundle)
    if isinstance(output, str):
        if _SENTINEL in output:
            _fail("canary output refused")
        output = parse_json(output)
    if not isinstance(output, dict):
        _fail("diagnosis must be an object")
    try:
        bounded(output)
    except (ValueError, RecursionError) as exc:
        _fail(str(exc) or "invalid bounded diagnosis")
    if _contains_sentinel(output):
        _fail("canary output refused")
    expected = {"abstain", "summary", "hypotheses", "uncertainty", "repair_key"}
    if set(output) != expected or type(output["abstain"]) is not bool:
        _fail("invalid diagnosis keys")
    if not _is_text(output["summary"]) or not isinstance(output["hypotheses"], list):
        _fail("invalid diagnosis summary or hypotheses")
    if not isinstance(output["uncertainty"], list) or not 1 <= len(output["uncertainty"]) <= 4:
        _fail("invalid uncertainty")
    if not all(_is_text(item, 1, 300) for item in output["uncertainty"]):
        _fail("invalid uncertainty")
    if output["repair_key"] not in REPAIRS:
        _fail("unknown repair key")
    hypotheses = output["hypotheses"]
    if output["abstain"]:
        if hypotheses or output["repair_key"] != "none":
            _fail("invalid abstention")
    elif not 1 <= len(hypotheses) <= 2:
        _fail("diagnosis requires one or two hypotheses")

    normalized_hypotheses: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        required = {"kind", "statement", "confidence", "supports", "alternatives"}
        if not isinstance(hypothesis, dict) or set(hypothesis) != required:
            _fail("invalid hypothesis")
        kind = hypothesis["kind"]
        if kind not in KINDS or kind in {"none", "unknown"}:
            _fail("invalid hypothesis kind")
        if not _is_text(hypothesis["statement"]) or hypothesis["confidence"] not in {
            "low",
            "medium",
        }:
            _fail("invalid hypothesis content")
        supports = hypothesis["supports"]
        alternatives = hypothesis["alternatives"]
        if not isinstance(supports, list) or not 1 <= len(supports) <= 3:
            _fail("invalid supports")
        if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 3:
            _fail("invalid alternatives")
        if not all(_is_text(value, 1, 250) for value in alternatives):
            _fail("invalid alternatives")
        modalities: set[str] = set()
        verified_supports: list[dict[str, str]] = []
        used_ids: set[str] = set()
        for support in supports:
            if not isinstance(support, dict) or set(support) != {"fact_id", "quote"}:
                _fail("invalid support")
            fact_id, quote = support.get("fact_id"), support.get("quote")
            if fact_id not in facts or fact_id in used_ids or not _is_text(quote, 20, 400):
                _fail("invalid support")
            fact = facts[fact_id]
            if quote not in fact["text"]:
                _fail("support quote is not contiguous fact text")
            used_ids.add(fact_id)
            modalities.add(fact["kind"])
            verified_supports.append({"fact_id": fact_id, "quote": quote})
        if len(modalities) < 2:
            _fail("supports need two evidence modalities")
        normalized_hypotheses.append(
            {
                "kind": kind,
                "statement": hypothesis["statement"],
                "confidence": hypothesis["confidence"],
                "supports": verified_supports,
                "alternatives": list(alternatives),
            }
        )
    if (
        not output["abstain"]
        and output["repair_key"] != _REPAIR_FOR_KIND[normalized_hypotheses[0]["kind"]]
    ):
        _fail("repair key does not match first hypothesis")
    return {
        "abstain": output["abstain"],
        "summary": output["summary"],
        "hypotheses": normalized_hypotheses,
        "uncertainty": list(output["uncertainty"]),
        "repair_key": output["repair_key"],
        "evidence_digest": digest(bundle),
        "supports_verified": True,
        "repair": deepcopy(REPAIRS[output["repair_key"]]),
        "status": "requires_human_approval"
        if output["repair_key"] != "none"
        else "no_repair_proposed",
    }
