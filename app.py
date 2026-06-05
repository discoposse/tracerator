#!/usr/bin/env python3
"""Simple Tracerator backend: serves the static UI and generates real trace zips."""

import json
import random
import zipfile
from io import BytesIO
from flask import Flask, request, send_file, jsonify

app = Flask(__name__, static_folder='site', static_url_path='')

# Simple base stats for simulation (from real patterns, but small for demo)
BASES = {
    'conversation': {'n': 12031, 'dur': 3537000, 'in_med': 6909, 'out_med': 350, 'burst': 28, 'share': 0.4},
    'toolagent': {'n': 23608, 'dur': 3537000, 'in_med': 6346, 'out_med': 30, 'burst': 47, 'share': 0.6},
    'synthetic': {'n': 3993, 'dur': 1022000, 'in_med': 11587, 'out_med': 69, 'burst': 2, 'share': 0.42},
}

def generate_trace_data(params):
    """Generate a simulated but realistic trace based on params. No external data needed."""
    base_name = params.get('base', 'conversation')
    base = BASES.get(base_name, BASES['conversation'])
    scale = float(params.get('scale', 1.0))
    input_mult = float(params.get('input_mult', 1.0))
    output_mult = float(params.get('output_mult', 1.0))
    reuse_bias = float(params.get('reuse_bias', 0.5))
    new_sessions = int(params.get('new_sessions', 0))
    modeled_mix = float(params.get('modeled_mix', 0.0))
    seed = int(params.get('seed', 42))

    random.seed(seed)

    n = max(1, int(base['n'] * scale * (1 + modeled_mix)))
    # Add some from new sessions
    n += new_sessions * 3

    reqs = []
    ts = 0
    hot_blocks = list(range(100))  # simulate hot shared blocks

    for i in range(n):
        # Simulate bursty timestamps
        if random.random() < 0.1:  # burst chance
            ts += random.randint(0, 50)
        else:
            ts += random.randint(10, 500)

        in_len = max(100, int(base['in_med'] * input_mult * random.uniform(0.8, 1.2)))
        out_len = max(1, int(base['out_med'] * output_mult * random.uniform(0.5, 2.0)))

        # hash_ids: simulate sharing, but *must* satisfy AIPerf mooncake_trace rule:
        #   len(hash_ids) == ceil(input_length / 512)
        # (See docs/VALIDATING_WITH_AIPERF.md and normalize_trace_for_aiperf)
        # We use 512 (not 500) and compute exactly from the final in_len.
        num_blocks = max(1, (in_len + 511) // 512)  # equivalent to math.ceil(in_len / 512)
        h = []
        if random.random() < reuse_bias:
            # reuse some hot
            h = random.sample(hot_blocks, min(5, len(hot_blocks)))
            h += [1000 + i * 10 + j for j in range(num_blocks - len(h))]
        else:
            h = [10000 + i * 20 + j for j in range(num_blocks)]

        reqs.append({
            "timestamp": ts,
            "input_length": in_len,
            "output_length": out_len,
            "hash_ids": sorted(h)[:num_blocks]
        })

    # Compute some stats
    unique_h = len(set(h for r in reqs for h in r['hash_ids']))
    manifest = {
        "generator": "tracerator",
        "params": params,
        "n_requests": len(reqs),
        "approx_cache_hit_ratio": round(reuse_bias * 0.8 + 0.1, 3),
        "unique_block_ids": unique_h,
        "max_concurrency": base['burst'],
        "seed": seed,
        "note": "Simulated from base patterns (demo). hash_ids length is now strictly ceil(input_length/512) for AIPerf mooncake_trace compatibility. For production fidelity use Mooncake/trace_gen/."
    }

    # Extra belt-and-suspenders: enforce the rule on the final list
    # (the per-record logic above should already be correct, but this matches the real generator's normalize_trace_for_aiperf)
    BLOCK = 512
    for r in reqs:
        il = r["input_length"]
        needed = max(1, (il + BLOCK - 1) // BLOCK)
        if len(r["hash_ids"]) != needed:
            r["hash_ids"] = r["hash_ids"][:needed]
            # If somehow short (won't be), pad would go here

    return reqs, manifest

@app.route('/')
def serve_ui():
    return app.send_static_file('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    params = request.get_json() or {}
    reqs, manifest = generate_trace_data(params)

    # Build zip in memory
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # trace.jsonl
        trace_content = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs)
        zf.writestr('trace.jsonl', trace_content + '\n')
        # manifest.json
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        # README for users who try to "open" the files
        readme = """Tracerator generated output (demo / simulated traces)

This zip was produced by the Tracerator containerized demo.

Files:
- trace.jsonl   JSON Lines (one compact JSON object per line / request).
                Fields: timestamp (ms), input_length, output_length, hash_ids (list of ints)
                The hash_ids simulate KVCache prefix block sharing for cache-hit modeling.

- manifest.json Exact input params + output aggregates (n_requests, approx_cache_hit_ratio,
                unique_block_ids, max_concurrency, seed, etc.). Always keep this alongside
                the trace for full reproducibility and traceability.

- README.txt    This file.

IMPORTANT:
trace.jsonl is often large (tens of MB for realistic scales). It is NOT a single JSON document
and is NOT intended to be double-clicked in TextEdit, Preview, or most GUI "JSON viewers".

Recommended ways to inspect or use:
  head -n 5 trace.jsonl | jq .                 # first few, pretty-printed (requires jq)
  wc -l trace.jsonl                            # request count

  # Python / pandas (best for analysis and modeling pipelines)
  import pandas as pd
  df = pd.read_json("trace.jsonl", lines=True)
  print(df.head())
  print("requests:", len(df))

  # Or stream line-by-line for very large traces (no full load in memory)
  import json
  with open("trace.jsonl") as f:
      for line in f:
          req = json.loads(line)
          # ... your processing / replay logic ...

This is a lightweight simulation (randomized from base stats). For real production-derived
traces with authentic burstiness and prefix distributions, use the full Mooncake trace tools.

See the main project README for parameter contract and background.

Tip: Use the project's ./run_trace_ui.sh launcher — it has a pre-flight that ensures
jq (and other utilities) are available and can auto-install them on common platforms.

VALIDATING WITH AIPERF (recommended for perf handoff)
----------------------------------------------------
These trace.jsonl files use the Mooncake format and are natively supported by
NVIDIA AIPerf via --custom-dataset-type mooncake_trace.

1. Static validation (no server needed):
   aiperf analyze-trace trace.jsonl --output-file analysis.json --block-size 512

2. Full "play out" replay (validates bursts + hash_id KV cache behavior):
   # Start a server first (example)
   # vllm serve Qwen/Qwen3-0.6B --port 8000
   aiperf profile \\
     --model Qwen/Qwen3-0.6B \\
     --endpoint-type chat --streaming \\
     --url http://localhost:8000 \\
     --input-file trace.jsonl \\
     --custom-dataset-type mooncake_trace \\
     --fixed-schedule \\
     --tokenizer Qwen/Qwen3-0.6B

   --fixed-schedule replays the exact timestamps (bursty arrivals).
   Omit it (or use --no-fixed-schedule) to drive the server as fast as possible
   with the same request mix.

For the best local experience (Ollama/vLLM + AIPerf setup + validation scripts)
see the aiperf-toolkit: https://github.com/discoposse/aiperf-toolkit

In this repo:
- Full instruction set (canonical guide): docs/VALIDATING_WITH_AIPERF.md
- Convenience wrapper:
   ./scripts/validate-with-aiperf.sh --with-replay --subset 30
   (It wraps analyze + replay with preflights, subsetting, reports, and manifest cross-checks.
    Uses the small demo example by default; point at your generated trace with TRACE_FILE=...)
"""
        zf.writestr('README.txt', readme)
    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='tracerator-output.zip'
    )

@app.route('/manifest', methods=['POST'])
def manifest_preview():
    """Return just the manifest for preview in UI."""
    params = request.get_json() or {}
    _, manifest = generate_trace_data(params)
    # Also include a small sample of the trace
    reqs, _ = generate_trace_data({**params, 'scale': min(0.1, float(params.get('scale',1)))})  # small for preview
    sample = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs[:5])
    return jsonify({
        'manifest': manifest,
        'sample_trace': sample
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
