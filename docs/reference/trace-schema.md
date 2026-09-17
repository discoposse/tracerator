# Trace schema

Each generated or imported trace is UTF-8 JSONL. One request per line.

```json
{
  "timestamp": 1250,
  "input_length": 1024,
  "output_length": 48,
  "hash_ids": [1001, 1002]
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `timestamp` | integer | Arrival time in milliseconds |
| `input_length` | positive integer | Prompt tokens |
| `output_length` | positive integer | Generation tokens |
| `hash_ids` | array of integers | Ordered prefix block ids |

Integrity:

- `len(hash_ids) == ceil(input_length / 512)`
- block size is 512 tokens unless a golden case records a different logical size
- matching prefixes across rows are KV cache hits

`manifest.json` records source id, parameters, ISL bucket distribution, approximate cache-hit ratio, seed, and `integrity.aiperf_ready`.

Golden bundles add `turn1_warm.jsonl`, `turn2_resume.jsonl`, `case_manifest.json`, and `kv_io_summary.jsonl`. `kv_io_summary.jsonl` is a semantic upper bound, not a physical I/O trace.
