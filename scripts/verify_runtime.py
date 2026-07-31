"""Capture the frozen lab cases, then optionally issue the seven one-shot model checks."""

import argparse
import hashlib
import json
import platform
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/verification/runtime-results.json"


def wait(client, kind, identifier):
    end = time.monotonic() + 140
    while time.monotonic() < end:
        value = client.get(f"/api/{kind}/{identifier}").json()
        if value.get("state") in {"complete", "failed"}:
            return value
        time.sleep(0.4)
    raise TimeoutError("Existing operation pending; inspect it instead of repeating it")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", action="store_true", help="Issue only missing frozen model identities"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    protocol = json.loads((ROOT / "evals/PROTOCOL.json").read_text())
    report = (
        json.loads(output.read_text())
        if output.exists()
        else {
            "started_at": datetime.now(UTC).isoformat(),
            "protocol": protocol,
            "hardware": platform.platform(),
            "python_host": platform.python_version(),
            "actual_cost_usd": None,
            "cases": {},
            "baselines": {},
            "models": {},
            "tokens": {},
            "code_sha256": {
                f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
                for f in ["sandbox_app/main.py", "app/evidence.py", "app/diagnosis.py", "bridge.py"]
            },
        }
    )

    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")

    with httpx.Client(base_url="http://127.0.0.1:8286", timeout=8, trust_env=False) as client:
        client.get("/health").raise_for_status()
        if report["protocol"] != protocol:
            raise ValueError("Saved report protocol changed; choose a separate --output")
        for saved in report["cases"].values():
            response = client.get("/api/cases/" + saved["id"])
            response.raise_for_status()
            current = response.json()
            if current.get("evidence_digest") != saved.get("evidence_digest"):
                raise ValueError(
                    "Saved report belongs to another runtime; choose a separate --output"
                )
        for spec in protocol["captures"]:
            mode = spec["scenario"]
            if mode not in report["cases"]:
                token = report["tokens"].setdefault("capture/" + mode, str(uuid.uuid4()))
                save()
                response = client.post("/api/cases", json={"scenario": mode, "token": token})
                response.raise_for_status()
                result = wait(client, "cases", response.json()["id"])
                report["cases"][mode] = result
                save()
                if result["state"] != "complete":
                    raise ValueError(
                        f"Capture failed; preserve and inspect: {mode}: {result['error']}"
                    )
                observed = result["raw"]["outcomes"]
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "state": result["state"],
                            "statuses": [o["status"] for o in observed],
                            "client_ms": [o["client_ms"] for o in observed],
                        }
                    ),
                    flush=True,
                )
            if mode not in report["baselines"]:
                token = report["tokens"].setdefault("baseline/" + mode, str(uuid.uuid4()))
                save()
                response = client.post(
                    "/api/analyses",
                    json={
                        "case_id": report["cases"][mode]["id"],
                        "policy": "baseline",
                        "probe": "normal",
                        "token": token,
                    },
                )
                response.raise_for_status()
                report["baselines"][mode] = wait(client, "analyses", response.json()["id"])
                save()
        if args.models:
            for spec in protocol["model_plan"]:
                key = spec["scenario"] + "/" + spec["probe"]
                if key in report["models"]:
                    continue  # Completed or failed attempts are never retried.
                token = report["tokens"].setdefault("model/" + key, str(uuid.uuid4()))
                save()
                response = client.post(
                    "/api/analyses",
                    json={
                        "case_id": report["cases"][spec["scenario"]]["id"],
                        "policy": "model",
                        "probe": spec["probe"],
                        "token": token,
                    },
                )
                response.raise_for_status()
                value = wait(client, "analyses", response.json()["id"])
                report["models"][key] = value
                save()
                result = value.get("result") or {}
                print(
                    json.dumps(
                        {
                            "model_case": key,
                            "state": value["state"],
                            "abstain": result.get("abstain"),
                            "kind": [h["kind"] for h in result.get("hypotheses", [])],
                            "error": value.get("error"),
                            "model_ms": value.get("model_ms"),
                        }
                    ),
                    flush=True,
                )
        report["last_verified_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    main()
