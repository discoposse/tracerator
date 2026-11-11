# How to run the UI

From the repository root, start the supported stack:

```bash
docker compose up
```

Add `--build` after Python, Docker, or dependency changes. Open http://localhost:8008.

`./run_trace_ui.sh` runs `docker compose up -d --build` and checks for Docker. Use either path. Compose from the repo root is the launch command to remember.

The compose file bind-mounts the repo into the container. HTML and JS updates appear on browser refresh. Restart the stack after Python changes.

## Hugging Face token

If AIPerf replay needs a gated tokenizer, copy `.env.example` to `.env` and set `HF_TOKEN`. Compose does not read your shell profile. Restart after you change `.env`.

## Optional extra sources

Empty directories under `external/` are enough for Compose to start. To attach a real checkout, set the matching variable in `.env`:

```bash
TRACERATOR_RAGPULSE_DIR=/path/to/RAGPulse
TRACERATOR_CC_WEKA_DIR=/path/to/cc-traces-weka-with-subagents-051826
TRACERATOR_AIPERF_TOOLKIT_DIR=/path/to/aiperf-toolkit
TRACERATOR_SAGESERVE_DIR=/path/to/SageServe
```

Then run `docker compose up --build`. Sources without payload files show as unavailable in the UI instead of failing the process.

To fetch the CC Weka Git LFS payload into a sibling directory:

```bash
scripts/fetch-baseline-traces.sh --install-deps --clone cc-weka-subagents
docker compose up --build
```

## Local Flask without Docker

Use this only for UI work. The container image is what includes `aiperf`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:8008.

## Production-style Compose

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

That file publishes `${TRACERATOR_PUBLISH_PORT:-8008}` and names the container `tracerator`. See [How to self-host](self-host.md).
