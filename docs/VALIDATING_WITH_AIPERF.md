# Validating Tracerator Outputs with AIPerf

This document is the canonical **instruction set** for validating that `trace.jsonl` files produced by Tracerator (the Mooncake trace generator) actually "play out" correctly when consumed by real performance tooling.

The primary tool for this is **NVIDIA AIPerf** (`aiperf`), which has first-class native support for the exact schema Tracerator emits:

```json
{"timestamp": <ms>, "input_length": <prompt tokens>, "output_length": <gen tokens>, "hash_ids": [<block hashes> ...]}
```

- `timestamp` + `--fixed-schedule` → validates bursty arrivals and exact timing.
- `hash_ids` → drives realistic KV-cache prefix sharing / hit behavior inside AIPerf's `mooncake_trace` loader (instead of random independent requests).

## Why Validate?

Generic synthetic workloads (Poisson arrivals, independent lengths, random or no `hash_ids`) produce completely wrong prefill/decode costs, cache hit ratios, queuing, and transfer behavior.

Tracerator's value is preserving the real statistical structure from production Kimi traces. AIPerf replay is the best way to confirm a generated variant still behaves like the real thing before handing it to modeling or capacity planning teams.

## Prerequisites

1. **AIPerf installed**
   - Recommended: Use the companion [aiperf-toolkit](https://github.com/discoposse/aiperf-toolkit) (the same one the author maintains).
   - It handles venvs (`~/venv`), platform differences (macOS, Linux), vLLM-metal, LMCache, etc.
   - Manual: `python3 -m venv ~/venv && ~/venv/bin/pip install aiperf`

2. **A running inference server** (for replay, not required for static analysis)
   - vLLM is strongly preferred for long-context traces and fine control.
   - On macOS (Apple Silicon): `vllm-metal` via the toolkit.
   - Ollama works for smaller smoke tests.

3. **This repo** (or at least the generated `trace.jsonl` + `manifest.json`).

4. **Optional but highly recommended**: `jq` (the `run_trace_ui.sh` launcher can install it).

## Recommended Path: Use the Convenience Script

The repo includes a purpose-built validator:

```bash
cd /path/to/tracerator

# Static analysis only (fast, no server needed)
./scripts/validate-with-aiperf.sh --analyze-only

# Full validation: analyze + small replay with exact timing
./scripts/validate-with-aiperf.sh --with-replay --subset 30

# Against one of your generated artifacts
TRACE_FILE=/path/to/your/generated/trace.jsonl \
  ./scripts/validate-with-aiperf.sh --with-replay --subset 50 --fixed-schedule

# Target a vLLM server explicitly (common when using the toolkit)
ENGINE=vllm \
MODEL="Qwen/Qwen3-0.6B" \
TOKENIZER="Qwen/Qwen3-0.6B" \
URL="http://localhost:8000" \
./scripts/validate-with-aiperf.sh --with-replay --subset 20
```

The script:
- Locates your `aiperf` (supports `~/venv` and other common locations from the toolkit).
- Runs `aiperf analyze-trace` (always).
- For replay: creates a safe subset, constructs the correct `mooncake_trace` command, runs it, and handles "no server yet" gracefully.
- Produces a timestamped report in `~` (`tracerator-aiperf-validate-*.txt`) plus the raw AIPerf analysis JSON.
- Cross-checks the accompanying `manifest.json` when present.
- Prints the exact commands so you can re-run or scale them manually.

See `./scripts/validate-with-aiperf.sh --help` for all options.

## Manual AIPerf Commands (What the Script Does Under the Hood)

### 1. Static Analysis (no server)

```bash
aiperf analyze-trace path/to/trace.jsonl \
  --output-file analysis.json \
  --block-size 512
```

This computes ISL/OSL distributions, theoretical hit rates from the `hash_ids`, block statistics, etc. Compare key numbers against the `manifest.json` (`output_stats.median_input`, `approx_cache_hit_ratio`, `n_requests`, etc.).

### 2. Full Trace Replay (the "play out" test)

```bash
# Recommended: exact timing + burst fidelity
aiperf profile \
  --model Qwen/Qwen3-0.6B \
  --endpoint-type chat \
  --streaming \
  --url http://localhost:8000 \
  --input-file trace.jsonl \
  --custom-dataset-type mooncake_trace \
  --fixed-schedule \
  --tokenizer Qwen/Qwen3-0.6B

# Alternative: same request mix/shape, but drive the server as hard as possible
aiperf profile \
  --model Qwen/Qwen3-0.6B \
  --endpoint-type chat \
  --streaming \
  --url http://localhost:8000 \
  --input-file trace.jsonl \
  --custom-dataset-type mooncake_trace \
  --no-fixed-schedule \
  --concurrency 4 \
  --tokenizer Qwen/Qwen3-0.6B
```

**Key flags explained:**
- `--custom-dataset-type mooncake_trace` — required for native support of the schema (including `hash_ids` for prefix synthesis).
- `--fixed-schedule` — tells AIPerf to emit requests at the precise millisecond `timestamp` values from the file. This is what validates the real bursty production pattern.
- Without it, AIPerf replays the lengths + sharing structure but ignores captured inter-arrival times (useful for pure capacity/queuing studies on the same workload).

After a replay run you can do:

```bash
aiperf plot
# or
aiperf plot --dashboard
```

## Working with the Example Traces in This Repo

| Trace | Location | Lines | Notes | Good for |
|-------|----------|-------|-------|----------|
| Small demo | `Mooncake/trace_gen/examples/demo_conversation_1.5x.jsonl` | 606 | Comes with `.manifest.json` | Quick local validation, script default |
| Conversation base | `Mooncake/traces/conversation_trace.jsonl` | 12,031 | Real production-derived | Larger but still manageable with `--subset` |
| Tool & Agent base | `Mooncake/traces/toolagent_trace.jsonl` | 23,608 | Extremely high cache reuse, very bursty | Stressing prefix sharing |
| Synthetic base | `Mooncake/traces/synthetic_trace.jsonl` | 3,993 | Lower sharing | Baseline comparison |
| Full arXiv Mooncake | `Mooncake/arxiv-trace/mooncake_trace.jsonl` | 23,608 | The original paper trace | Reference |

Always start replay validation with a small subset on real traces:

```bash
head -n 50 Mooncake/traces/conversation_trace.jsonl > /tmp/tiny.jsonl
aiperf profile ... --input-file /tmp/tiny.jsonl ...
```

## Validating Your Own Generated Outputs

1. Use the Tracerator UI (containerized via `./run_trace_ui.sh` or the Streamlit generator in `Mooncake/trace_gen/`).
2. Download the zip (contains `trace.jsonl`, `manifest.json`, `README.txt`).
3. Run the validator against it:

   ```bash
   TRACE_FILE=/path/to/unzipped/trace.jsonl ./scripts/validate-with-aiperf.sh --with-replay --subset 40
   ```

4. The generated `README.txt` inside every zip now contains the key AIPerf commands and points back to this document + the validator script.

## Built-in Guarantee (as of the fix for perf-team testing)

Both generators now produce traces that satisfy the AIPerf rule **by construction**:

- `len(hash_ids) == ceil(input_length / 512)` for every record (using `BLOCK_SIZE = 512`).

The real generator (`Mooncake/trace_gen/generator.py`) calls `normalize_trace_for_aiperf()` at the very end of `generate_extended()`.

The demo generator (`app.py` used by the web UI) uses equivalent logic when building each record.

**Programmatic access (for legacy traces or external pipelines):**

```python
from Mooncake.trace_gen.generator import (
    normalize_trace_for_aiperf,
    validate_hash_block_consistency,
    blocks_for_length,
)

clean = normalize_trace_for_aiperf(your_reqs)          # always safe to call
errors = validate_hash_block_consistency(clean)        # should be []
needed = blocks_for_length(63532)                      # -> 125
```

`normalize_trace_for_aiperf` implements exactly the truncation strategy recommended by the perf team (drop excess tail blocks, keep the original `input_length` value and the prefix sharing intact).

## Interpreting Results

**Good signs:**
- `analyze-trace` produces plausible ISL/OSL and hit-rate numbers that roughly match the manifest.
- Replay with `--fixed-schedule` succeeds and shows realistic burst behavior in the time-sliced / goodput views.
- Observed request rate during fixed-schedule replay is consistent with the trace's average RPS (plus the scale factor you applied).
- Cache-related behavior (TTFT distribution, etc.) reflects the prefix sharing encoded in `hash_ids`.

**Compare back to the manifest:**
- `output_stats.n_requests`, `median_input`, `median_output`, `approx_cache_hit_ratio`, `avg_rps`, `max_concurrency`.

**Red flags:**
- Very high failure rate due to context length (the trace may need synthesis filters or a larger-context model).
- Tokenizer load failures → add `--use-server-token-count`.
- Replay succeeds but metrics look like a completely uniform synthetic workload (suggests `hash_ids` were ignored or the loader wasn't `mooncake_trace`).

## Common Issues & Fixes

- **"No server" / connection errors on replay** — Start your server first. The validator script will still show the exact command you can re-run after the server is up.
- **Long context / context window exceeded** — Real traces have heavy tails (many > 8k–30k+ tokens). Use tiny subsets for smoke tests, or a model with sufficient context. Some AIPerf versions support synthesis filters like `--synthesis-max-isl`.
- **Tokenizer errors** — Provide the correct Hugging Face tokenizer repo ID (not the Ollama tag). Fall back with `--use-server-token-count`.
- **Slow runs** — Never run full 12k–23k line traces with fixed schedule for initial validation. `--subset 20-100` is usually enough to prove the structure works.
- **Python version issues with AIPerf** — The toolkit's setup scripts avoid 3.14+ (known cyclopts/ForwardRef problems). Use the venvs they create.
- **macOS specifics** — Use the toolkit's `macos-aiperf-full-setup.sh --with-vllm` path for good vLLM-metal support.

## Handoff to Perf / Modeling Teams

When you hand off a generated trace + manifest:

1. Recipient runs `aiperf analyze-trace` themselves (cheap sanity check).
2. They run a small fixed-schedule replay against their target stack.
3. They compare observed behavior (TTFT/TBT distributions under realistic bursts + cache hits) against the manifest stats and against baseline Mooncake traces.
4. The `manifest.json` + the AIPerf analysis JSON + profile export give full provenance and reproducibility.

The validator script + this document + the embedded `README.txt` in zips make the handoff self-documenting.

## Related Files & Commands

- Validator script: `scripts/validate-with-aiperf.sh`
- Demo + manifest: `Mooncake/trace_gen/examples/demo_conversation_1.5x.*`
- Real generator (full fidelity): `Mooncake/trace_gen/`
- UI launcher (includes jq preflight): `./run_trace_ui.sh`
- Toolkit (best way to get a reliable local AIPerf + server stack): https://github.com/discoposse/aiperf-toolkit
- Official AIPerf Mooncake trace docs: https://docs.nvidia.com/aiperf/benchmark-modes/trace-replay-with-mooncake-traces

## Contributing / Extending

- Improvements to the validator script or this doc are welcome.
- If you add new trace collections, make sure they follow the same schema so the `mooncake_trace` path continues to work.
- Consider adding automated validation (e.g. in CI) that runs `--analyze-only` against the committed small demo and checks that key stats are within tolerance of the manifest.

This instruction set + the `validate-with-aiperf.sh` script together give you a repeatable, documented way to prove that every Tracerator output is faithful before it goes into serious performance work.