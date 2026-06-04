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

## License

See the root [LICENSE](../LICENSE) (Apache-2.0).