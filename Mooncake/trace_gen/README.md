# Trace Generator & Slider UI

This tool loads realistic production-derived LLM serving traces, lets you inspect their characteristics, and **extends them with precise controls** while preserving the statistical properties that actually matter for performance modeling:

- Bursty arrivals (many requests at identical timestamps)
- Heavy-tailed input/output lengths
- Structured KV block prefix sharing (the real source of cache reuse in production)
- The co-occurrence patterns that drive real system behavior

**The output is always a standard `trace.jsonl` + `manifest.json`** so your modeling and perf work remains fully reproducible and traceable.

## Current Collection

The first collection is the Mooncake traces (Kimi production traffic) originally released with the Mooncake paper. See the parent [README](../README.md) and the [detailed workload narrative](../WORKLOAD_NARRATIVE.md).

The generator itself is designed to work with any traces that follow the simple schema (`timestamp`, `input_length`, `output_length`, `hash_ids`). Future collections from other sources will use the same tooling.

## Quick Start

```bash
# From the root of the traces repository
./run_trace_ui.sh
```

This creates an isolated environment, installs the pinned dependencies from `requirements.txt`, and launches the Streamlit UI.

In the UI you can:
- Load any trace in the current collection (or upload your own matching the schema)
- Adjust scale, length multipliers, cache reuse intensity, new session injection, jitter, etc.
- See live estimates
- Generate extended traces + full manifests

The core generator is also usable directly from Python.

## Reproducibility

`requirements.txt` pins exact versions. Every generated artifact includes a manifest with the source collection, every parameter value, the seed, and output statistics.

## Adding New Trace Sources

The generator is intentionally not hard-coded to one collection. As long as traces follow the documented schema, they can be loaded and extended. See the root README for the long-term vision of supporting multiple trace sources and output styles.

## Validating Generated Traces with AIPerf

Tracerator outputs are **natively compatible** with [NVIDIA AIPerf](https://github.com/ai-dynamo/aiperf) via the `mooncake_trace` dataset type. This is the recommended way to confirm that your generated traces actually "play out" correctly (exact burst timing via `--fixed-schedule`, realistic KV-cache prefix sharing driven by the `hash_ids`, heavy-tailed lengths, etc.).

### 1. Static analysis (no inference server required)

```bash
aiperf analyze-trace extended_....jsonl \
  --output-file analysis.json \
  --block-size 512
```

This produces distributions + cache-hit estimates derived from the `hash_ids`. Compare against the accompanying `manifest.json`.

### 2. Full trace replay (the real validation)

```bash
# Start a server (vLLM recommended for control + long context)
# source ~/.venv-vllm-metal/bin/activate   # on macOS if using aiperf-toolkit
# vllm serve Qwen/Qwen3-0.6B --port 8000

aiperf profile \
  --model Qwen/Qwen3-0.6B \
  --endpoint-type chat \
  --streaming \
  --url http://localhost:8000 \
  --input-file extended_....jsonl \
  --custom-dataset-type mooncake_trace \
  --fixed-schedule \
  --tokenizer Qwen/Qwen3-0.6B
```

- `--fixed-schedule` (default in the helper) sends requests at the exact millisecond timestamps from the trace — this is what validates the bursty arrival patterns.
- Omit `--fixed-schedule` (or pass `--no-fixed-schedule`) + add `--concurrency N` to replay the *same request mix* but at maximum server throughput (great for capacity studies).

**Important for real traces**: Full Mooncake-derived files contain 12k–23k requests with very long contexts. Always start with a subset:

```bash
head -n 50 extended_....jsonl > tiny.jsonl
aiperf profile ... --input-file tiny.jsonl ...
```

### Convenient wrapper (this repo)

```bash
# From repo root — uses the small demo example by default
./scripts/validate-with-aiperf.sh --with-replay --subset 30

# Against one of your generated artifacts
TRACE_FILE=/path/to/your/trace.jsonl \
  ./scripts/validate-with-aiperf.sh --with-replay --subset 40 --fixed-schedule

# Analyze only (very fast)
TRACE_FILE=... ./scripts/validate-with-aiperf.sh --analyze-only
```

The script:
- Locates your aiperf (supports the `~/venv` convention from the aiperf-toolkit)
- Runs `analyze-trace` + optional replay
- Creates a timestamped report + analysis JSON
- Does manifest cross-checks when available
- Handles subsetting automatically for replay

See the script `--help` for all options (engine, model, tokenizer, url overrides, etc.).

**The full canonical instruction set** (this is the best single document to follow or hand off):
[../docs/VALIDATING_WITH_AIPERF.md](../docs/VALIDATING_WITH_AIPERF.md)

### Related tooling

- **Complete local AIPerf + vLLM/Ollama + LMCache stack** (recommended setup scripts for macOS, Linux, validation helpers):
  https://github.com/discoposse/aiperf-toolkit

- Official AIPerf Mooncake trace replay documentation:
  https://docs.nvidia.com/aiperf/benchmark-modes/trace-replay-with-mooncake-traces

## License

See the root [LICENSE](../LICENSE) (Apache-2.0).