# How to import a workload source

Tracerator accepts timestamped requests with input/output lengths and ordered prefix `hash_ids`.

## Convert a file

Mooncake-style block hashes, timestamps in milliseconds:

```bash
python scripts/import_trace_source.py source.jsonl \
  --mode mooncake \
  --timestamp-unit milliseconds \
  --source-name my-source \
  -o imported_trace.jsonl
```

Component hashes (RAGPulse-style), timestamps in seconds:

```bash
python scripts/import_trace_source.py ragpulse.jsonl \
  --mode component \
  --timestamp-unit seconds \
  --source-name ragpulse \
  -o ragpulse.mooncake.jsonl
```

The importer writes a companion manifest. It exits nonzero unless every record satisfies `len(hash_ids) == ceil(input_length / 512)`.

## Known sources

- **Mooncake**: bundled under `Mooncake/traces/`.
- **SageServe**: code/conversation CSVs, plus optional `traces/week-dp.csv`. Prefix blocks are derived from tenant and workload dimensions.
- **RAGPulse**: campus RAG trace. Use component mode. Dataset: [flashserve/RAGPulse](https://github.com/flashserve/RAGPulse).
- **CC Weka with subagents**: Git LFS payload. Run `scripts/fetch-baseline-traces.sh --clone cc-weka-subagents`.
- **LMCache agentic traces**: convert into the same schema before import.

CoffeeBench fit is discussed in [COFFEEBENCH_EVALUATION.md](../COFFEEBENCH_EVALUATION.md).

## CLI export from a discovered source

```bash
./scripts/export_trace_source.py --list-sources

./scripts/export_trace_source.py \
  --base conversation \
  --scale 1.0 \
  --reuse-bias 0.75 \
  --output-dir artifacts/conversation-aiperf
```

Each output directory contains `trace.jsonl`, `manifest.json`, and `isl_distribution.txt`.

## Mount a checkout in Docker

Set `TRACERATOR_*_DIR` in `.env` as in [How to run the UI](run-the-ui.md). Restart Compose. Unavailable sources stay listed with a reason.
