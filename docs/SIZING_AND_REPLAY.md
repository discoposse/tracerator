# Trace-driven sizing and Replay Map

Tracerator can turn a generated run into two related artifacts:

- a sizing study that sweeps replica counts using the workload's real arrivals,
  ISL/OSL distribution, and modeled KV-cache reuse;
- a bounded replay scene that shows the same workload as physical resource flow
  or logical prefix families.

The trace remains the source of truth. Sizing and visualization are derived
views and do not rewrite request timing, lengths, or prefix hashes.

## Sizing study

Open a run and choose **Size**. Select a model architecture, hardware SKU,
precisions, tensor-parallel degree, llm-d latency profile, topology,
per-replica KV-cache capacity, p95 TTFT target, and maximum replica count.

The study has three evidence layers:

1. The analytical envelope checks weight, KV-cache, and activation memory, then
   estimates prefill and decode as the maximum of compute, HBM, and interconnect
   time. It uses the trace's p95 input/output lengths rather than a generic
   fixed sequence length.
2. The llm-d sweep replays actual arrivals through finite sequence slots to
   model load, queueing, and replica count.
3. An attached AIPerf result calibrates the latency profile with observed TTFT
   and inter-token latency.

The memory/roofline report is independently implemented from the methodology
demonstrated by the
[LLM Performance Calculator](https://calc.inference.systems/#info). Catalog
entries keep direct publisher or hardware-vendor source links. Its lower bound
does not replace the existing serving model.

The study uses the per-token model documented by
[llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim):

```text
prefill = overhead + uncached_input_tokens * prefill_time_per_token
decode = (output_tokens - 1) * inter_token_latency
```

GPU-bound latency is multiplied by llm-d's linearly interpolated
`time-factor-under-load`. Requests are assigned to the replica with the earliest
available sequence slot, producing queue, TTFT, request-latency, utilization,
and GPU-count estimates for every candidate.

When an AIPerf replay result is attached and calibration is `auto`, the study
anchors the profile's p50 TTFT slope and inter-token latency to those observed
values. Otherwise it uses one of the upstream llm-d reference profiles:

- 8B on one H100, balanced;
- 70B on 8x H100, throughput optimized;
- 1-3B on one L40S, low latency.

The analytical baseline control has three modes:

- `Profile + roofline guardrail` keeps the llm-d assumptions but will not allow
  them below the analytical floor;
- `Report only` leaves serving latency unchanged;
- `Roofline baseline` seeds the llm-d profile from the analytical floor before
  AIPerf calibration.

The initial source-backed catalog includes Llama 3.2 3B, Llama 3.1 8B/70B and
NVIDIA L40S, H100, H200, and B200 SKUs. It is intentionally a maintained
Tracerator catalog rather than a copy of another application's bundled data.

The run directory receives:

- `sizing_result.json`, the complete assumptions, sweep, recommendation, and
  caveats;
- `llmd-simulator.yaml`, an llm-d-inference-sim configuration for measurement
  with AIPerf.

Tracerator uses 512-token cache blocks. llm-d-inference-sim supports block sizes
through 128 tokens, so the exported configuration translates the same token
capacity into four simulator blocks per Tracerator block.

### API

```text
GET  /sizing/profiles
GET  /runs/<run_id>/sizing
POST /runs/<run_id>/sizing
```

Example request:

```json
{
  "profile": "8b-h100-balanced",
  "analytical_mode": "guardrail",
  "model_id": "llama-3.1-8b",
  "hardware_id": "h100-sxm-80gb",
  "tensor_parallel": 1,
  "weight_precision": "bf16",
  "kv_precision": "bf16",
  "activation_precision": "bf16",
  "memory_headroom_percent": 10,
  "topology": "aggregated",
  "calibration": "auto",
  "cache_gib": 40,
  "target_ttft_p95_ms": 1000,
  "max_replicas": 16
}
```

This is an estimate, not a substitute for target-hardware measurement. Use the
exported simulator config for controlled routing experiments, then replay the
trace with AIPerf and attach the result to calibrate the next study.

## Replay Map

Open **Inspect > Replay Map**. The physical representation shows request flow
through ingress, queue, prefill, KV cache, decode, and response resources. The
logical representation groups requests around their hottest shared-prefix
roots. Playback can be scrubbed, paused, accelerated, and rotated in 3D.

The renderer does not draw every request or hash. The backend creates at most
600 deterministic request samples and 180 time buckets by default:

```text
GET /runs/<run_id>/replay-scene
```

The scene includes physical and logical nodes, sampled requests, bucketed
pressure frames, attached AIPerf latency, and the current sizing recommendation.
Three.js provides the primary renderer; a 2D canvas fallback uses the same
scene contract when WebGL or the module cannot load.

The particle, orbit, field-line, camera, and damping vocabulary was adapted from
the MIT-licensed [gemsjohn/physics-sims](https://github.com/gemsjohn/physics-sims)
examples. Tracerator owns its renderer and data model; the standalone demo HTML
files are not vendored into the application.

## Fence and future calibration

The boundaries are intentional:

- AIPerf generates, validates, and measures replay workloads.
- Tracerator owns workload provenance, causal prefix structure, and 512-token KV
  planning.
- llm-d-inference-sim supplies latency/load semantics and a calibration target.
- Replay Map explains the derived state without becoming an independent
  simulator.

The next sizing increment should ingest llm-d Prometheus metrics and compare
modeled versus observed running/waiting requests, TTFT, TPOT, prefix hits, and
KV-cache pressure for each sweep point.
