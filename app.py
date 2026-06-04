#!/usr/bin/env python3
"""Tracerator backend - serves UI and generates trace zips."""

import json
import random
import zipfile
from io import BytesIO
from flask import Flask, request, send_file, jsonify

app = Flask(__name__, static_folder='site', static_url_path='')

BASES = {
    'conversation': {'n': 12031, 'dur': 3537000, 'in_med': 6909, 'out_med': 350, 'burst': 28, 'share': 0.4},
    'toolagent': {'n': 23608, 'dur': 3537000, 'in_med': 6346, 'out_med': 30, 'burst': 47, 'share': 0.6},
    'synthetic': {'n': 3993, 'dur': 1022000, 'in_med': 11587, 'out_med': 69, 'burst': 2, 'share': 0.42},
}

def generate_trace_data(params):
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

    n = max(1, int(base['n'] * scale * (1 + modeled_mix)) + new_sessions * 3)

    reqs = []
    ts = 0
    hot_blocks = list(range(100))

    for i in range(n):
        if random.random() < 0.1:
            ts += random.randint(0, 50)
        else:
            ts += random.randint(10, 500)

        in_len = max(100, int(base['in_med'] * input_mult * random.uniform(0.8, 1.2)))
        out_len = max(1, int(base['out_med'] * output_mult * random.uniform(0.5, 2.0)))

        num_blocks = max(2, int(in_len / 500))
        h = []
        if random.random() < reuse_bias:
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

    unique_h = len(set(h for r in reqs for h in r['hash_ids']))
    manifest = {
        "generator": "tracerator",
        "params": params,
        "n_requests": len(reqs),
        "approx_cache_hit_ratio": round(reuse_bias * 0.8 + 0.1, 3),
        "unique_block_ids": unique_h,
        "max_concurrency": base['burst'],
        "seed": seed,
        "note": "Simulated output based on base patterns."
    }

    return reqs, manifest

@app.route('/')
def serve_ui():
    return app.send_static_file('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    params = request.get_json() or {}
    reqs, manifest = generate_trace_data(params)

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        trace_content = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs)
        zf.writestr('trace.jsonl', trace_content + '\n')
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
    params = request.get_json() or {}
    _, manifest = generate_trace_data(params)
    reqs, _ = generate_trace_data({**params, 'scale': min(0.05, float(params.get('scale', 1)))})
    sample = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs[:5])
    return jsonify({
        'manifest': manifest,
        'sample_trace': sample
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
