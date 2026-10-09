"""Offline regressions for the failure modes recorded in the frozen model study.

Everything here runs against temporary Store/Runner directories. Model responses are
SYNTHETIC REDUCTIONS of the recorded failure shapes: they reproduce the envelope and the
kind of parse error, never the archived generations. No inference, host bridge process,
Docker or network call is made. Passing these tests says nothing about causal truth.
"""

import inspect
import json
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.runner as runner_module
import bridge as host
from app.codec import digest
from app.diagnosis import baseline, validate_diagnosis
from app.evidence import validate_bundle, with_probe
from app.io import atomic_json
from app.main import create_app
from app.runner import Runner
from app.source_pin import SOURCE_SHA
from app.store import Store

DEMO = Path(__file__).resolve().parent.parent / "docs/demo-data.json"
DATASET = {
    "publisher": "USGS",
    "snapshot": True,
    "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
}
LOCK_FACTS = [
    {
        "id": "L01",
        "kind": "log",
        "text": "Synthetic fixture: severity=ERROR; error_type=OperationalError; "
        "message=summary failed: database is locked",
    },
    {
        "id": "T01",
        "kind": "trace",
        "text": "Synthetic fixture: cache.read span status=error; duration_ms=41.2; "
        "error_type=OperationalError.",
    },
]
HEALTHY_FACTS = [
    {
        "id": "L01",
        "kind": "log",
        "text": "Synthetic fixture: severity=INFO; error_type=None; message=summary completed",
    },
    {
        "id": "T01",
        "kind": "trace",
        "text": "Synthetic fixture: all spans status=ok; aggregate duration_ms=0.2.",
    },
]
TIMEOUT = "Approved model request stopped or timed out; no retry"


def make_bundle(identifier, facts):
    return {
        "id": identifier,
        "source_sha256": SOURCE_SHA,
        "captured_at": "2026-10-16T00:00:00Z",
        "dataset": dict(DATASET),
        "facts": [dict(f) for f in facts],
    }


class Collector:
    """Returns fixed synthetic evidence; 'normal' is healthy, any fault mode is a lock."""

    def __init__(self, recorded=None):
        self.calls, self.controls, self.recorded = [], [], recorded

    def capture(self, identifier, mode):
        self.calls.append((identifier, mode))
        if self.recorded is not None:
            b = self.recorded
        else:
            b = make_bundle(identifier, HEALTHY_FACTS if mode == "normal" else LOCK_FACTS)
        return {"bundle": b, "digest": digest(b), "outcomes": [], "telemetry": {}}

    def control(self, mode):
        self.controls.append(mode)

    def close(self):
        pass


def diagnosis():
    """A generic diagnosis that validates against the synthetic lock bundle."""
    return {
        "abstain": False,
        "summary": "A local cache read reported a SQLite lock.",
        "hypotheses": [
            {
                "kind": "sqlite_contention",
                "statement": "Read contention may explain the failed local request.",
                "confidence": "low",
                "supports": [{"fact_id": f["id"], "quote": f["text"]} for f in LOCK_FACTS],
                "alternatives": ["A different transaction could hold the lock."],
            }
        ],
        "uncertainty": ["Synthetic offline fixture, not a live measurement."],
        "repair_key": "review_transaction_scope",
    }


# Two shapes of trailing data after a complete root object. Root-verified archive facts:
# slow_aggregate/normal had extra syntax after the root object (2080 chars, error at
# char 2054); normal/normal ended in a doubled closing brace (558 chars, error at char 557).
# These strings are reductions of those shapes, not the archived text.
SHAPES = {
    "extra_syntax_after_root": ',\n  "repair_key": "none"\n}',
    "double_closing_brace": "}",
}


@pytest.fixture
def lab(tmp_path):
    state = {"path": tmp_path / "state.sqlite", "collector": Collector()}
    state["store"] = Store(state["path"])
    state["runner"] = Runner(
        state["store"], state["collector"], tmp_path / "bridge", tmp_path / "evidence"
    )
    yield state
    state["store"].close()


def ready(runner):
    atomic_json(
        runner.bridge / "heartbeat.json",
        {"at": time.time(), "calls": 0, "status": "ready", "model": "cookllm"},
    )


def capture(lab, scenario="sqlite_lock"):
    identifier = lab["store"].capture({"scenario": scenario, "token": str(uuid.uuid4())})
    lab["runner"].step()
    assert lab["store"].get("cases", identifier)["state"] == "complete"
    return identifier


def request(lab, case_id, policy="model", probe="normal", token=None):
    value = {
        "case_id": case_id,
        "policy": policy,
        "probe": probe,
        "token": token or str(uuid.uuid4()),
    }
    b = with_probe(lab["store"].get("cases", case_id)["evidence"], probe)
    return value, lab["store"].analyze(value, digest(b))


def publish(lab, case_id, token=None):
    """Reserve one model attempt and let the runner write its request file."""
    ready(lab["runner"])
    value, analysis_id = request(lab, case_id, token=token)
    lab["runner"].step()
    row = lab["store"].get("analyses", analysis_id)
    assert row["state"] == "awaiting" and lab["runner"].paths(row)[0].exists()
    return value, row


def envelope(row, **fields):
    """Outer bridge envelope with matching identity, digest and alias."""
    return {
        "id": row["id"],
        "evidence_digest": row["evidence_digest"],
        "model": "cookllm",
        **fields,
    }


def model_rows(store):
    return [r for r in store.rows("analyses") if r["policy"] == "model"]


def no_ipc(runner, row):
    return not any(p.exists() or p.is_symlink() for p in runner.paths(row))


def fail_with_shape(lab, shape):
    case_id = capture(lab)
    value, row = publish(lab, case_id)
    prefix = json.dumps(diagnosis(), indent=2)
    raw = prefix + SHAPES[shape]
    usage = {"input_tokens": 5714, "output_tokens": 2352}
    atomic_json(
        lab["runner"].paths(row)[1],
        envelope(
            row, state="succeeded", attempted=True, output=raw, latency_ms=46528.9, usage=usage
        ),
    )
    lab["runner"].step()
    return case_id, value, lab["store"].get("analyses", row["id"]), prefix, raw, usage


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_extra_data_rejects_whole_output_and_retains_attempt(lab, shape):
    case_id, _, failed, prefix, raw, usage = fail_with_shape(lab, shape)
    bundle = with_probe(lab["store"].get("cases", case_id)["evidence"], "normal")

    # The prefix alone is a valid diagnosis, so a prefix-salvaging parser would succeed.
    assert validate_diagnosis(bundle, prefix)["supports_verified"]
    assert failed["state"] == "failed" and failed["result"] is None
    match = re.fullmatch(r"Extra data: line \d+ column 2 \(char (\d+)\)", failed["error"])
    assert match and int(match.group(1)) == len(prefix)
    assert failed["raw_output"] == raw
    assert failed["usage"] == usage and failed["model_ms"] == 46528.9
    assert no_ipc(lab["runner"], failed)
    assert len(model_rows(lab["store"])) == 1  # reservation consumed, not refunded


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_failed_row_is_frozen_while_independent_baseline_completes(lab, shape):
    case_id, _, failed, *_ = fail_with_shape(lab, shape)
    _, baseline_id = request(lab, case_id, policy="baseline")
    lab["runner"].step()

    done = lab["store"].get("analyses", baseline_id)
    assert done["state"] == "complete", done["error"]
    assert done["result"]["hypotheses"][0]["kind"] == "sqlite_contention"
    assert done["result"]["repair"]["executable"] is False
    assert done["model_ms"] is None and done["raw_output"] is None
    assert lab["store"].get("analyses", failed["id"]) == failed
    assert len(model_rows(lab["store"])) == 1


def test_restart_cleans_terminal_ipc_and_ignores_late_response(lab):
    case_id, value, failed, prefix, *_ = fail_with_shape(lab, "double_closing_brace")
    lab["store"].close()
    lab["store"] = Store(lab["path"])
    request_path, response_path = lab["runner"].paths(failed)
    late = envelope(
        failed,
        state="succeeded",
        attempted=True,
        output=prefix,
        latency_ms=1.0,
        usage={"input_tokens": 1},
    )

    # Terminal IPC files reappearing before restart are removed, never consumed.
    atomic_json(request_path, {"id": failed["id"], "replayed": True})
    atomic_json(response_path, late)
    restarted = Runner(lab["store"], lab["collector"], lab["runner"].bridge, lab["runner"].evidence)
    assert no_ipc(restarted, failed)

    # A late, valid response after restart cannot overwrite the terminal row.
    ready(restarted)
    atomic_json(response_path, late)
    restarted.step()
    lab["store"].finish(failed["id"], result={"forged": True}, usage={"input_tokens": 0})
    assert lab["store"].get("analyses", failed["id"]) == failed
    assert not request_path.exists()  # no republishing
    assert len(model_rows(lab["store"])) == 1  # no refund, no new reservation

    # The same token returns the same identity; a changed request under it is refused.
    assert request(lab, case_id, token=value["token"])[1] == failed["id"]
    with pytest.raises(ValueError, match="Token already used"):
        request(lab, case_id, probe="injection", token=value["token"])
    with pytest.raises(ValueError, match="Token already used"):
        request(lab, case_id, policy="baseline", token=value["token"])
    assert len(model_rows(lab["store"])) == 1
    Runner(lab["store"], lab["collector"], restarted.bridge, restarted.evidence)
    assert no_ipc(restarted, failed)


def test_stopped_or_timed_out_attempt_keeps_duration_and_unknown_usage(lab):
    case_id = capture(lab)
    _, row = publish(lab, case_id)
    # Same envelope bridge.serve writes when invoke() raises its own TimeoutError:
    # attempted, latency measured, no usage and no output.
    atomic_json(
        lab["runner"].paths(row)[1],
        envelope(row, state="failed", attempted=True, error=TIMEOUT, latency_ms=100655.2),
    )
    lab["runner"].step()
    done = lab["store"].get("analyses", row["id"])
    assert done["state"] == "failed" and done["error"] == TIMEOUT
    assert done["model_ms"] == 100655.2
    assert done["usage"] == {}  # not reported: unknown, not zero
    assert done["raw_output"] is None and done["result"] is None
    assert no_ipc(lab["runner"], done)


def test_preinference_failures_have_no_duration_or_usage(lab):
    case_id = capture(lab)
    # attempted=false: the bridge refused before counting an inference.
    _, refused = publish(lab, case_id)
    atomic_json(
        lab["runner"].paths(refused)[1],
        envelope(
            refused,
            state="failed",
            attempted=False,
            error="Durable bridge call cap reached; no further inference",
            latency_ms=2.4,
        ),
    )
    lab["runner"].step()
    refused = lab["store"].get("analyses", refused["id"])
    assert refused["state"] == "failed" and "call cap" in refused["error"]
    assert refused["model_ms"] is None and refused["usage"] is None

    # No heartbeat: the recorded normal/unsupported path, failing before any request file.
    (lab["runner"].bridge / "heartbeat.json").unlink()
    _, unavailable_id = request(lab, case_id, probe="unsupported")
    lab["runner"].step()
    unavailable = lab["store"].get("analyses", unavailable_id)
    assert unavailable["error"] == "Approved host bridge unavailable; no provider fallback"
    assert unavailable["published"] == 0 and unavailable["started"] is None
    assert unavailable["model_ms"] is None and unavailable["usage"] is None
    assert no_ipc(lab["runner"], unavailable)
    assert len(model_rows(lab["store"])) == 2  # both still count toward the lifetime cap


def test_expired_request_fails_once_and_late_response_is_discarded(lab, monkeypatch):
    case_id = capture(lab)
    _, row = publish(lab, case_id)
    later = time.time() + 130  # beyond the runner's 125 s request lifetime
    monkeypatch.setattr(runner_module, "time", SimpleNamespace(time=lambda: later))
    lab["runner"].step()
    expired = lab["store"].get("analyses", row["id"])
    assert expired["state"] == "failed"
    assert expired["error"] == "Interrupted/missing/expired request; no inference retry"
    assert expired["model_ms"] is None and expired["usage"] is None
    assert no_ipc(lab["runner"], expired)

    atomic_json(
        lab["runner"].paths(row)[1],
        envelope(
            row,
            state="succeeded",
            attempted=True,
            output=json.dumps(diagnosis()),
            latency_ms=9.0,
            usage={"input_tokens": 3},
        ),
    )
    lab["runner"].step()
    assert lab["store"].get("analyses", row["id"]) == expired
    assert not lab["runner"].paths(row)[0].exists()
    Runner(lab["store"], lab["collector"], lab["runner"].bridge, lab["runner"].evidence)
    assert no_ipc(lab["runner"], expired)


def test_http_bridge_check_precedes_dedup_and_failures_stay_visible(tmp_path):
    collector = Collector()
    service = create_app(
        tmp_path / "state.sqlite",
        tmp_path / "bridge",
        tmp_path / "evidence",
        collector=collector,
        background=False,
    )
    with TestClient(service, base_url="http://127.0.0.1") as client:
        runner = service.state.runner
        case = {"scenario": "sqlite_lock", "token": str(uuid.uuid4())}
        case_id = client.post("/api/cases", json=case).json()["id"]
        runner.step()
        body = {
            "case_id": case_id,
            "policy": "model",
            "probe": "normal",
            "token": str(uuid.uuid4()),
        }

        ready(runner)  # explicitly synthetic heartbeat; no bridge process runs
        created = client.post("/api/analyses", json=body)
        assert created.status_code == 202
        analysis_id = created.json()["id"]
        runner.step()
        row = service.state.store.get("analyses", analysis_id)
        raw = json.dumps(diagnosis(), indent=2) + "}"
        atomic_json(
            runner.paths(row)[1],
            envelope(
                row, state="succeeded", attempted=True, output=raw, latency_ms=51030.0, usage={}
            ),
        )
        runner.step()

        # Offline: even an exact replay is refused before Store deduplication.
        (runner.bridge / "heartbeat.json").unlink()
        assert client.post("/api/analyses", json=body).status_code == 503
        ready(runner)
        assert client.post("/api/analyses", json=body).json()["id"] == analysis_id
        changed = client.post("/api/analyses", json={**body, "probe": "injection"})
        assert changed.status_code == 400

        (runner.bridge / "heartbeat.json").unlink()
        rule = client.post(
            "/api/analyses", json={**body, "policy": "baseline", "token": str(uuid.uuid4())}
        )
        assert rule.status_code == 202
        runner.step()
        assert client.get("/api/analyses/" + rule.json()["id"]).json()["state"] == "complete"

        detail = client.get("/api/analyses/" + analysis_id).json()
        assert detail["state"] == "failed" and detail["result"] is None
        assert detail["error"].startswith("Extra data:") and detail["raw_output"] == raw
        assert detail["model_ms"] == 51030.0 and detail["bundle"]["id"] == case_id
        exported = client.get(f"/api/cases/{case_id}/export").json()
        assert {a["state"] for a in exported["analyses"]} == {"failed", "complete"}
        assert exported["repairs_executable"] is False
        state = client.get("/api/state").json()
        assert state["model_reservations"] == 1 and state["actual_cost_usd"] is None
        assert client.post("/api/repairs/apply", json={"approved": True}).status_code == 404


@pytest.fixture(scope="module")
def demo():
    return json.loads(DEMO.read_text())


def recorded(demo, scenario):
    return validate_bundle(next(c for c in demo["cases"] if c["id"] == scenario)["evidence"])


@pytest.mark.parametrize("probe", ["normal", "unsupported"])
def test_recorded_healthy_capture_baseline_abstains_without_repair(tmp_path, demo, probe):
    store = Store(tmp_path / "state.sqlite")
    try:
        lab = {"store": store, "collector": Collector(recorded(demo, "normal"))}
        lab["runner"] = Runner(store, lab["collector"], tmp_path / "bridge", tmp_path / "ev")
        _, analysis_id = request(lab, capture(lab, "normal"), policy="baseline", probe=probe)
        lab["runner"].step()
        result = store.get("analyses", analysis_id)["result"]
        assert result["abstain"] and result["hypotheses"] == []
        assert result["repair_key"] == "none" and result["status"] == "no_repair_proposed"
        assert result["repair"]["executable"] is False
    finally:
        store.close()


def test_walkthrough_numbers_trace_to_public_records(demo):
    """Provenance only: the walkthrough's quoted figures exist in committed public data."""
    facts = {f["id"]: f["text"] for f in recorded(demo, "sqlite_lock")["facts"]}
    assert facts["L04"].endswith(
        "error_type=OperationalError; message=summary failed: database is locked"
    )
    spans = json.loads(facts["T04"].split("spans=", 1)[1])
    assert [(s["name"], s["status"], round(s["duration_ms"], 3)) for s in spans[1:]] == [
        ("dependency.request", "ok", 5.483),
        ("cache.read", "error", 50.496),
    ]
    assert "aggregate" not in {s["name"] for s in spans}
    for text in [
        "baseline_errors=0",
        "incident_errors=3",
        "baseline_median_ms=71.105",
        "incident_median_ms=60.773",
        "delta_ms=-10.332",
    ]:
        assert text in facts["M01"]
    assert baseline(recorded(demo, "sqlite_lock"))["repair_key"] == "review_transaction_scope"

    study = demo["study_summary"]
    rows = {r["condition"]: r for r in study["model_conditions"]}
    assert (study["frozen_conditions"], study["actual_host_attempts"]) == (7, 6)
    assert study["validated_matching_conditions"] == 2 and study["actual_cost_usd"] is None
    assert study["usage_known_attempts"] == 4
    assert study["observed_token_totals_partial"]["input_tokens"] == 18267
    assert study["observed_token_totals_partial"]["output_tokens"] == 6144
    for condition in ["schema_mismatch/normal", "dependency_timeout/normal"]:
        assert rows[condition]["error"] == TIMEOUT and rows[condition]["usage"] == {}
        assert rows[condition]["model_ms"] > 100_000
    unsupported = rows["normal/unsupported"]
    assert unsupported["model_ms"] is None and unsupported["usage"] is None
    # The recorded timeout text is the bridge's own stop-or-deadline message; its deadline
    # is 100 s. It does not name a provider, network or transport cause.
    assert inspect.signature(host.invoke).parameters["timeout"].default == 100
    assert TIMEOUT in inspect.getsource(host.invoke)
