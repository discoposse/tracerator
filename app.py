#!/usr/bin/env python3
"""Simple Tracerator backend: serves the static UI and generates real trace zips."""

import json
import random
import zipfile
from html import escape
from io import BytesIO
from flask import Flask, request, send_file, jsonify

app = Flask(__name__, static_folder='site', static_url_path='')

# Simple base stats for simulation (from real patterns, but small for demo)
BASES = {
    'conversation': {'n': 12031, 'dur': 3537000, 'in_med': 6909, 'out_med': 350, 'burst': 28, 'share': 0.4},
    'toolagent': {'n': 23608, 'dur': 3537000, 'in_med': 6346, 'out_med': 30, 'burst': 47, 'share': 0.6},
    'synthetic': {'n': 3993, 'dur': 1022000, 'in_med': 11587, 'out_med': 69, 'burst': 2, 'share': 0.42},
}

ISL_BINS = [
    ('<=1K', 1, 1024),
    ('1-2K', 1025, 2048),
    ('2-4K', 2049, 4096),
    ('4-8K', 4097, 8192),
    ('8-16K', 8193, 16384),
    ('16-32K', 16385, 32768),
    ('32-64K', 32769, 65536),
    ('64-128K', 65537, 131072),
    ('>128K', 131073, 262144),
]

ISL_PROFILES = {
    'empirical': {},
    'rag': {'<=1K': 0.02, '1-2K': 0.08, '2-4K': 0.28, '4-8K': 0.32, '8-16K': 0.20, '16-32K': 0.07, '32-64K': 0.02, '64-128K': 0.01, '>128K': 0.0},
    'balanced': {'<=1K': 0.03, '1-2K': 0.05, '2-4K': 0.12, '4-8K': 0.18, '8-16K': 0.22, '16-32K': 0.22, '32-64K': 0.13, '64-128K': 0.04, '>128K': 0.01},
    'long_context': {'<=1K': 0.01, '1-2K': 0.02, '2-4K': 0.04, '4-8K': 0.08, '8-16K': 0.16, '16-32K': 0.25, '32-64K': 0.25, '64-128K': 0.15, '>128K': 0.04},
    'short_chat': {'<=1K': 0.18, '1-2K': 0.24, '2-4K': 0.28, '4-8K': 0.18, '8-16K': 0.08, '16-32K': 0.03, '32-64K': 0.01, '64-128K': 0.0, '>128K': 0.0},
}

def isl_bin_for_length(length):
    for name, lo, hi in ISL_BINS:
        if lo <= length <= hi:
            return name
    return '>128K'

def isl_distribution(lengths):
    total = max(1, len(lengths))
    return {
        name: {'count': sum(1 for v in lengths if isl_bin_for_length(v) == name),
               'share': round(sum(1 for v in lengths if isl_bin_for_length(v) == name) / total, 6)}
        for name, _, _ in ISL_BINS
    }

def render_isl_distribution_text(distribution, title="ISL distribution"):
    total = sum(int(v.get('count', 0)) for v in distribution.values()) or 1
    max_share = max((float(v.get('share', 0)) for v in distribution.values()), default=0) or 1
    lines = [title, "[Input length (ISL, tokens)]"]
    for name, _, _ in ISL_BINS:
        bucket = distribution.get(name, {})
        count = int(bucket.get('count', 0))
        share = float(bucket.get('share', count / total))
        bar_len = int(round((share / max_share) * 42)) if share > 0 else 0
        lines.append(f"{name:<8} {count:>7} ({share * 100:>5.1f}%) {'#' * bar_len}")
    return "\n".join(lines) + "\n"

def render_isl_distribution_svg(distribution, title="ISL distribution"):
    width = 900
    row_h = 34
    top = 64
    left = 130
    bar_w = 560
    height = top + len(ISL_BINS) * row_h + 44
    max_share = max((float(v.get('share', 0)) for v in distribution.values()), default=0) or 1
    rows = []
    for idx, (name, _, _) in enumerate(ISL_BINS):
        bucket = distribution.get(name, {})
        count = int(bucket.get('count', 0))
        share = float(bucket.get('share', 0))
        y = top + idx * row_h
        w = int((share / max_share) * bar_w) if share > 0 else 0
        rows.append(f'''
  <text x="24" y="{y + 19}" class="label">{escape(name)}</text>
  <rect x="{left}" y="{y}" width="{bar_w}" height="22" rx="4" class="track"/>
  <rect x="{left}" y="{y}" width="{w}" height="22" rx="4" class="bar"/>
  <text x="{left + bar_w + 18}" y="{y + 17}" class="value">{count:,} ({share * 100:.1f}%)</text>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <style>
    .bg {{ fill: #f8fafc; }}
    .title {{ font: 700 22px system-ui, -apple-system, Segoe UI, sans-serif; fill: #0f172a; }}
    .sub {{ font: 500 13px system-ui, -apple-system, Segoe UI, sans-serif; fill: #64748b; }}
    .label {{ font: 600 13px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #334155; }}
    .value {{ font: 600 13px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #334155; }}
    .track {{ fill: #e2e8f0; }}
    .bar {{ fill: #0f172a; }}
  </style>
  <rect class="bg" width="100%" height="100%"/>
  <text x="24" y="30" class="title">{escape(title)}</text>
  <text x="24" y="50" class="sub">Input length buckets for AIPerf trace replay</text>
{''.join(rows)}
</svg>
'''

def choose_isl_length(base_med, input_mult, profile):
    weights = ISL_PROFILES.get(profile or 'empirical', {})
    if not weights:
        return max(100, int(base_med * input_mult * random.uniform(0.8, 1.2)))
    r = random.random()
    cum = 0.0
    chosen = ISL_BINS[3]
    for bucket in ISL_BINS:
        cum += weights.get(bucket[0], 0)
        if r <= cum:
            chosen = bucket
            break
    _, lo, hi = chosen
    value = int(random.uniform(lo, hi) * input_mult)
    return max(lo, min(hi, value))

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
    isl_profile = params.get('isl_profile', 'empirical')
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

        in_len = choose_isl_length(base['in_med'], input_mult, isl_profile)
        out_len = max(1, int(base['out_med'] * output_mult * random.uniform(0.5, 2.0)))

        # hash_ids: simulate sharing, but *must* satisfy AIPerf mooncake_trace rule:
        #   len(hash_ids) == ceil(input_length / 512)
        # (See docs/VALIDATING_WITH_AIPERF.md and normalize_trace_for_aiperf)
        num_blocks = max(1, (in_len + 511) // 512)

        # Improved demo logic that demonstrates the stronger reuse_bias effect.
        # High bias → much higher chance of starting with a "long" hot shared prefix
        # (longer shared = deeper causal block hit). This mirrors the production
        # _choose_hit_prefix + commit logic in the real generator.
        h = []
        p_reuse = min(0.97, 0.04 + 0.93 * (reuse_bias ** 0.6))
        if random.random() < p_reuse:
            # bias the length of the reused hot prefix toward longer at high bias
            desired_shared = max(2, int(num_blocks * (0.35 + 0.60 * reuse_bias)))
            shared_n = min(desired_shared, len(hot_blocks), num_blocks - 1)
            if shared_n > 0:
                h = random.sample(hot_blocks, shared_n)
            # fill the rest with "unique" tail
            h += [2000 + i * 13 + j for j in range(num_blocks - len(h))]
        else:
            h = [10000 + i * 17 + j for j in range(num_blocks)]

        reqs.append({
            "timestamp": ts,
            "input_length": in_len,
            "output_length": out_len,
            "hash_ids": h[:num_blocks]
        })

    # Compute some stats
    unique_h = len(set(h for r in reqs for h in r['hash_ids']))

    # Compute a realistic approx_cache_hit_ratio from the actual generated prefixes
    # (mini version of the real generator's _compute_causal_hits + analyze_trace).
    # This makes the demo's manifest value move much more intuitively with reuse_bias
    # and be closer to what aiperf analyze-trace will report on the output.
    past_prefixes = set()
    total_hit_blocks = 0
    total_blocks = 0
    for r in reqs:
        h = r["hash_ids"]
        total_blocks += len(h)
        m = 0
        for k in range(len(h), 0, -1):
            if tuple(h[:k]) in past_prefixes:
                m = k
                break
        total_hit_blocks += m
        for k in range(1, len(h) + 1):
            past_prefixes.add(tuple(h[:k]))
    computed_hit = (total_hit_blocks / max(1, total_blocks)) if total_blocks > 0 else 0.0

    manifest = {
        "generator": "tracerator",
        "params": params,
        "n_requests": len(reqs),
        "approx_cache_hit_ratio": round(computed_hit, 3),
        "unique_block_ids": unique_h,
        "max_concurrency": base['burst'],
        "isl_distribution": isl_distribution([r["input_length"] for r in reqs]),
        "integrity": {
            "schema": "mooncake_trace",
            "block_size": 512,
            "hash_ids_rule": "len(hash_ids) == ceil(input_length / 512)",
            "aiperf_ready": True
        },
        "seed": seed,
        "note": "Simulated from base patterns (demo, improved bias model). ISL profiles reshape input-length clusters for prefill/KV experiments. approx_cache_hit_ratio is computed from actual generated prefix overlaps (mini-causal). hash_ids length strictly ceil(input_length/512). For production use Mooncake/trace_gen/."
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
        zf.writestr(
            'isl_distribution.txt',
            render_isl_distribution_text(manifest['isl_distribution'], 'TRACE: trace.jsonl')
        )
        zf.writestr(
            'isl_distribution.svg',
            render_isl_distribution_svg(manifest['isl_distribution'], 'TRACE: trace.jsonl')
        )
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

- isl_distribution.txt / .svg
                Human-readable ISL bucket distribution for quick perf handoff review.

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

**Canonical full instructions:**
See docs/VALIDATING_WITH_AIPERF.md in this repository (the complete step-by-step guide).

Quick start:
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
   Omit it (or use --no-fixed-schedule --concurrency N) to drive the server
   as fast as possible with the same request mix.

**Best local experience (Ollama/vLLM + AIPerf + LMCache setup + validation scripts):**
https://github.com/discoposse/aiperf-toolkit

In this repo:
- Full instruction set (canonical guide): docs/VALIDATING_WITH_AIPERF.md
- Convenience wrapper (recommended):
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
