# Public demonstration data

Curated input for the browser demonstration.

Five original sandbox captures, six measured HTTP outcomes and six matching telemetry records each. Failures are explicitly injected sandbox conditions, not production incidents. The real USGS source contains 157 events (156 earthquakes, one explosion). Only 2/7 original frozen conditions validated; failed/time-out attempts are retained. Raw output archives and capability tokens are excluded. Repairs are nonexecutable proposals requiring human approval.

Fixture: `docs/demo-data.json` · SHA-256 `3be3f5e2150ab8b9f716080f3e72ed101ad6d4db7410c4ef1723f46a690169a6` · 159,903 bytes.

Declared mode: Recorded real local HTTP/log/span observations over frozen USGS data; underlying fault causes explicitly injected

Top-level schema fields: `title`, `data_kind`, `cases`, `study_summary`, `source_url`, `limit`, `repository`

Original publisher URLs, revisions, retrieval methods, rights and caveats are in [DATA_SOURCES.md](DATA_SOURCES.md). Browser behavior is described in [docs/DEMO.md](docs/DEMO.md). Data and application dependencies are credited separately in [UPSTREAM.md](UPSTREAM.md).
