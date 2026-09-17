# How to generate an external-KV golden trace

Build a deterministic Turn-1 warm / Turn-2 resume pair. Run it twice: once with external KV disabled (cache regeneration) and once with an external KV store enabled.

Published LightInferra numbers in the case manifest are reference points, not pass/fail gates for your stack.

## From the UI

1. Create a run.
2. Set **Baseline source** to **LightInferra-style golden**.
3. Choose the headline set or the context-length curve, then a model and context.
4. Set repetitions and the engine's physical KV page size. Logical AIPerf blocks stay at 512 tokens.
5. Generate and download the zip.

The zip keeps `trace.jsonl`, `turn1_warm.jsonl`, `turn2_resume.jsonl`, `case_manifest.json`, and `kv_io_summary.jsonl`.

## From the CLI

Headline cases:

```bash
python scripts/export_lightinferra_golden.py \
  --suite headline \
  --output-dir artifacts/lightinferra-headline
```

Context curve:

```bash
python scripts/export_lightinferra_golden.py \
  --suite curve \
  --output-dir artifacts/lightinferra-curve
```

## Replay both arms

1. Analyze `trace.jsonl` with AIPerf.
2. Profile with `--export-level records`, the same model, tokenizer, schedule, and software on both arms.
3. Between turns, evict or bypass GPU/host KV. Keep the external KV namespace on the remote-KV arm.
4. Compare Turn-2 TTFT only:

```bash
python scripts/compare_lightinferra_golden.py \
  --case-manifest artifacts/lightinferra-headline/qwen2.5-7b/1010000/case_manifest.json \
  --regen-run-dir artifacts/regen-run \
  --remote-kv-run-dir artifacts/remote-kv-run \
  --remote-kv-read-bytes 123456 \
  --output artifacts/lightinferra-compare.json
```

A comparison is incomplete until telemetry shows nonzero external-KV read bytes on the remote arm. An HBM hit is not a remote-KV result.

LightInferra methodology source: [Lightbits Labs blog](https://www.lightbitslabs.com/blog/introducing-lightinferra-280x-improved-ai-token-economy-by-lightbits-labs/).
