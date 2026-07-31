"""Serial experiment capture and analysis orchestration; no repair executor exists."""

import math
import time
from pathlib import Path

from app.codec import digest
from app.diagnosis import baseline, validate_diagnosis
from app.evidence import validate_bundle, with_probe
from app.io import atomic_json, read_json


def heartbeat(directory):
    try:
        value = read_json(Path(directory) / "heartbeat.json", 4096)
        at, count = value.get("at"), value.get("calls")
        if (
            type(at) not in {int, float}
            or not 0 <= at <= 1e10
            or not math.isfinite(at)
            or not -1 <= time.time() - at <= 6
            or value.get("model") != "cookllm"
            or value.get("status") not in {"ready", "busy"}
            or type(count) is not int
            or not 0 <= count <= 10
        ):
            raise ValueError("Stale or invalid heartbeat")
        return {"available": True, "calls": count, "status": value["status"]}
    except (OSError, ValueError, TypeError):
        return {"available": False, "calls": None, "status": "unavailable"}


class Runner:
    def __init__(self, store, collector, bridge, evidence):
        self.store, self.collector = store, collector
        self.bridge, self.evidence = Path(bridge), Path(evidence)
        for path in [self.bridge, self.evidence]:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        for row in store.rows("cases"):
            if row["state"] == "capturing":
                try:
                    collector.control("normal")
                    cleanup = "experiment control reset to normal"
                except Exception:
                    cleanup = "experiment cleanup failed; inspect sandbox before new work"
                store.finish_capture(
                    row["id"],
                    error="Interrupted capture; " + cleanup + "; requests were not repeated",
                )
        for row in store.rows("analyses"):
            if row["published"] and row["state"] in {"complete", "failed"}:
                self.cleanup(row)

    def bundle(self, row):
        case = self.store.get("cases", row["case_id"])
        base = validate_bundle(case["evidence"])
        if digest(base) != case["evidence_digest"]:
            raise ValueError("Stored evidence changed")
        bundle = with_probe(base, row["probe"])
        if digest(bundle) != row["evidence_digest"]:
            raise ValueError("Analysis evidence changed")
        return bundle

    def paths(self, row):
        return [
            self.bridge / (row["id"] + suffix) for suffix in [".request.json", ".response.json"]
        ]

    def cleanup(self, row):
        for path in self.paths(row):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # Never recurse into a substituted directory.

    def consume(self, row):
        request, response = self.paths(row)
        if response.exists() or response.is_symlink():
            model_ms = None
            raw_output = None
            observed_usage = None
            try:
                value = read_json(response, 80_000)
                if (
                    value.get("id") != row["id"]
                    or value.get("evidence_digest") != row["evidence_digest"]
                    or value.get("model") != "cookllm"
                ):
                    raise ValueError("Response identity mismatch")
                elapsed, attempted, usage = (
                    value.get("latency_ms"),
                    value.get("attempted"),
                    value.get("usage", {}),
                )
                if (
                    type(elapsed) not in {int, float}
                    or not 0 <= elapsed <= 110_000
                    or not math.isfinite(elapsed)
                    or type(attempted) is not bool
                    or not isinstance(usage, dict)
                    or any(type(n) is not int or not 0 <= n <= 10_000_000 for n in usage.values())
                ):
                    raise ValueError("Invalid observed model metadata")
                if attempted:
                    model_ms = elapsed
                    observed_usage = usage
                if value.get("state") != "succeeded" or not attempted:
                    raise ValueError(str(value.get("error", "Model attempt failed"))[:240])
                raw_output = value.get("output")
                result = validate_diagnosis(self.bundle(row), value.get("output"))
                self.store.finish(
                    row["id"], result=result, usage=usage, model_ms=model_ms, raw_output=raw_output
                )
            except (OSError, ValueError, TypeError) as exc:
                self.store.finish(
                    row["id"],
                    error=str(exc) or "Invalid model response",
                    model_ms=model_ms,
                    raw_output=raw_output,
                    usage=observed_usage,
                )
            self.cleanup(row)
            return
        if (
            not request.exists()
            or request.is_symlink()
            or time.time() - (row["started"] or row["created"]) > 125
        ):
            self.store.finish(
                row["id"], error="Interrupted/missing/expired request; no inference retry"
            )
            self.cleanup(row)
            return
        try:
            value = read_json(request, 4096)
            if (
                value.get("id") != row["id"]
                or value.get("case_id") != row["case_id"]
                or value.get("evidence_digest") != row["evidence_digest"]
                or value.get("probe") != row["probe"]
            ):
                raise ValueError("Pending request changed")
        except (OSError, ValueError, TypeError) as exc:
            self.store.finish(row["id"], error=str(exc) or "Invalid request")
            self.cleanup(row)

    def step(self):
        for row in reversed(self.store.rows("analyses")):
            if row["state"] == "awaiting":
                self.consume(row)
            elif row["state"] == "queued":
                try:
                    bundle = self.bundle(row)
                    if row["policy"] == "baseline":
                        proposal = (
                            baseline(bundle)
                            if row["probe"] != "unsupported"
                            else {
                                "abstain": True,
                                "summary": "CPU temperature was not measured.",
                                "hypotheses": [],
                                "uncertainty": ["No physical sensor evidence supplied."],
                                "repair_key": "none",
                            }
                        )
                        self.store.finish(row["id"], result=validate_diagnosis(bundle, proposal))
                    else:
                        if not heartbeat(self.bridge)["available"]:
                            raise ValueError(
                                "Approved host bridge unavailable; no provider fallback"
                            )
                        case = self.store.get("cases", row["case_id"])
                        path = self.evidence / (case["id"] + ".json")
                        if not path.exists():
                            atomic_json(path, case["evidence"])
                        elif digest(read_json(path, 60_000)) != case["evidence_digest"]:
                            raise ValueError("Published evidence file changed")
                        self.store.published(row["id"])
                        atomic_json(
                            self.paths(row)[0],
                            {
                                "id": row["id"],
                                "case_id": row["case_id"],
                                "case_digest": case["evidence_digest"],
                                "evidence_digest": row["evidence_digest"],
                                "probe": row["probe"],
                                "expires_at": time.time() + 120,
                            },
                        )
                except Exception as exc:
                    self.store.finish(row["id"], error=str(exc)[:300] or "Analysis failed")
        pending = [r for r in reversed(self.store.rows("cases")) if r["state"] == "queued"]
        if pending:
            row = pending[0]
            self.store.mark_capture(row["id"])
            try:
                captured = self.collector.capture(row["id"], row["scenario"])
                atomic_json(self.evidence / (row["id"] + ".json"), captured["bundle"])
                self.store.finish_capture(row["id"], captured=captured)
            except Exception as exc:
                self.store.finish_capture(row["id"], error=str(exc)[:300] or "Capture failed")

    def serve(self, stop):
        while not stop.wait(0.25):
            self.step()
