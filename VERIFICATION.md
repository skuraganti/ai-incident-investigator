# Release verification — 2026-10-16

Theme 2026; actual development and release preparation 2026-10-16. This report records checks on the sanitized public source copy before its initial commit. Deployment is checked separately after pushing, without backdated commits. Passing these bounded checks does not guarantee every future feed, model response or workload.

| Check | Observed result |
|---|---|
| Clean source environment | `uv sync --frozen`, project-specific `scripts/bootstrap.py`: passed |
| Native suite | **34 passed** with Python 3.14.8; FastAPI/Starlette emitted a TestClient deprecation warning |
| Lint | `uv run ruff check .`: passed; pinned upstream corpus files excluded from project style rules |
| Docker | Digest-pinned Python 3.13 image built; startup, HTTP health and application page passed |
| Workflow | Actual sandbox SQLite contention captured over HTTP; deterministic evidence-backed baseline validated |
| Persistence | Restarted app container; workflow state retained |
| Browser | Isolated Chromium via agent-browser 0.38.1; 8 domain assertions passed; native input/click controls passed |
| Error state | Fixture temporarily unavailable: visible recoverable error; restored fixture loads after reload |
| Responsive layout | 1440×1000 and 390×844 inspected; 390px document width with no page overflow |
| Accessibility | axe-core 4.12.1, WCAG 2 A/AA and 2.1 AA tags: **0 detected violations** |
| Privacy | Candidate-file Gitleaks scan passed; no private home paths, unrelated service IDs, credentials or mutable data included |
| Release inference | **0 model calls**; recorded original model studies remain distinct from release checks |

Browser scenarios: five cases and honest study outcome; correlated request spans inspectable; validated saved evidence and approval-only proposal; citation focuses exact fact; failed attempt stays visible; normal control labeled honestly; abstention proposes no repair; no repair execution control.

Some axe color checks on clipped scroll contents require manual review. Visible desktop/mobile views and the authored foreground/background palette were inspected; the minimum checked ink/muted/link ratio against the declared page/panel backgrounds was 4.94:1. This is a bounded audit, not a universal accessibility certification. Scroll regions are keyboard focusable and tables accompany charts/graphs.

## Corrections and retesting

Healthy controls are labeled correctly and abstentions show no repair proposal. The runtime verifier now requires live health and matching saved-case evidence before refreshing reports; its offline regression test passes. Two release-harness requests initially used invalid enum values; corrected requests passed. Original failed/timeout model evaluations were preserved without reruns or relabeling.

## Recorded findings and limitations

Fault causes are deliberately injected; observed errors, HTTP results and durations are actual local measurements. The frozen model study validated2/7conditions; two timed out, two returned malformed JSON, and one failed before inference. A separate browser generation was also rejected. The injection answer had weak alternatives despite valid quotes. No production-RCA or repair-efficacy claim is made, and there is no repair executor.

Native tests use explicit fixtures, original-source boundary checks and simulated model responses; they do not perform live inference. See [PUBLIC_DEMO_DATA.md](PUBLIC_DEMO_DATA.md) for the curated Pages fixture and checksum, [DATA_SOURCES.md](DATA_SOURCES.md) for publisher/version/rights and [UPSTREAM.md](UPSTREAM.md) for attribution. Original model studies have small samples and cannot establish general performance or costs.

## Reproduce

```sh
uv sync --frozen
uv run python scripts/bootstrap.py
uv run pytest
uv run ruff check .
sh stack.sh up
```

Local URL: http://127.0.0.1:8286. The container limit is 768 MiB combined across the app and sandbox, plus Docker and host process overhead. Tested one portfolio stack at a time on an ARM Mac with 8 GiB host RAM and a 2 GiB Docker VM. No GPU. Defaults use independent named volumes and localhost bindings. Stack stop retains state; no unrelated services are managed.

For Pages: `python3 -m http.server 8008 --bind 127.0.0.1 --directory docs`. With optional agent-browser 0.38.1 installed, run `sh scripts/check-pages.sh`; assertions are in `tests/pages-smoke.js`. No browser automation package is shipped to Pages.
