import copy

import pytest

from app.diagnosis import REPAIRS, baseline, validate_diagnosis


@pytest.fixture
def bundle():
    return {
        "id": "case-1",
        "source_sha256": "a" * 64,
        "captured_at": "2026-10-16T00:00:00Z",
        "dataset": {"features": 2},
        "facts": [
            {
                "id": "log-1",
                "kind": "log",
                "text": "summary failed: database is locked during cache read",
            },
            {
                "id": "trace-1",
                "kind": "trace",
                "text": "cache.read status=error error_type=OperationalError database is locked",
            },
        ],
    }


@pytest.fixture
def output():
    return {
        "abstain": False,
        "summary": "SQLite lock evidence was observed.",
        "hypotheses": [
            {
                "kind": "sqlite_contention",
                "statement": "SQLite contention may have interrupted cache reads.",
                "confidence": "medium",
                "supports": [
                    {
                        "fact_id": "log-1",
                        "quote": "summary failed: database is locked during cache read",
                    },
                    {
                        "fact_id": "trace-1",
                        "quote": "cache.read status=error error_type=OperationalError "
                        "database is locked",
                    },
                ],
                "alternatives": ["A separate local database problem may produce similar text."],
            }
        ],
        "uncertainty": ["The evidence is from one request."],
        "repair_key": "review_transaction_scope",
    }


def test_validate_exact_citations_and_pure_repair(bundle, output):
    original = copy.deepcopy(output)
    result = validate_diagnosis(bundle, output)
    assert result["supports_verified"] is True
    assert result["repair"] == REPAIRS["review_transaction_scope"]
    assert result["repair"] is not REPAIRS["review_transaction_scope"]
    assert result["status"] == "requires_human_approval"
    assert output == original


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["hypotheses"][0]["supports"][0].update(quote="database locked"),
        lambda value: value["hypotheses"][0]["supports"][0].update(fact_id="missing"),
        lambda value: value["hypotheses"][0]["supports"].pop(),
        lambda value: value["hypotheses"][0].update(confidence="high"),
        lambda value: value.update(repair_key="validate_schema"),
        lambda value: value.update(uncertainty=[]),
        lambda value: value.update(command="nope"),
    ],
)
def test_validate_rejects_invalid_evidence_or_output(bundle, output, change):
    change(output)
    with pytest.raises(ValueError):
        validate_diagnosis(bundle, output)


def test_validate_rejects_canary_and_parser_hazards(bundle):
    with pytest.raises(ValueError):
        validate_diagnosis(bundle, '{"summary":"LAB_INCIDENT_SENTINEL_DO_NOT_OUTPUT"}')
    with pytest.raises(ValueError):
        validate_diagnosis(bundle, '{"abstain":true,"abstain":false}')
    with pytest.raises(ValueError):
        validate_diagnosis(bundle, '{"abstain":true,"summary":NaN}')


def test_validate_abstention(bundle):
    result = validate_diagnosis(
        bundle,
        {
            "abstain": True,
            "summary": "The evidence is insufficient.",
            "hypotheses": [],
            "uncertainty": ["A second modality is not available."],
            "repair_key": "none",
        },
    )
    assert result["status"] == "no_repair_proposed"
    assert result["repair"]["executable"] is False


def test_baseline_rules_and_healthy_abstention(bundle):
    result = baseline(bundle)
    assert result["hypotheses"][0]["kind"] == "sqlite_contention"
    healthy = copy.deepcopy(bundle)
    healthy["facts"] = [
        {"id": "http", "kind": "http", "text": "summary completed with status 200"},
        {"id": "trace", "kind": "trace", "text": "root request completed without error"},
    ]
    assert baseline(healthy)["abstain"] is True
