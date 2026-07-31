import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import bridge as host
from app.codec import digest
from app.evidence import Collector, validate_bundle, with_probe
from app.io import atomic_json
from app.main import create_app
from app.runner import Runner
from app.source_pin import SOURCE_SHA
from app.store import Store


def bundle(identifier):
    return {
        "id": identifier,
        "source_sha256": SOURCE_SHA,
        "captured_at": "2026-10-16T00:00:00Z",
        "dataset": {
            "publisher": "USGS",
            "snapshot": True,
            "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
        },
        "facts": [
            {
                "id": "L01",
                "kind": "log",
                "text": "Observed OperationalError: database is locked during cache.read request.",
            },
            {
                "id": "T01",
                "kind": "trace",
                "text": "Measured cache.read span status=error; duration_ms=41.2; "
                "error_type=OperationalError.",
            },
        ],
    }


def proposal():
    b = bundle(str(uuid.uuid4()))
    return {
        "abstain": False,
        "summary": "Local cache read encountered a lock.",
        "hypotheses": [
            {
                "kind": "sqlite_contention",
                "statement": "Read contention may explain the local request failure.",
                "confidence": "low",
                "supports": [{"fact_id": f["id"], "quote": f["text"]} for f in b["facts"]],
                "alternatives": ["A different transaction could be holding the lock."],
            }
        ],
        "uncertainty": ["This is synthetic integration evidence, not a live measurement."],
        "repair_key": "review_transaction_scope",
    }


class FakeCollector:
    def __init__(self):
        self.calls = []

    def capture(self, identifier, mode):
        self.calls.append((identifier, mode))
        b = bundle(identifier)
        return {
            "bundle": b,
            "digest": digest(b),
            "outcomes": [],
            "telemetry": {},
            "experiment_cleanup_normal": True,
        }

    def control(self, mode):
        self.controls = getattr(self, "controls", []) + [mode]

    def close(self):
        pass


@pytest.fixture
def system(tmp_path):
    store, collector = Store(tmp_path / "state.sqlite"), FakeCollector()
    runner = Runner(store, collector, tmp_path / "bridge", tmp_path / "data/evidence")
    yield store, collector, runner
    store.close()


def captured(system):
    store, _, runner = system
    identifier = store.capture({"scenario": "sqlite_lock", "token": str(uuid.uuid4())})
    runner.step()
    assert store.get("cases", identifier)["state"] == "complete"
    return identifier


def ready(runner):
    atomic_json(
        runner.bridge / "heartbeat.json",
        {"at": time.time(), "calls": 0, "status": "ready", "model": "cookllm"},
    )


def analyze(system, identifier, policy="baseline", probe="normal", token=None):
    store, _, _ = system
    b = with_probe(store.get("cases", identifier)["evidence"], probe)
    return store.analyze(
        {
            "case_id": identifier,
            "policy": policy,
            "probe": probe,
            "token": token or str(uuid.uuid4()),
        },
        digest(b),
    )


def test_case_idempotency_and_no_label_leak(system):
    store, collector, runner = system
    value = {"scenario": "sqlite_lock", "token": str(uuid.uuid4())}
    identifier = store.capture(value)
    assert store.capture(value) == identifier
    with pytest.raises(ValueError):
        store.capture({**value, "scenario": "normal"})
    runner.step()
    case = store.get("cases", identifier)
    assert "scenario" not in json.dumps(case["evidence"])
    assert len(collector.calls) == 1
    assert validate_bundle(case["evidence"])


def test_fixed_bundle_destination_and_probe_controls(system):
    case_id = captured(system)
    b = system[0].get("cases", case_id)["evidence"]
    with pytest.raises(ValueError):
        Collector("http://169.254.169.254")
    with pytest.raises(ValueError):
        validate_bundle({**b, "source_sha256": "a" * 64})
    with pytest.raises(ValueError):
        validate_bundle({**b, "dataset": {"private": "unapproved content"}})
    probe = with_probe(b, "injection")
    assert probe["facts"][-1]["id"] == "P00" and probe != b
    assert len(b["facts"]) == 2


def test_baseline_and_unsupported_are_nonexecutable(system):
    identifier = captured(system)
    store, collector, runner = system
    normal = analyze(system, identifier)
    unknown = analyze(system, identifier, probe="unsupported")
    runner.step()
    result = store.get("analyses", normal)
    assert result["state"] == "complete", result["error"]
    assert result["result"]["repair"]["requires_approval"]
    assert result["result"]["repair"]["executable"] is False
    assert store.get("analyses", unknown)["result"]["abstain"]
    assert len(collector.calls) == 1


def test_pending_identity_response_first_no_oracle_payload(system, tmp_path, monkeypatch):
    identifier = captured(system)
    store, _, runner = system
    ready(runner)
    analysis_id = analyze(system, identifier, "model")
    runner.step()
    row = store.get("analyses", analysis_id)
    content = runner.paths(row)[0].read_bytes()
    Runner(store, system[1], runner.bridge, runner.evidence).step()
    assert runner.paths(row)[0].read_bytes() == content
    monkeypatch.setattr(host, "ROOT", tmp_path)
    payload = host.approved_context(json.loads(content))
    assert set(payload) == {"question", "evidence"}
    assert "scenario" not in json.dumps(payload)
    atomic_json(
        runner.paths(row)[1],
        {
            "id": analysis_id,
            "evidence_digest": row["evidence_digest"],
            "model": "cookllm",
            "state": "succeeded",
            "attempted": True,
            "output": json.dumps(proposal()),
            "latency_ms": 1.2,
            "usage": {"input_tokens": 3},
        },
    )
    runner.paths(row)[0].unlink()
    runner.step()
    done = store.get("analyses", analysis_id)
    assert done["state"] == "complete", done["error"]
    assert done["result"]["supports_verified"]
    assert done["raw_output"]


@pytest.mark.parametrize(
    "raw", ["[]", '{"latency_ms":NaN}', "[" * 2000 + "]" * 2000, '{"latency_ms":' + "9" * 400 + "}"]
)
def test_malformed_response_never_succeeds_or_kills_runner(system, raw):
    identifier = captured(system)
    store, _, runner = system
    ready(runner)
    analysis_id = analyze(system, identifier, "model")
    runner.step()
    row = store.get("analyses", analysis_id)
    runner.paths(row)[1].write_text(raw)
    runner.step()
    done = store.get("analyses", analysis_id)
    assert done["state"] == "failed" and done["error"]
    assert done["result"] is None


def test_fifo_orphan_terminal_cleanup_and_interrupted_capture(system):
    identifier = captured(system)
    store, collector, runner = system
    ready(runner)
    analysis_id = analyze(system, identifier, "model")
    runner.step()
    row = store.get("analyses", analysis_id)
    runner.paths(row)[0].unlink()
    os.mkfifo(runner.paths(row)[0])
    runner.step()
    assert store.get("analyses", analysis_id)["state"] == "failed"
    other = store.capture({"scenario": "normal", "token": str(uuid.uuid4())})
    store.mark_capture(other)
    atomic_json(runner.paths(row)[0], {"orphan": True})
    atomic_json(runner.paths(row)[1], {"orphan": True})
    Runner(store, collector, runner.bridge, runner.evidence)
    assert not any(p.exists() for p in runner.paths(row))
    assert store.get("cases", other)["state"] == "failed"
    assert collector.controls == ["normal"]
    assert len(collector.calls) == 1


def test_rejected_generation_retains_latency_and_raw_output(system):
    identifier = captured(system)
    store, _, runner = system
    ready(runner)
    analysis_id = analyze(system, identifier, "model")
    runner.step()
    row = store.get("analyses", analysis_id)
    invalid = {**proposal(), "execute_patch": True}
    atomic_json(
        runner.paths(row)[1],
        {
            "id": analysis_id,
            "evidence_digest": row["evidence_digest"],
            "model": "cookllm",
            "state": "succeeded",
            "attempted": True,
            "output": json.dumps(invalid),
            "latency_ms": 8.6,
            "usage": {"input_tokens": 7, "output_tokens": 9},
        },
    )
    runner.step()
    done = store.get("analyses", analysis_id)
    assert done["state"] == "failed" and done["model_ms"] == 8.6
    assert "execute_patch" in done["raw_output"] and done["result"] is None
    assert done["usage"] == {"input_tokens": 7, "output_tokens": 9}


def test_http_boundaries_export_no_repair_action(tmp_path):
    collector = FakeCollector()
    app = create_app(
        tmp_path / "state.sqlite",
        tmp_path / "bridge",
        tmp_path / "evidence",
        collector=collector,
        background=False,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        value = {"scenario": "sqlite_lock", "token": str(uuid.uuid4())}
        identifier = client.post("/api/cases", json=value).json()["id"]
        app.state.runner.step()
        assert client.get(f"/api/cases/{identifier}").json()["state"] == "complete"
        body = {
            "case_id": identifier,
            "policy": "baseline",
            "probe": "normal",
            "token": str(uuid.uuid4()),
        }
        result = client.post("/api/analyses", json=body)
        assert result.status_code == 202
        app.state.runner.step()
        detail = client.get("/api/analyses/" + result.json()["id"]).json()
        assert detail["result"]["status"] == "requires_human_approval"
        exported = client.get(f"/api/cases/{identifier}/export").json()
        assert exported["repairs_executable"] is False
        assert client.post("/api/repairs/apply", json={"approved": True}).status_code == 404
        assert client.post("/api/analyses", json={**body, "policy": "model"}).status_code == 503
        assert (
            client.post(
                "/api/cases", json=value, headers={"Origin": "https://invalid.example"}
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "evil.invalid"}).status_code == 400
        assert client.post("/api/cases", content=b"x" * 70_000).status_code == 413
        assert client.get("/api/state").json()["actual_cost_usd"] is None


def test_model_reservations_survive_failures_and_bound_pending(system):
    identifier = captured(system)
    store, _, runner = system
    ready(runner)
    for number in range(10):
        active = [r for r in store.rows("analyses") if r["state"] == "awaiting"]
        if len(active) == 3:
            with pytest.raises(ValueError, match="bound"):
                analyze(system, identifier, "model")
            for row in active:
                runner.paths(row)[0].unlink()
            runner.step()
        token = str(uuid.uuid4())
        row_id = analyze(system, identifier, "model", token=token)
        assert analyze(system, identifier, "model", token=token) == row_id
        runner.step()
    with pytest.raises(ValueError, match="bound"):
        analyze(system, identifier, "model")
    assert len([r for r in store.rows("analyses") if r["policy"] == "model"]) == 10
