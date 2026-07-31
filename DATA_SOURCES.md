# Faultline source decision and acquisition

Actual development/retrieval2026-10-16. Source metadata, exact URL, format version, retrieval/generated times, bytes and SHA256: [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json). Source bodies are untrusted input. No downloaded scripts/code execute.

| Candidate | Original source / permissions | Decision |
|---|---|---|
| USGS event snapshot + our measured local service telemetry | [USGS GeoJSON v1.0](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php); USGS-produced data in U.S. public domain with credit requested/third-party exceptions | Chosen: genuine input supports a useful quake-briefing API. Our own instrumented service yields aligned request/log/metric/span evidence and explicit experiment ground truth within a small resource budget. |
| Loghub HDFS | [Original Loghub HDFS documentation](https://raw.githubusercontent.com/logpai/loghub/master/HDFS/README.md), [license](https://raw.githubusercontent.com/logpai/loghub/master/LICENSE) | Research/academic use with attribution and retained notice, **not CC BY**. HDFS_v1 uses private-cloud benchmark workloads and handcrafted anomaly/block labels; HDFS_v3 includes injected faults. Those labels do not establish exact diagnoses/repairs or aligned metrics/spans. Not acquired or installed. |
| OpenTelemetry Demo | [Official deployment requirements](https://opentelemetry.io/docs/demo/docker-deployment/), [Apache-2.0 repository](https://github.com/open-telemetry/opentelemetry-demo) | Documented6GB RAM,~3GB minimal and14GB disk exceed this2GB Docker VM. Strong full-service demo, but unnecessary runtime breadth here. These are published requirements, not locally measured resource use. No upstream app installed. |

Independent reviewer confirmed the source rationale and qualifications. This is a fit judgment, not a measured benchmark ranking. OpenTelemetry's aligned-signal design and standard trace/span identifiers inform the independent lightweight instrumented implementation; this is not OTel SDK/protocol compliance or a copied demo.

## Original input

`https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson`,112778bytes,157unique source features at acquisition:156earthquakes and1explosion. SHA256 `6ff063e5234d266158c27d9860faddc4449a14309227dc804882c3fbd7e08ee0`, generated_epoch_ms1792151400000. No feature is relabeled an earthquake. The summary reports event types and null/finite magnitudes separately. Original coordinates, depth, event/revision epochs, review status and strings remain in the raw capture; magnitude units/meaning are unchanged. Preview is bounded, not a different source dataset.

USGS programmatic feed/reuse policies: [feed schema](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php), [Copyrights and Credits](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits), [ANSS data policy](https://www.usgs.gov/media/files/anss-data-and-products-policy). Credit U.S. Geological Survey and contributing networks. The U.S. public-domain statement applies to USGS-produced information; third-party material/foreign rights exceptions are not silently generalized. No restricted account, credentials or bank/private data.

The original feed updates, but this project uses a **retained frozen snapshot**, not a live feed. `uv run python scripts/acquire.py` checks existing bytes and reconstructs the small preparation manifest. Missing-cache reacquisition is bounded4MB/20s, no redirect/proxy/cookies, and refuses changed bytes. The upstream URL does not provide content-addressed historical archives; if the local original is lost, exact bit reproduction requires restoring the retained file. A newer feed is a new version requiring deliberate repinning and new evaluations. Do not silently overwrite it or label an updated revision as the original capture.

Coverage limitations: one24h window, network-dependent geographic/magnitude reporting, automatic/reviewed revisions, non-earthquake events and nullable properties. Counts are not hazard or completeness estimates. No identities, resume/evidence context, private application logs or employer material enter the data.

## Observations and experiments

Each case makes3healthy baseline and3experiment HTTP requests. The sandbox consumes the exact original snapshot via its own SQLite cache. Actual HTTP status/elapsed time, real SQLite OperationalError/KeyError/httpx ReadTimeout, measured span durations, timestamps and UUID links are collected from our own service. Three requests in a case share conditions and are correlated; they are not independent production samples.

Causes are explicitly injected: short exclusive SQLite transaction/read contention; incompatible magnitude accessor; a delayed local dependency exceeding a real HTTP timeout; a0.12s artificial aggregation delay. The healthy control injects none. Workload and causes are experiments, even though errors and durations are genuinely observed. Do not call these naturally occurring production incidents, real upstream USGS outages or an external root-cause benchmark.

Raw records include capture/trace/span identities and dropped-record counts; SQLite journal retains at most200request records with persistent eviction count. The investigator copies a complete6record case into its independent immutable evidence record before later sandbox eviction. Evidence SHA256 and exact fact IDs bind model citations. Operator scenario/grading labels stay in the local case/evaluation metadata, outside model-visible evidence. Model sees only bounded measured facts/public source context. Synthetic injected-log instructions and unsupported CPU-temperature questions are separately labeled probes. No real secret or external repair action exists.

Frozen evaluation split/protocol: [evals/PROTOCOL.json](evals/PROTOCOL.json). Fault families are known to the implementation; held-out refers to newly captured runtime instances, not unseen families or training-data independence. Remote inference uses only approved cookllm; no raw private telemetry is imported.
