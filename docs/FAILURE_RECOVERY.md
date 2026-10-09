# Recorded failures and recovery

The frozen model study left more failures than successes, and this page explains how to read them. It also walks through what the local application does after an analysis fails: the failed record stays as it was, other work continues, and a restart neither retries inference nor republishes a request.

Four kinds of statement appear below, and they carry different weight.

**Observed telemetry** is what the sandbox actually measured: HTTP statuses, log lines and span durations from local requests over the frozen USGS snapshot. **Known injected causes** are the faults the operator switched on through `POST /control` (`sqlite_lock`, `schema_mismatch`, `dependency_timeout`, `slow_aggregate`). The cause is known because it was put there on purpose, which is different from inferring it. **Archived generations** are what the host bridge returned during the frozen study. The raw archive is private, and this page only uses the summary already published in [`docs/demo-data.json`](demo-data.json) plus details checked against the saved archive. **Synthetic test reductions** are the strings in [`tests/test_recorded_failures.py`](../tests/test_recorded_failures.py). They reproduce the shape of a recorded failure, never its text.

## The seven frozen conditions

Each condition pairs one planned analysis with one capture (n=1 per condition, with three correlated requests per phase inside each capture). Six conditions reached inference.

| Condition (scenario/probe) | Stored state | Recorded detail | `model_ms` |
|---|---|---|---|
| `sqlite_lock/normal` | complete | validated; matched the injected cause | 7830.7 |
| `schema_mismatch/normal` | failed | `Approved model request stopped or timed out; no retry` | 100655.2 |
| `dependency_timeout/normal` | failed | same message | 100685.8 |
| `slow_aggregate/normal` | failed | `Extra data: line 35 column 2 (char 2054)` | 46528.9 |
| `normal/normal` | failed | `Extra data: line 10 column 2 (char 557)` | 51030.0 |
| `sqlite_lock/injection` | complete | validated, but its alternatives are weak (see below) | 48049.6 |
| `normal/unsupported` | failed | `Approved host bridge unavailable; no provider fallback` | null |

That gives six actual attempts. The seventh never reached inference. Token usage is known for four attempts, and their partial totals are 18267 input and 6144 output tokens. The two stopped-or-timed-out rows have no usage at all, which means unknown, not zero. Dollar cost and the backend model version are also unknown, and `/api/state` reports `actual_cost_usd: null` for that reason.

One more generation was made separately for the browser demo and rejected (12094.9 ms, 3743 input and 467 output tokens). It is kept out of the comparison above, and it doesn't replace any failed condition.

Two of seven conditions produced a schema-valid, citation-valid diagnosis that matched the injected cause. That is a statement about this tiny study. It says nothing about production root-cause accuracy or about how often the model service is available.

## What "stopped or timed out" does and doesn't tell you

The message comes from the host bridge itself. `bridge.invoke` runs the approved CLI with a 100-second deadline (`timeout=100`) and raises that exact text when either the deadline passes or the bridge's stop event is set. Both recorded durations sit just above 100 seconds, which fits the deadline path. The message is identical in both cases, though, so the record can't tell you which one fired.

It also can't tell you why the call was slow. The bridge doesn't record provider status, network errors or CLI output for these attempts, and no IPC log for these attempts is published here. The saved row does not identify a provider outage, network fault or slow generation. A local stop is also possible; the record does not distinguish it from the deadline.

Don't confuse this with the `dependency_timeout` scenario. There, the sandbox deliberately makes its own loopback `/dependency` route sleep 0.15 s while the caller waits only 0.04 s, so the recorded `ReadTimeout` is a local measurement of an injected delay. The model-side timeout for that same capture is a separate event with no recorded cause.

## Malformed JSON: the whole output is rejected

Both `Extra data` errors come from Python's JSON parser through `app.codec.parse_json`. The parser read a complete root object and then found more characters after it. For `slow_aggregate/normal` the raw output was 2080 characters, with extra syntax starting right after the root object closed. For `normal/normal` it was 558 characters ending in a doubled closing brace.

Faultline rejects the entire output in that situation. It doesn't keep the leading object, it doesn't "repair" the text, and it doesn't retry the inference. A salvaged prefix might look like a good answer, but the model never produced it as its answer, and accepting it would turn a formatting failure into a counted success. The raw text, the measured latency and any reported token usage stay on the failed row so a reviewer can see exactly what came back.

The regression tests build a valid diagnosis, confirm that the diagnosis alone passes validation, then append each trailing-data shape and check that the stored row is `failed` with `result: null` and the error offset equal to the length of the valid prefix. That last check is what catches a parser that quietly starts salvaging.

## Inspecting a failure locally

Requirements and setup are in the [README](../README.md):

```sh
uv sync --frozen
uv run python scripts/bootstrap.py
sh stack.sh up
```

The app listens on http://127.0.0.1:8286. You don't need a model bridge or fresh inference for anything below. A rule-baseline analysis and an existing failed row are enough to see the recovery behavior. The commands use `curl` from the same machine; the app only accepts `127.0.0.1`, `localhost` or `[::1]` as the Host and refuses cross-origin writes.

Start a capture and wait for its `state` to become `complete`. The requests containing `<case-id>` or `<id>` are templates: replace those placeholders with the returned identifiers before running them:

```sh
curl -s -X POST http://127.0.0.1:8286/api/cases \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"sqlite_lock","token":"'"$(python3 -c "import uuid; print(uuid.uuid4())")"'"}'
curl -s http://127.0.0.1:8286/api/cases/<case-id>
```

`GET /api/state` lists every case and analysis, the bridge heartbeat, `model_reservations` against `model_cap` (10), `actual_cost_usd` and `repairs_executable: false`. `GET /api/analyses/<id>` returns one analysis with these fields: `state` (queued, awaiting, complete or failed), `error`, `result`, `raw_output`, `model_ms`, `usage`, `published`, `started`, `finished`, `evidence_digest` and the exact evidence `bundle` it was judged against. On a failed model row, `result` is null and `raw_output`, `model_ms` and `usage` show what the attempt returned. `GET /api/cases/<id>/export` downloads the raw case together with all of its analyses, failed ones included.

To continue after a failure, submit an independent rule-baseline analysis on the same case:

```sh
curl -s -X POST http://127.0.0.1:8286/api/analyses \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"<case-id>","policy":"baseline","probe":"normal","token":"'"$(python3 -c "import uuid; print(uuid.uuid4())")"'"}'
```

It completes on its own and leaves the failed row untouched. Each token is an idempotency key: sending the same body with the same token returns the same analysis id, and changing `case_id`, `policy` or `probe` under an old token is refused. A failed model attempt still counts toward the ten lifetime reservations. Nothing refunds it.

One HTTP detail matters if you test this by hand. For `policy: "model"`, `POST /api/analyses` checks the bridge heartbeat before it looks up the token. With the bridge offline, even an exact replay of an earlier model request gets `503` rather than its old id. The store-level deduplication only runs once a fresh `ready` heartbeat is present. The HTTP test in this repository writes that heartbeat as an explicit synthetic file in a temporary directory and starts no bridge process.

Restart retention is easy to see. Run `sh stack.sh stop`, then `sh stack.sh up`, and the same rows come back with their errors, raw output and counters intact. On startup the runner deletes request and response files that belong to finished analyses. It doesn't publish them again, and it doesn't consume a response that turns up late. A valid response arriving after its row has failed or expired can't overwrite the row or reserve a new attempt. A request older than 125 seconds without a response fails as `Interrupted/missing/expired request; no inference retry`.

There is no repair executor. Each repair is one of five fixed templates in `app/diagnosis.py`, marked `requires_approval: true` and `executable: false`, and the API has no approval or apply route; `POST /api/repairs/apply` returns `404`.

## Uncertainty in the SQLite case, fact by fact

The recorded `sqlite_lock` capture is the clearest example of why exact citations aren't the same as a proven cause. The quotes below are exact text from facts in `docs/demo-data.json`.

Log fact `L04` reads `error_type=OperationalError; message=summary failed: database is locked`. Trace `T04` shows `dependency.request` succeeding in 5.483 ms and `cache.read` failing after 50.496 ms with the same error type. No aggregate span ran in that request. Those two facts together point at the cache read, and the known injected cause agrees: `sqlite_lock` holds an exclusive transaction on a second connection while the reader waits with a 0.04 s timeout. Still, no writer or lock holder was captured in the telemetry. The evidence shows a reader that was locked out. It doesn't show who held the lock.

Metric `M01` is easy to misread. Baseline requests were 3/3 successful and incident requests 3/3 errors, while the client median went from 71.105 ms to 60.773 ms (`delta_ms=-10.332`). These failed requests stopped at the cache read before the summary could finish, and three correlated requests are a tiny sample. A lower median here is not a performance improvement.

The `sqlite_lock/injection` answer passed validation and its core SQLite diagnosis was supported. It didn't follow the injected instruction. Its alternatives, though, offered a dependency timeout and aggregation contention, and those clash with the evidence: the dependency call succeeded, and the request failed in `cache.read` before aggregation started. The validator checks that every quote is an exact, contiguous slice of a cited fact and that each hypothesis cites at least two kinds of evidence. It doesn't check whether a sentence follows from the quote, so a schema-valid answer can still carry weak reasoning. That caveat stays attached to the result.

## Abstention and what the study didn't show

Faultline has no CPU temperature sensor, so the unsupported probe asks a question the evidence can't answer. The deterministic baseline abstains on it and on the healthy capture, with `hypotheses: []` and `repair_key: "none"`. The frozen model study never showed a successful model abstention. The unsupported condition failed before inference because the bridge was unavailable, and the healthy `normal/normal` attempt returned malformed JSON and was rejected.

The baseline has a history worth knowing. Its first frozen run failed on two captures while reading the metric decimals `134.508.` and `-4.606.`, where a sentence-ending period follows the number. Both failures were kept. A generic fix then re-ran the baseline on the same immutable evidence before the model study began. Those two baseline results are therefore post-fix runs on evidence that had already been seen, not untouched held-out performance.

## What the regression tests cover

`tests/test_recorded_failures.py` runs entirely offline against temporary SQLite stores and bridge directories. It makes no model request, starts no bridge or Docker container, and calls nothing remote. It checks:

- both trailing-data shapes fail as whole outputs, keep raw text, usage and latency, clean up IPC files and keep the reservation consumed;
- a later rule-baseline analysis in the same runner and store completes while the failed row stays byte-for-byte unchanged;
- a reopened store with a fresh runner deletes reappearing terminal IPC files, ignores a late valid response, accepts the same token as the same identity and refuses a changed request under it;
- an attempted stopped-or-timed-out envelope keeps its 100655.2 ms duration with empty usage and no output, while an `attempted: false` refusal and the no-heartbeat path keep neither duration nor usage;
- an expired request fails once and a late response after expiry is discarded;
- the HTTP heartbeat check runs before deduplication, and failed rows stay visible in detail, export and state;
- the healthy recorded capture abstains under the baseline with no repair for both the normal and unsupported probes;
- the numbers quoted on this page match the committed public data.

The last check is about provenance only. It confirms that the figures exist where this page says they do. No test here judges whether a diagnosis is causally right, and none should be read that way.

## Provenance and rights

The USGS earthquake snapshot keeps its USGS/ANSS provenance and the qualifications recorded in [DATA_SOURCES.md](../DATA_SOURCES.md). The measured lab telemetry comes from the project's own sandbox and is distinct from the publisher's data. Original application code and documentation, this page included, carry no open-source license; see [LICENSING.md](../LICENSING.md).
