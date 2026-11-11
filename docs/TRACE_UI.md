# Tracerator UI

Browser pages and the HTTP surface. Procedures live in [How to run the UI](how-to/run-the-ui.md).

Default origin: http://localhost:8008

Runs persist in SQLite (`artifacts/tracerator.db`, override `TRACERATOR_DB`). Trace files live under `artifacts/runs/{id}/` (`TRACERATOR_RUNS_ROOT`).

## Pages

| Path | Contents |
|------|----------|
| `/` | Workspace. List, search, filter runs. **New run** creates a draft. |
| `/runs/<id>/view` | Run detail. Alias `/run/<id>`. |
| `/traces` | Trace cards and per-trace profiles. `/profiles` redirects here. |
| `/traces/<id>` | Full profile for one trace. |
| `/workflows` | Start a run from a trace. The canvas comes later. |
| `/compare` | Up to five manifests or replay results, or saved runs. HTML/PNG export. |
| `/study` | Trace-driven sizing study. |

Run detail stepper:

1. Generate — baseline, scenario, scale, reuse bias, ISL profile. Writes `trace.jsonl` and `manifest.json`.
2. Validate — AIPerf `analyze-trace`.
3. Replay / Download — subset replay, zip download, or upload of `replay_result.json`.
4. Inspect — Overview, Workload, Timeline, Prefix, Waterfall, Events, Results, Data.
5. Narrative — auto-built markdown.

Golden source controls appear when the baseline is **Turn-1 warm / Turn-2 resume**. See [How to generate an external-KV golden trace](how-to/generate-an-external-kv-golden-trace.md).

HTTP paths: [HTTP API](reference/http-api.md).

Generated traces are simulations. They preserve selected source characteristics. They are not exact production profiles.
