# How to validate with AIPerf

Confirm a Tracerator `trace.jsonl` analyzes and, if you have a server, replays with Mooncake timing and prefix hashes.

## Static analysis only

From the repository root, with `aiperf` on your `PATH` or in a venv:

```bash
./scripts/validate-with-aiperf.sh --analyze-only
```

Against a generated file:

```bash
TRACE_FILE=/path/to/trace.jsonl ./scripts/validate-with-aiperf.sh --analyze-only
```

Or call AIPerf directly:

```bash
aiperf analyze-trace trace.jsonl --output-file analysis.json
```

The wrapper writes a timestamped report (`tracerator-aiperf-validate-*.txt`) and the analysis JSON.

## Replay a subset

Start an OpenAI-compatible server first (vLLM on port 8000, Ollama on 11434, or equivalent). Do not point AIPerf at Tracerator's UI port 8008.

```bash
TRACE_FILE=/path/to/trace.jsonl \
  ./scripts/validate-with-aiperf.sh --with-replay --subset 50 --fixed-schedule
```

Target a specific engine:

```bash
ENGINE=vllm \
MODEL="Qwen/Qwen3-0.6B" \
TOKENIZER="Qwen/Qwen3-0.6B" \
URL="http://localhost:8000" \
TRACE_FILE=/path/to/trace.jsonl \
./scripts/validate-with-aiperf.sh --with-replay --subset 20
```

Equivalent AIPerf command:

```bash
aiperf profile --model Qwen/Qwen3-0.6B --endpoint-type chat --streaming \
  --url http://localhost:8000 --input-file trace.jsonl \
  --custom-dataset-type mooncake_trace --fixed-schedule \
  --tokenizer Qwen/Qwen3-0.6B
```

## Replay from the UI

1. Run Tracerator with `docker compose up`.
2. Generate a run.
3. On **Validate**, run analyze.
4. On **Replay**, set the server URL to the inference host, not `http://localhost:8008`.
5. From inside Docker, use `http://host.docker.internal:8000` for a host vLLM.

If replay returns HTML and **405 Method Not Allowed**, the URL is Tracerator. Change the port.

If tokenizer download fails, set `HF_TOKEN` in `.env` and recreate the container.

## Local AIPerf install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install aiperf
```

A full server + AIPerf setup for macOS and Linux is in [aiperf-toolkit](https://github.com/discoposse/aiperf-toolkit).

`./scripts/validate-with-aiperf.sh --help` lists flags, including `--slice-duration` for `aiperf plot --dashboard`.
