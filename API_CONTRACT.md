# Local investigator API

| Route | Behavior |
|---|---|
| `GET /health` | Journal+runner liveness;503 if runner stopped |
| `GET /api/state` | Case summaries, immutable analyses, model reservation/host-heartbeat counters, no actual dollar-cost estimate |
| `POST /api/cases` | Exact `{scenario,token}`; canonical UUID idempotency;202 ID. Allowed normal/sqlite_lock/schema_mismatch/dependency_timeout/slow_aggregate;40lifetime cases/2pending |
| `GET /api/cases/{uuid}` | Complete raw observations, immutable evidence+fingerprint, explicit operator scenario outside model bundle |
| `POST /api/analyses` | Exact `{case_id,policy,probe,token}`; baseline/model, normal/injection/unsupported fixed probes; completed case required;160records/10lifetime model reservations/3pending; unavailable bridge503, no fallback |
| `GET /api/analyses/{uuid}` | Validated advisory output or visible failure/raw rejected text, metadata and exact evidence bundle |
| `GET /api/cases/{uuid}/export` | Local JSON with raw case, analyses, approval-required/nonexecutable flags |

No approval, repair-apply, arbitrary prompt, remote URL, file import, shell or patch endpoint. Unknown routes404 regardless of caller-supplied approval fields. Model output selects only allowlisted proposal templates; it cannot mutate source/control/runtime.

External binding127.0.0.1:8286; Host allowlist/same-origin mutation checks/CSP/pre-parser64KiB body bound. Other local processes with access to this Mac are not authenticated. Private sandbox service exposes only source/cache health, bounded experiment control, summary/dependency and capture-filtered telemetry on its project network. It has no published port; control is for declared lab experiments.

Capture state queued/capturing/complete/failed; analysis queued/awaiting/complete/failed. A successful validation means references/shape/repair guard passed, not a proven production root cause. Failed outputs stay visible. Idempotency tokens reject changed reuse; attempt counters persist across failures/restarts. Interrupted captures fail without request replay and attempt bounded control cleanup. Source/cache mismatch is never replaced silently.
