"""Archived results cannot masquerade as a fresh runtime verification."""

import json

import pytest

from scripts import verify_runtime


def test_existing_report_requires_live_health_before_refresh(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    protocol = {"captures": [], "model_plan": []}
    (tmp_path / "evals/PROTOCOL.json").write_text(json.dumps(protocol))
    output = tmp_path / "report.json"
    report = {
        "protocol": protocol,
        "cases": {},
        "baselines": {},
        "models": {},
        "tokens": {},
        "last_verified_at": "original timestamp",
    }
    output.write_text(json.dumps(report))
    original = output.read_bytes()
    calls = []

    class OfflineClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path):
            calls.append(path)
            raise RuntimeError("deliberately offline")

    monkeypatch.setattr(verify_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(verify_runtime, "OUTPUT", output)
    monkeypatch.setattr(verify_runtime.httpx, "Client", OfflineClient)
    monkeypatch.setattr("sys.argv", ["verify_runtime"])
    with pytest.raises(RuntimeError, match="offline"):
        verify_runtime.main()
    assert calls == ["/health"]
    assert output.read_bytes() == original
