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

        # hash_ids: simulate sharing
        num_blocks = max(2, int(in_len / 500))
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
            "hash_ids": sorted(h)[:num_blocks]  # limit
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
        "note": "Simulated from base patterns. Real generator would load full base trace data and remap hashes precisely."
    }

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
