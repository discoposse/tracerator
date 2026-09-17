# HTTP API

Flask app in `app.py`. UI origin is http://localhost:8008.

## Runs

| Method | Path | Purpose |
|--------|------|---------|
| GET, POST | `/runs` | List or create |
| GET, PATCH, DELETE | `/runs/<id>` | Read, rename, or delete |
| POST | `/runs/<id>/generate` | Generate and persist a trace |
| POST | `/runs/<id>/validate` | AIPerf analyze or replay |
| POST | `/runs/<id>/upload` | Attach a results file or `artifact_dir` JSON |
| GET | `/runs/<id>/download` | Zip bundle |
| GET | `/runs/<id>/inspect` | Enriched view-models |
| GET | `/runs/<id>/trace-card` | Trace card JSON |

## Sources and catalogs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/sources` | Discovered baselines |
| GET | `/sources/lightinferra-golden/options` | Golden suites, models, contexts, defaults |
| GET | `/sources/lightinferra-ddn-golden/options` | Deprecated alias of the golden options route |
| GET | `/trace-cards` | Built-in cards |

Legacy `/generate`, `/manifest`, and `/aiperf/*` routes remain for scripts.

The golden source id is `lightinferra-golden`. The generate API still accepts `lightinferra-ddn-golden` and stores the canonical id.
