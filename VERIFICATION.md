# Release verification — 2026-10-16

Actual development and release preparation 2026-10-16. This report distinguishes initial release preparation from the maintenance checks below. Deployment is checked separately after publication. Passing these bounded checks does not guarantee every future feed, model response or workload.

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

Fault causes are deliberately injected; observed errors, HTTP results and durations are actual local measurements. The frozen model study validated 2/7 conditions; two were stopped or timed out, two returned malformed JSON, and one failed before inference. A separate browser generation was also rejected. The injection answer had weak alternatives despite valid quotes. No production-RCA or repair-efficacy claim is made, and there is no repair executor.

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

## Maintenance: failure retention and evidence selection

The [failure and recovery walkthrough](docs/FAILURE_RECOVERY.md) separates recorded model outcomes from synthetic response tests. It keeps the seven-condition result, six actual attempts, partial known token usage, unknown dollar cost and the separate browser rejection. A stop-or-timeout message does not identify a provider outage. The exact-quote check also does not validate the causal meaning of an alternative diagnosis.

The new offline regressions use temporary SQLite stores and explicitly simulated bridge envelopes. They check whole-output JSON rejection, retained raw output/usage/duration, consumed reservations, later independent baseline progress, restart cleanup, expired and late responses, token deduplication and the HTTP heartbeat caveat. No model process or live inference is involved.

During browser verification, opening a failed capture left the prior case’s facts in the evidence panel. The UI now clears the evidence when a case or analysis selection starts and displays an empty state when no bundle exists. Three Node built-in DOM-fixture tests check loading/failed transitions, delayed responses across case and analysis selections, and a rejected case request. Case changes also clear the old request controls, spans, raw data and metrics and hide the export link while loading. These are separate from the actual browser check below; Node is optional for this UI regression, not an application dependency.

A fresh isolated Docker stack built and started over the pinned 157-record USGS snapshot. Five captures and their rule-baseline analyses completed. SQLite/schema experiments each recorded three incident 500s, the dependency experiment three 503s, and the healthy capture six 200s. The first slow-aggregation capture was mixed: control statuses 503/200/200 (`ConnectTimeout` on the first), then three incident 200s. Aggregation spans measured 120.36–122.44 ms, and median client duration rose 86.246 to 240.379 ms. The rule scans both phases, so that control timeout took precedence over the aggregation evidence. It completed with a timeout hypothesis; it did not identify the deliberately injected slowdown. Here “baseline” means injection disabled, not guaranteed success. Resource contention is a possible explanation for variability, not a verified cause.

One additional slow capture failed with a recorded capture timeout. The verification client also timed out while reading it; a later read recovered the existing saved identity without repeating the capture. Both outcomes were retained, and no third capture was attempted. An independent unsupported-probe baseline on the completed healthy case then finished, abstained and proposed no repair. Its idempotent resubmission returned the same ID; the failed capture could not be analyzed (400), and the repair-apply route remained 404. Model reservations stayed 0. These observations demonstrate retained failure and continued work, not universal capture reliability. The original model study was not rerun.

**46 Python tests passed** with Python 3.14.8, and Ruff passed. The three Node DOM-fixture tests passed with Node 24. The existing TestClient deprecation warning remains. Docker was rebuilt with the UI correction and both services were recreated using their existing isolated volumes. Full API state, all six cases (including the failed capture), all six analyses and five complete-case exports matched exactly after restart; model reservations remained zero. The served UI script matched the reviewed source.

The real browser check first loaded SQLite evidence, then opened the failed capture and confirmed an empty evidence panel with investigation disabled. Opening the later healthy/unsupported baseline showed abstention and no repair. Citation navigation opened the exact recorded log. The verification stack was stopped with its volumes retained. No application-model requests were made. Saved model observations remain historical, and these current local captures are separate from them.
