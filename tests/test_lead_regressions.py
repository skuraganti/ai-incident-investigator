import uuid

import pytest

from app.diagnosis import baseline, validate_diagnosis


def bundle(delta):
    return {
        "id": str(uuid.uuid4()),
        "source_sha256": "a" * 64,
        "captured_at": "fixture",
        "dataset": {},
        "facts": [
            {
                "id": "M01",
                "kind": "metric",
                "text": f"Measured baseline median_ms=3; incident median_ms=124; delta_ms={delta}. "
                "A single local capture.",
            },
            {
                "id": "T04",
                "kind": "trace",
                "text": 'Observed trace=fake; spans=[{"duration_ms":120.3,'
                '"name":"aggregate","status":"ok"}]',
            },
        ],
    }


def test_relative_slow_span_rule_and_sentence_punctuation():
    b = bundle(121.3)
    proposal = baseline(b)
    assert proposal["hypotheses"][0]["kind"] == "aggregate_latency"
    assert validate_diagnosis(b, proposal)["supports_verified"]
    assert baseline(bundle(-4.606))["abstain"]


def test_deep_dict_or_cycles_fail_before_recursive_canary_scan():
    b = bundle(121.3)
    output = baseline(b)
    value = output
    for _ in range(1200):
        child = {}
        value["nested"] = child
        value = child
    with pytest.raises(ValueError):
        validate_diagnosis(b, output)
    other = baseline(b)
    other["cycle"] = other
    with pytest.raises(ValueError):
        validate_diagnosis(b, other)


def test_fixed_heartbeat_frame_retains_strict_freshness(tmp_path):
    import time

    from app.io import atomic_json, read_json
    from app.runner import heartbeat

    path = tmp_path / "heartbeat.json"
    for count, status in [(7, "ready"), (10, "busy")]:
        frame = {"at": time.time(), "model": "cookllm", "status": status, "calls": count}
        atomic_json(path, frame, pad_to=256)
        assert path.stat().st_size == 256
        assert read_json(path, 4096) == frame
        assert heartbeat(tmp_path)["available"]
    before = path.read_bytes()
    with pytest.raises(ValueError):
        atomic_json(path, frame, pad_to=8)
    assert path.read_bytes() == before
    frame["status"] = "stopped"
    atomic_json(path, frame, pad_to=256)
    assert path.stat().st_size == 256
    assert not heartbeat(tmp_path)["available"]
    frame["status"] = "ready"
    frame["model"] = "unapproved-route"
    atomic_json(path, frame, pad_to=256)
    assert not heartbeat(tmp_path)["available"]
    frame["model"] = "cookllm"
    frame["at"] = time.time() - 60
    atomic_json(path, frame, pad_to=256)
    assert not heartbeat(tmp_path)["available"]
    path.write_bytes(b'{"at": 123' + b" " * 246)
    assert not heartbeat(tmp_path)["available"]
