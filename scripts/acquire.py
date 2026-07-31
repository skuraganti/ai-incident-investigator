"""Validate the retained versioned USGS capture; bounded reacquisition fails on revisions."""

import hashlib
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text())
    path = ROOT / "data/raw/usgs-all-day.geojson"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Snapshot symlink refused")
    bundled = ROOT / "examples/source-snapshot/usgs-all-day.geojson"
    if not path.exists() and bundled.is_file():
        content = bundled.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
            raise ValueError("Bundled source fingerprint changed")
        path.write_bytes(content)
    if not path.exists():
        content, started = bytearray(), time.monotonic()
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", manifest["source_url"]) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(65536):
                    content.extend(chunk)
                    if len(content) > 4_000_000 or time.monotonic() - started > 20:
                        raise ValueError("Source bound exceeded")
        if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
            raise ValueError(
                "Feed revised since this capture; restore the retained snapshot. "
                "A new dataset version requires explicit repinning, not silent replacement."
            )
        path.write_bytes(content)
    content = path.read_bytes()
    if (
        len(content) != manifest["bytes"]
        or hashlib.sha256(content).hexdigest() != manifest["sha256"]
    ):
        raise ValueError("Snapshot bytes changed")
    raw = json.loads(content)
    if len(raw["features"]) != manifest["features"]:
        raise ValueError("Source record count changed")
    (ROOT / "data/prepared").mkdir(parents=True, exist_ok=True)
    (ROOT / "data/prepared/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "source_sha256": manifest["sha256"],
                "bytes": len(content),
                "features": len(raw["features"]),
                "kind": "retained frozen original snapshot",
            }
        )
    )


if __name__ == "__main__":
    main()
