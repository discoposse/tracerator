# Tracerator

Tracerator generates simulated traces from base patterns for replay and modeling.

## UI

The UI is at site/index.html (served by the app).

Run with Docker Compose:

```bash
docker compose up -d
```

Open http://localhost:8000.

In the UI:

- Select base trace pattern

- Adjust parameters with sliders (live preview updates estimates for requests, duration, cache hit, concurrency)

- Generate: downloads zip with trace.jsonl + manifest.json (actual output from backend)

- Preview manifest: shows manifest and sample trace lines

Parameters affect the simulation based on the base stats.

## Parameters

- base

- scale

- input_mult, output_mult

- reuse_bias

- new_sessions

- modeled_mix

- seed

## Running locally (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:8000

See docker-compose.yml and app.py for the backend that handles real generation and serving the UI.

## Notes

Output is simulated from observed patterns in the base collection(s). Intended for testing/modeling.

The first collection is based on Mooncake traces. The tool and UI support the schema for additional collections.
