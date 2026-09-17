#!/usr/bin/env python3
"""Simple Tracerator backend: serves the static UI and generates real trace zips."""

import json
import random
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from flask import Flask, request, send_file, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent / "Mooncake" / "trace_gen"))
from generator import analyze_trace, generate_extended  # noqa: E402
from trace_sources import (  # noqa: E402
    LIGHTINFERRA_GOLDEN_SOURCE_ID,
    canonicalize_source_id,
    is_lightinferra_golden_source,
    load_source_records,
    source_by_id,
    sources_for_api,
)
from trace_cards import build_run_trace_card, list_builtin_trace_cards  # noqa: E402
from aiperf_results import normalize_aiperf_run  # noqa: E402
from aiperf_runner import aiperf_status, validate_generated_trace  # noqa: E402
from aiperf_datasets import (  # noqa: E402
    convert_mooncake_records,
    dataset_file_name,
    dataset_style_manifest,
    list_aiperf_dataset_styles,
    list_aiperf_public_datasets,
    normalize_aiperf_dataset_style,
    write_aiperf_dataset,
)
from inspect_enrich import enrich_run  # noqa: E402
from replay_scene import build_replay_scene  # noqa: E402
from sizing import build_sizing_study, sizing_catalog  # noqa: E402
from kv_cache_planning import (  # noqa: E402
    DEFAULT_CAPACITY_GIB,
    model_options,
    parse_capacity_gib_values,
    plan_kv_cache,
    precision_options,
)
from lightinferra_golden import (  # noqa: E402
    DEFAULT_LOGICAL_BLOCK_TOKENS,
    DEFAULT_OUTPUT_TOKENS,
    DEFAULT_PHYSICAL_PAGE_TOKENS,
    DEFAULT_RESUME_TAIL_TOKENS,
    DEFAULT_SEED as LIGHTINFERRA_DEFAULT_SEED,
    GOLDEN_MODELS,
    build_case as build_lightinferra_case,
)
import storage  # noqa: E402
from study import (  # noqa: E402
    build_run_study_slice,
    build_study_context,
    chat_with_study,
    list_providers,
    summarize_study,
)

app = Flask(__name__, static_folder='site', static_url_path='')
storage.init_db()

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
ISL_PROFILE_LABELS = {
    'empirical': 'Empirical source',
    'rag': 'RAG-like 2K-16K',
    'balanced': 'Balanced enterprise',
    'long_context': 'Long-context stress',
    'short_chat': 'Short-chat skew',
}

BLOCK_SIZE = 512
WALL_CLOCK_WIN_SCENARIO = "wall_clock_win"
LIGHTINFERRA_GOLDEN_SOURCE = LIGHTINFERRA_GOLDEN_SOURCE_ID
WALL_CLOCK_WIN_ISL_SHARES = {
    '<=1K': 0.01,
    '1-2K': 0.02,
    '2-4K': 0.04,
    '4-8K': 0.08,
    '8-16K': 0.16,
    '16-32K': 0.23,
    '32-64K': 0.28,
    '64-128K': 0.15,
    '>128K': 0.03,
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
  <rect x="{left}" y="{y}" width="{bar_w}" height="22" class="track"/>
  <rect x="{left}" y="{y}" width="{w}" height="22" class="bar"/>
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

def filename_slug(value):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "trace"

def download_timestamp():
    return datetime.now().strftime("%Y%m%d%H%M")

def trace_download_name(manifest, params, extension):
    source = (manifest.get("base_source") or {}).get("label")
    scenario = (manifest.get("trace_scenario") or {}).get("label")
    if not scenario:
        scenario = "Standard generated trace"
    profile = ISL_PROFILE_LABELS.get(params.get("isl_profile") or "empirical", params.get("isl_profile") or "empirical")
    stem = filename_slug(" ".join(part for part in (source, scenario, profile) if part))
    return f"{stem}-{download_timestamp()}.{extension.lstrip('.')}"

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

def parse_float(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback

def parse_int(value, fallback):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback

def clamp(value, lo, hi):
    return max(lo, min(hi, value))

def percentile(values, q):
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]

def counts_from_shares(total, shares):
    counts = {}
    assigned = 0
    items = list(shares.items())
    for idx, (name, share) in enumerate(items):
        if idx == len(items) - 1:
            count = max(0, total - assigned)
        else:
            count = int(round(total * share))
            assigned += count
        counts[name] = count
    return counts

def blocks_for_bucket(bucket_name, rng):
    for name, lo, hi in ISL_BINS:
        if name != bucket_name:
            continue
        min_blocks = max(1, (lo + BLOCK_SIZE - 1) // BLOCK_SIZE)
        max_blocks = max(min_blocks, (hi + BLOCK_SIZE - 1) // BLOCK_SIZE if hi else 320)
        if bucket_name == '>128K':
            max_blocks = min(max_blocks, 320)
        return rng.randint(min_blocks, max_blocks)
    return rng.randint(33, 128)

def resolve_trace_source_id(params):
    """UI/storage use `source`; older callers and scripts used `base`."""
    params = params or {}
    raw = params.get("source") if params.get("source") not in (None, "") else params.get("base")
    value = canonicalize_source_id(str(raw or "conversation").strip())
    return value or "conversation"


def generate_legacy_simulated_trace_data(params):
    """Generate a simulated but realistic trace based on summary stats."""
    params = {**(params or {})}
    params["aiperf_dataset_style"] = normalize_aiperf_dataset_style(params.get("aiperf_dataset_style"))
    base_name = resolve_trace_source_id(params)
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
        "aiperf_dataset": dataset_style_manifest(params.get("aiperf_dataset_style")),
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

def generate_wall_clock_win_trace_data(params):
    """Generate a purpose-built AIPerf trace that emphasizes persistent KV reuse.

    Shape:
    - many distinct long shared prefixes
    - high fan-out per prefix
    - short per-request unique tails
    - delayed reuse across a large working set
    - modest output lengths so prefill dominates end-to-end latency
    """
    params = {**(params or {})}
    params["aiperf_dataset_style"] = normalize_aiperf_dataset_style(params.get("aiperf_dataset_style"))
    seed = parse_int(params.get('seed', 42), 42)
    rng = random.Random(seed)
    scale = clamp(parse_float(params.get('scale', 1.0), 1.0), 0.2, 4.0)
    input_mult = clamp(parse_float(params.get('input_mult', 1.0), 1.0), 0.5, 2.0)
    output_mult = clamp(parse_float(params.get('output_mult', 0.8), 0.8), 0.2, 2.0)
    reuse_bias = clamp(parse_float(params.get('reuse_bias', 0.92), 0.92), 0.55, 0.98)

    prefix_count = max(80, int(round(360 * scale)))
    fanout = max(8, min(18, int(round(7 + reuse_bias * 7))))
    burst_size = 16
    burst_gap_ms = 180

    prefix_buckets = []
    for bucket_name, count in counts_from_shares(prefix_count, WALL_CLOCK_WIN_ISL_SHARES).items():
        prefix_buckets.extend([bucket_name] * count)
    rng.shuffle(prefix_buckets)

    prefix_defs = []
    shared_block_total = 0
    for prefix_idx, bucket_name in enumerate(prefix_buckets):
        target_blocks = max(1, int(round(blocks_for_bucket(bucket_name, rng) * input_mult)))
        if target_blocks <= 4:
            tail_blocks_base = 1
        else:
            tail_blocks_base = max(1, min(12, int(round(target_blocks * rng.uniform(0.035, 0.075)))))
        shared_blocks = max(1, target_blocks - tail_blocks_base)
        start = 10_000_000 + prefix_idx * 10_000
        prefix_defs.append({
            "prefix_id": prefix_idx,
            "bucket": bucket_name,
            "target_blocks": target_blocks,
            "tail_blocks_base": tail_blocks_base,
            "shared_blocks": shared_blocks,
            "shared_ids": list(range(start, start + shared_blocks)),
        })
        shared_block_total += shared_blocks

    reqs = []
    request_index = 0
    tail_id = 100_000_000
    total_blocks = 0
    reused_blocks = 0
    total_unique_tail_blocks = 0
    per_request_reuse = []

    for reuse_round in range(fanout):
        order = list(range(prefix_count))
        rng.shuffle(order)
        for prefix_idx in order:
            prefix = prefix_defs[prefix_idx]
            # Short unique tails make the long shared prefix dominate TTFT.
            if prefix["target_blocks"] <= 4:
                tail_blocks = 1
            else:
                tail_blocks = max(1, int(round(prefix["tail_blocks_base"] * rng.uniform(0.8, 1.2))))
            output_length = max(8, int(rng.gauss(64, 14) * output_mult))
            input_blocks = prefix["shared_blocks"] + tail_blocks
            input_length = input_blocks * BLOCK_SIZE - rng.randint(0, min(256, BLOCK_SIZE - 1))
            tail_ids = list(range(tail_id, tail_id + tail_blocks))
            tail_id += tail_blocks
            hit_blocks = prefix["shared_blocks"] if reuse_round > 0 else 0

            reqs.append({
                "timestamp": (request_index // burst_size) * burst_gap_ms,
                "input_length": input_length,
                "output_length": output_length,
                "hash_ids": prefix["shared_ids"] + tail_ids,
            })
            request_index += 1
            total_blocks += input_blocks
            reused_blocks += hit_blocks
            total_unique_tail_blocks += tail_blocks
            per_request_reuse.append(hit_blocks / input_blocks)

    reqs.sort(key=lambda r: (r["timestamp"], r["input_length"], len(r["hash_ids"])))
    timestamps = [r["timestamp"] for r in reqs]
    input_lengths = [r["input_length"] for r in reqs]
    output_lengths = [r["output_length"] for r in reqs]
    duration_ms = max(timestamps) - min(timestamps) if timestamps else 0
    unique_block_ids = shared_block_total + total_unique_tail_blocks
    reused_token_fraction = reused_blocks / max(1, total_blocks)

    params_with_defaults = {
        **params,
        "trace_scenario": WALL_CLOCK_WIN_SCENARIO,
        "scenario_defaults": {
            "base": "synthetic",
            "scale": scale,
            "reuse_bias": reuse_bias,
            "input_mult": input_mult,
            "output_mult": output_mult,
            "isl_profile": "long_context",
        },
    }
    manifest = {
        "generator": "tracerator",
        "params": params_with_defaults,
        "aiperf_dataset": dataset_style_manifest(params.get("aiperf_dataset_style")),
        "trace_scenario": {
            "id": WALL_CLOCK_WIN_SCENARIO,
            "label": "KV reuse stress scenario",
            "purpose": "Exercise prefix-cache tier behavior with high fan-out, long shared prefixes, and delayed reuse.",
            "design": [
                "mixed context distribution with a long-context tail",
                "shared prefixes with short unique tails",
                "high fan-out per unique prefix",
                "many distinct prefixes so working set exceeds typical HBM KV budgets",
                "reuse delayed by interleaving all prefixes between fan-out rounds",
                "modest output length so prefill and KV effects are easier to observe",
            ],
        },
        "base_source": {
            "id": "synthetic-wall-clock-win",
            "label": "Purpose-built KV reuse trace",
            "family": "Tracerator scenario",
            "path": "generated",
            "mode": "scenario",
            "cache_fidelity": "deterministic block hash_ids",
        },
        "scenario_stats": {
            "prefix_count": prefix_count,
            "fanout_per_prefix": fanout,
            "burst_size": burst_size,
            "burst_gap_ms": burst_gap_ms,
            "shared_blocks_median": percentile([p["shared_blocks"] for p in prefix_defs], 0.5),
            "shared_prefix_tokens_median": percentile([p["shared_blocks"] for p in prefix_defs], 0.5) * BLOCK_SIZE,
            "unique_tail_blocks_total": total_unique_tail_blocks,
            "reused_token_fraction": round(reused_token_fraction, 6),
            "inter_reuse_gap_requests": prefix_count - 1,
            "target_isl_shares": WALL_CLOCK_WIN_ISL_SHARES,
        },
        "effective_scale": scale,
        "n_requests": len(reqs),
        "approx_cache_hit_ratio": round(sum(per_request_reuse) / max(1, len(per_request_reuse)), 3),
        "reused_token_fraction": round(reused_token_fraction, 3),
        "unique_block_ids": unique_block_ids,
        "max_concurrency": max(Counter(timestamps).values()) if timestamps else 0,
        "duration_ms": duration_ms,
        "avg_rps": round(len(reqs) / max(1, duration_ms / 1000), 2),
        "median_input": percentile(input_lengths, 0.5),
        "median_output": percentile(output_lengths, 0.5),
        "p95_input": percentile(input_lengths, 0.95),
        "isl_distribution": isl_distribution(input_lengths),
        "integrity": {
            "schema": "mooncake_trace",
            "block_size": BLOCK_SIZE,
            "hash_ids_rule": "len(hash_ids) == ceil(input_length / block_size)",
            "timestamp_unit": "milliseconds",
            "aiperf_ready": True,
        },
        "note": "Purpose-built trace for exercising persistent prefix reuse while retaining a normal mixed context distribution: high reuse, varied shared-prefix lengths, large distinct-prefix working set, high fan-out, and modest OSL.",
    }
    return reqs, manifest

def generate_trace_data(params):
    """Generate an AIPerf-ready trace from the selected real baseline source."""
    params = {**(params or {})}
    params["aiperf_dataset_style"] = normalize_aiperf_dataset_style(params.get("aiperf_dataset_style"))
    base_name = resolve_trace_source_id(params)
    if is_lightinferra_golden_source(base_name):
        return generate_lightinferra_golden_trace_data(params)
    if params.get('trace_scenario') == WALL_CLOCK_WIN_SCENARIO or params.get('scenario') == WALL_CLOCK_WIN_SCENARIO:
        return generate_wall_clock_win_trace_data(params)

    # Keep source/base aligned so manifests, study packs, and UI stay consistent.
    params = {**params, "source": base_name, "base": base_name}
    try:
        source = source_by_id(base_name)
    except KeyError:
        return generate_legacy_simulated_trace_data(params)

    if not source.available:
        raise ValueError(f"{source.label} is not available: {source.status}")

    scale = float(params.get('scale', 1.0))
    input_mult = float(params.get('input_mult', 1.0))
    output_mult = float(params.get('output_mult', 1.0))
    reuse_bias = float(params.get('reuse_bias', 0.5))
    new_sessions = int(params.get('new_sessions', 0))
    modeled_mix = min(0.30, max(0.0, float(params.get('modeled_mix', 0.0))))
    isl_profile = params.get('isl_profile', 'empirical')
    seed = int(params.get('seed', 42))

    base_reqs = load_source_records(base_name)
    effective_scale = scale
    if scale < 1.0:
        subset_n = max(1, int(len(base_reqs) * scale))
        base_reqs = base_reqs[:subset_n]
        effective_scale = 1.0

    analysis = analyze_trace(base_reqs, name=source.id)
    ext_reqs, real_manifest = generate_extended(
        base_reqs,
        analysis,
        scale=effective_scale,
        input_mult=input_mult,
        output_mult=output_mult,
        reuse_bias=reuse_bias,
        reuse_temperature=0.7,
        seed=seed,
        add_new_sessions=new_sessions,
        new_req_fraction=modeled_mix,
        isl_profile=None if isl_profile == 'empirical' else isl_profile,
    )
    output_stats = real_manifest["output_stats"]
    manifest = {
        "generator": "tracerator",
        "params": params,
        "aiperf_dataset": dataset_style_manifest(params.get("aiperf_dataset_style")),
        "base_source": {
            "id": source.id,
            "label": source.label,
            "family": source.family,
            "path": str(source.path),
            "mode": source.mode,
            "cache_fidelity": source.cache_fidelity,
            "description": source.description,
            "source_url": source.source_url,
            "source_label": source.source_label,
            "license": source.license,
        },
        "base_stats": real_manifest.get("base_stats", {}),
        "effective_scale": effective_scale,
        "n_requests": output_stats["n_requests"],
        "approx_cache_hit_ratio": output_stats["approx_cache_hit_ratio"],
        "unique_block_ids": output_stats["unique_block_ids"],
        "max_concurrency": output_stats["max_concurrency"],
        "isl_distribution": output_stats["isl_distribution"],
        "integrity": real_manifest.get("integrity", {
            "schema": "mooncake_trace",
            "block_size": 512,
            "hash_ids_rule": "len(hash_ids) == ceil(input_length / 512)",
            "aiperf_ready": True,
        }),
        "seed": seed,
        "production_manifest": real_manifest,
        "note": "Generated from a real baseline source through the production Tracerator generator. Source prefix/hash structure is preserved or deterministically adapted to 512-token AIPerf block hashes.",
    }
    return ext_reqs, manifest


def lightinferra_golden_options():
    models = []
    for model in GOLDEN_MODELS.values():
        references = {
            str(context): {
                "published_context_label": reference.published_context_label,
                "regen_ttft_ms": reference.regen_ttft_ms,
                "remote_kv_ttft_ms": reference.remote_ttft_ms,
                "published_speedup_x": reference.published_speedup,
            }
            for context, reference in model.references.items()
        }
        models.append({
            "id": model.id,
            "label": model.published_model,
            "max_context_tokens": model.max_context_tokens,
            "headline_contexts": list(model.references),
            "curve_contexts": list(model.curve_points),
            "references": references,
        })
    return {
        "source_id": LIGHTINFERRA_GOLDEN_SOURCE,
        "suites": [
            {"id": "headline", "label": "Headline reference points"},
            {"id": "curve", "label": "Context-length curve"},
        ],
        "models": models,
        "defaults": {
            "suite": "headline",
            "model": "qwen2.5-7b",
            "context_tokens": 1_010_000,
            "repetitions": 1,
            "physical_page_tokens": DEFAULT_PHYSICAL_PAGE_TOKENS,
            "resume_tail_tokens": DEFAULT_RESUME_TAIL_TOKENS,
            "output_tokens": DEFAULT_OUTPUT_TOKENS,
            "seed": LIGHTINFERRA_DEFAULT_SEED,
        },
    }


def generate_lightinferra_golden_trace_data(params):
    params = {**(params or {})}
    source = source_by_id(LIGHTINFERRA_GOLDEN_SOURCE)
    suite = str(params.get("lightinferra_suite") or "headline")
    if suite not in {"headline", "curve"}:
        raise ValueError("LightInferra suite must be 'headline' or 'curve'")

    model_id = str(params.get("lightinferra_model") or "qwen2.5-7b")
    model = GOLDEN_MODELS.get(model_id)
    if model is None:
        raise ValueError(f"Unknown LightInferra golden model: {model_id}")
    allowed_contexts = tuple(model.references) if suite == "headline" else tuple(model.curve_points)
    default_context = max(allowed_contexts)
    try:
        context_tokens = int(params.get("lightinferra_context_tokens") or default_context)
        repetitions = int(params.get("lightinferra_repetitions") or 1)
        physical_page_tokens = int(
            params.get("lightinferra_physical_page_tokens") or DEFAULT_PHYSICAL_PAGE_TOKENS
        )
        resume_tail_tokens = int(
            params.get("lightinferra_resume_tail_tokens") or DEFAULT_RESUME_TAIL_TOKENS
        )
        output_tokens = int(params.get("lightinferra_output_tokens") or DEFAULT_OUTPUT_TOKENS)
        seed = int(params.get("seed") or LIGHTINFERRA_DEFAULT_SEED)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid LightInferra golden parameter: {exc}") from exc
    if context_tokens not in allowed_contexts:
        raise ValueError(
            f"Context {context_tokens:,} is not in the {suite} suite for {model_id}"
        )

    turn1, turn2, combined, case_manifest = build_lightinferra_case(
        model=model,
        context_tokens=context_tokens,
        repetitions=repetitions,
        logical_block_tokens=DEFAULT_LOGICAL_BLOCK_TOKENS,
        physical_page_tokens=physical_page_tokens,
        resume_tail_tokens=resume_tail_tokens,
        output_tokens=output_tokens,
        seed=seed,
    )
    analysis = analyze_trace(combined, name=case_manifest["case_id"])
    unique_ids = {block_id for row in combined for block_id in row["hash_ids"]}
    effective_params = {
        **params,
        "source": LIGHTINFERRA_GOLDEN_SOURCE,
        "base": LIGHTINFERRA_GOLDEN_SOURCE,
        "scenario": "lightinferra_golden",
        "lightinferra_suite": suite,
        "lightinferra_model": model_id,
        "lightinferra_context_tokens": context_tokens,
        "lightinferra_repetitions": repetitions,
        "lightinferra_physical_page_tokens": physical_page_tokens,
        "lightinferra_resume_tail_tokens": resume_tail_tokens,
        "lightinferra_output_tokens": output_tokens,
        "kv_model": model.kv_model_id,
        "block_size": DEFAULT_LOGICAL_BLOCK_TOKENS,
        "seed": seed,
    }
    manifest = {
        "generator": "tracerator-lightinferra-golden",
        "params": effective_params,
        "aiperf_dataset": dataset_style_manifest(effective_params.get("aiperf_dataset_style")),
        "base_source": {
            "id": source.id,
            "label": source.label,
            "family": source.family,
            "path": str(source.path),
            "mode": source.mode,
            "cache_fidelity": source.cache_fidelity,
            "description": source.description,
            "source_url": source.source_url,
            "source_label": source.source_label,
            "license": source.license,
        },
        "trace_scenario": {
            "id": "lightinferra_golden",
            "label": "Turn-1/Turn-2 external KV golden",
            "description": (
                "Warm a deterministic long-context KV prefix on Turn 1, then resume the "
                "same prefix on Turn 2 after GPU/host eviction."
            ),
        },
        "base_stats": {
            "n_requests": len(combined),
            "duration_ms": analysis.duration_ms,
            "median_input": analysis.median_input,
            "median_output": analysis.median_output,
        },
        "n_requests": len(combined),
        "approx_cache_hit_ratio": analysis.approx_cache_hit_ratio,
        "unique_block_ids": len(unique_ids),
        "max_concurrency": max(analysis.burst_sizes, default=0),
        "isl_distribution": isl_distribution([row["input_length"] for row in combined]),
        "integrity": {
            "schema": "mooncake_trace",
            "block_size": DEFAULT_LOGICAL_BLOCK_TOKENS,
            "hash_ids_rule": (
                "len(hash_ids) == ceil(input_length / logical_block_tokens)"
            ),
            "timestamp_unit": "milliseconds",
            "aiperf_ready": True,
        },
        "seed": seed,
        "lightinferra_golden": {
            **case_manifest,
            "suite": suite,
            "phase_record_counts": {"turn1": len(turn1), "turn2": len(turn2)},
        },
        "note": (
            "Deterministic LightInferra-style golden case. Published numbers are "
            "external reference points, not automatic pass/fail thresholds."
        ),
    }
    return combined, manifest

def kv_planning_params(params):
    warmup_value = params.get("warmup_fraction", 0.5)
    try:
        warmup_fraction = float(warmup_value)
    except (TypeError, ValueError):
        warmup_fraction = 0.5
    return {
        "model_id": params.get("kv_model", "kimi-k2.5"),
        "precision": params.get("kv_precision", "bf16_fp16"),
        "indexer_precision": params.get("kv_indexer_precision") or params.get("kv_precision", "bf16_fp16"),
        "include_draft_kv": str(params.get("include_draft_kv", "false")).lower() in ("1", "true", "yes", "on"),
        "tensor_parallel": int(float(params.get("tensor_parallel", 1) or 1)),
        "block_size": int(float(params.get("block_size", 512) or 512)),
        "capacity_gib_values": parse_capacity_gib_values(params.get("capacity_gib_values")),
        "warmup_fraction": warmup_fraction,
    }

def attach_kv_planning(reqs, manifest, params):
    planning = plan_kv_cache(reqs, **kv_planning_params(params))
    manifest["kv_cache_planning"] = planning
    return manifest

@app.route('/')
def serve_ui():
    return app.send_static_file('index.html')

@app.route('/runs/<run_id>/view')
@app.route('/run/<run_id>')
def serve_run_ui(run_id):
    return app.send_static_file('run.html')

@app.route('/compare')
def serve_compare():
    return app.send_static_file('compare.html')

@app.route('/profiles')
def serve_profiles():
    return app.send_static_file('profiles.html')

@app.route('/study')
def serve_study():
    return app.send_static_file('study.html')


def _study_slices_for_run_ids(run_ids):
    slices = []
    errors = []
    for run_id in run_ids:
        run = storage.get_run(run_id, full=True)
        if not run:
            errors.append(f"{run_id}: not found")
            continue
        records = storage.load_trace_records(run_id)
        max_records = 6000
        if len(records) > max_records:
            step = max(1, len(records) // max_records)
            records = records[::step][:max_records]
        try:
            slices.append(build_run_study_slice(run=run, records=records))
        except Exception as exc:  # noqa: BLE001 — surface per-run failures to UI
            errors.append(f"{run_id}: {exc}")
    return slices, errors


@app.route('/study/providers', methods=['GET'])
def study_providers_route():
    return jsonify({"providers": list_providers()})


@app.route('/study/context', methods=['POST'])
def study_context_route():
    payload = request.get_json() or {}
    run_ids = payload.get("run_ids") or []
    if not isinstance(run_ids, list) or not run_ids:
        return jsonify({"error": "run_ids must be a non-empty list"}), 400
    run_ids = [str(rid) for rid in run_ids][:5]
    slices, errors = _study_slices_for_run_ids(run_ids)
    if not slices:
        return jsonify({"error": "No usable runs", "details": errors}), 400
    context = build_study_context(slices)
    return jsonify({"context": context, "runs": slices, "errors": errors})


@app.route('/study/summarize', methods=['POST'])
def study_summarize_route():
    payload = request.get_json() or {}
    run_ids = payload.get("run_ids") or []
    if not isinstance(run_ids, list) or not run_ids:
        return jsonify({"error": "run_ids must be a non-empty list"}), 400
    run_ids = [str(rid) for rid in run_ids][:5]
    slices, errors = _study_slices_for_run_ids(run_ids)
    if not slices:
        return jsonify({"error": "No usable runs", "details": errors}), 400
    context = build_study_context(slices)
    try:
        result = summarize_study(
            context,
            provider=str(payload.get("provider") or "openai"),
            api_key=str(payload.get("api_key") or ""),
            model=payload.get("model"),
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc), "errors": errors}), 400
    return jsonify({
        "summary": result,
        "context": context,
        "runs": slices,
        "errors": errors,
    })


@app.route('/study/chat', methods=['POST'])
def study_chat_route():
    payload = request.get_json() or {}
    run_ids = payload.get("run_ids") or []
    question = payload.get("question") or ""
    history = payload.get("history") or []
    if not isinstance(run_ids, list) or not run_ids:
        return jsonify({"error": "run_ids must be a non-empty list"}), 400
    run_ids = [str(rid) for rid in run_ids][:5]
    slices, errors = _study_slices_for_run_ids(run_ids)
    if not slices:
        return jsonify({"error": "No usable runs", "details": errors}), 400
    context = build_study_context(slices)
    if not isinstance(history, list):
        history = []
    try:
        result = chat_with_study(
            context,
            provider=str(payload.get("provider") or "openai"),
            api_key=str(payload.get("api_key") or ""),
            model=payload.get("model"),
            history=history,
            question=str(question),
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc), "errors": errors}), 400
    return jsonify({"reply": result, "errors": errors})


def _run_index_stats(manifest):
    integrity = manifest.get("integrity") or {}
    return {
        "request_count": manifest.get("n_requests"),
        "max_concurrency": manifest.get("max_concurrency"),
        "approx_cache_hit": manifest.get("approx_cache_hit_ratio"),
        "integrity_ok": bool(integrity.get("aiperf_ready", True)),
    }


def _persist_generated_run(run_id, params, reqs, manifest):
    storage.write_trace_jsonl(run_id, reqs)
    aiperf_style = normalize_aiperf_dataset_style(
        params.get("aiperf_dataset_style") or (manifest.get("aiperf_dataset") or {}).get("id")
    )
    write_aiperf_dataset(storage.run_dir(run_id) / dataset_file_name(aiperf_style), reqs, aiperf_style)
    storage.save_json_artifact(run_id, "manifest.json", manifest)
    _persist_lightinferra_artifacts(run_id, reqs, manifest)
    stats = _run_index_stats(manifest)
    source = params.get("source") or (manifest.get("base_source") or {}).get("id")
    scenario = params.get("scenario") or (manifest.get("trace_scenario") or {}).get("id") or "standard"
    records = reqs
    enriched = enrich_run(records, params=params, manifest=manifest)
    narrative = enriched.get("narrative")
    storage.save_json_artifact(run_id, "narrative.json", narrative)
    existing = storage.get_run(run_id, full=False) or {}
    trace_card = build_run_trace_card(
        run_id,
        existing.get("name") or (manifest.get("base_source") or {}).get("label") or run_id,
        records,
        manifest,
    )
    storage.save_json_artifact(run_id, "trace_card.json", trace_card)
    return storage.update_run(
        run_id,
        status="generated",
        source=source,
        scenario=scenario,
        params=params,
        manifest=manifest,
        narrative=narrative,
        request_count=stats["request_count"],
        max_concurrency=stats["max_concurrency"],
        approx_cache_hit=stats["approx_cache_hit"],
        integrity_ok=stats["integrity_ok"],
    )


def _persist_lightinferra_artifacts(run_id, records, manifest):
    golden = manifest.get("lightinferra_golden") or {}
    if not golden:
        return
    workload = golden.get("workload") or {}
    repetitions = int(workload.get("repetitions") or 0)
    gap_ms = int(workload.get("recommended_turn2_delay_ms") or 0)
    if repetitions <= 0 or len(records) != repetitions * 2:
        raise ValueError("LightInferra golden run has an invalid Turn-1/Turn-2 record layout")

    turn1 = []
    turn2 = []
    for repetition in range(repetitions):
        phase_timestamp = repetition * gap_ms
        turn1.append({**records[repetition * 2], "timestamp": phase_timestamp})
        turn2.append({**records[repetition * 2 + 1], "timestamp": phase_timestamp})

    run_dir = storage.run_dir(run_id)
    for name, rows in (
        ("turn1_warm.jsonl", turn1),
        ("turn2_resume.jsonl", turn2),
        ("kv_io_summary.jsonl", golden.get("storage_operations") or []),
    ):
        (run_dir / name).write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
    case_manifest = {
        key: value
        for key, value in golden.items()
        if key not in {"storage_operations", "suite", "phase_record_counts"}
    }
    storage.save_json_artifact(run_id, "case_manifest.json", case_manifest)


def _build_run_zip(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return None, None
    params = run.get("params") or {}
    manifest = run.get("manifest") or {}
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        trace_file = storage.trace_path(run_id)
        if trace_file.exists():
            zf.write(trace_file, "trace.jsonl")
        for artifact_name in (
            "turn1_warm.jsonl",
            "turn2_resume.jsonl",
            "case_manifest.json",
            "kv_io_summary.jsonl",
        ):
            artifact_path = storage.run_dir(run_id) / artifact_name
            if artifact_path.exists():
                zf.write(artifact_path, artifact_name)
        if manifest:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            if manifest.get("isl_distribution"):
                zf.writestr(
                    "isl_distribution.txt",
                    render_isl_distribution_text(manifest["isl_distribution"], "TRACE: trace.jsonl"),
                )
                zf.writestr(
                    "isl_distribution.svg",
                    render_isl_distribution_svg(manifest["isl_distribution"], "TRACE: trace.jsonl"),
                )
        if run.get("analysis"):
            zf.writestr("analysis.json", json.dumps(run["analysis"], indent=2))
        if run.get("replay_result"):
            zf.writestr("replay_result.json", json.dumps(run["replay_result"], indent=2))
        sizing_result = storage.load_json_artifact(run_id, "sizing_result.json")
        if sizing_result:
            zf.writestr("sizing_result.json", json.dumps(sizing_result, indent=2))
            llmd_yaml = (sizing_result.get("llmd_config") or {}).get("yaml")
            if llmd_yaml:
                zf.writestr("llmd-simulator.yaml", llmd_yaml)
        aiperf_style = normalize_aiperf_dataset_style(
            params.get("aiperf_dataset_style") or (manifest.get("aiperf_dataset") or {}).get("id")
        )
        records = storage.load_trace_records(run_id)
        if records:
            aiperf_records = "\n".join(
                json.dumps(row, separators=(",", ":"))
                for row in convert_mooncake_records(records, aiperf_style)
            )
            zf.writestr(dataset_file_name(aiperf_style), aiperf_records + ("\n" if aiperf_records else ""))
        if run.get("narrative"):
            zf.writestr("narrative.json", json.dumps(run["narrative"], indent=2))
            md = (run["narrative"] or {}).get("markdown")
            if md:
                zf.writestr("narrative.md", md)
        trace_card = storage.load_json_artifact(run_id, "trace_card.json")
        if trace_card:
            zf.writestr("trace_card.json", json.dumps(trace_card, indent=2))
        zf.writestr(
            "README.txt",
            "Tracerator run bundle\n\n"
            f"run_id: {run_id}\n"
            "Files may include trace.jsonl, manifest.json, analysis.json, "
            "replay_result.json, sizing_result.json, llmd-simulator.yaml, "
            "trace_card.json, and narrative.md.\n",
        )
    zip_buffer.seek(0)
    name = trace_download_name(manifest or {"base_source": {"label": run.get("name")}}, params, "zip")
    return zip_buffer, name


@app.route('/runs', methods=['GET'])
def list_runs_route():
    return jsonify({
        "runs": storage.list_runs(
            limit=parse_int(request.args.get("limit"), 100),
            offset=parse_int(request.args.get("offset"), 0),
            status=request.args.get("status"),
            query=request.args.get("q"),
        )
    })


@app.route('/runs', methods=['POST'])
def create_run_route():
    payload = request.get_json() or {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else payload
    run = storage.create_run(
        name=payload.get("name"),
        source=params.get("source") or payload.get("source"),
        scenario=params.get("scenario") or payload.get("scenario") or "standard",
        params=params,
        notes=payload.get("notes"),
    )
    return jsonify(run), 201


@app.route('/runs/<run_id>', methods=['GET'])
def get_run_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run)


@app.route('/runs/<run_id>', methods=['DELETE'])
def delete_run_route(run_id):
    if not storage.delete_run(run_id):
        return jsonify({"error": "run not found"}), 404
    return jsonify({"ok": True, "id": run_id})


@app.route('/runs/<run_id>/generate', methods=['POST'])
def generate_run_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    payload = request.get_json() or {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {
        key: value for key, value in payload.items()
        if key not in ("name", "notes")
    }
    if not params:
        params = run.get("params") or {}
    name = payload.get("name")
    try:
        reqs, manifest = generate_trace_data(params)
        params = manifest.get("params") or params
        attach_kv_planning(reqs, manifest, params)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if name:
        storage.update_run(run_id, name=str(name).strip() or run.get("name"))
    updated = _persist_generated_run(run_id, params, reqs, manifest)
    return jsonify(updated)


@app.route('/runs/<run_id>', methods=['PATCH'])
def patch_run_route(run_id):
    payload = request.get_json() or {}
    try:
        updated = storage.update_run(
            run_id,
            name=payload.get("name"),
            notes=payload.get("notes"),
            status=payload.get("status"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not updated:
        return jsonify({"error": "run not found"}), 404
    return jsonify(updated)


@app.route('/runs/<run_id>/validate', methods=['POST'])
def validate_run_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    payload = request.get_json() or {}
    params = run.get("params") or {}
    if isinstance(payload.get("trace_params"), dict):
        params = {**params, **payload["trace_params"]}
    if payload.get("aiperf_dataset_style") or payload.get("custom_dataset_type"):
        params = {
            **params,
            "aiperf_dataset_style": payload.get("aiperf_dataset_style") or payload.get("custom_dataset_type"),
        }

    reqs = storage.load_trace_records(run_id)
    manifest = run.get("manifest")
    if not reqs or not manifest:
        try:
            reqs, manifest = generate_trace_data(params)
            params = manifest.get("params") or params
            attach_kv_planning(reqs, manifest, params)
            _persist_generated_run(run_id, params, reqs, manifest)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    result = validate_generated_trace(
        reqs,
        manifest,
        _aiperf_validation_options(payload),
    )

    analysis = None
    if isinstance(result.get("analysis"), dict) and result["analysis"].get("status") == "ok":
        analysis = result["analysis"]
    replay_result = None
    if isinstance(result.get("replay"), dict) and result["replay"].get("replay_result"):
        replay_result = result["replay"]["replay_result"]

    status = "validated" if analysis else (run.get("status") or "generated")
    if replay_result:
        status = "replayed"
        storage.save_json_artifact(run_id, "replay_result.json", replay_result)
    if analysis:
        storage.save_json_artifact(run_id, "analysis.json", analysis)

    enriched = enrich_run(
        reqs,
        params=params,
        manifest=manifest,
        analysis=analysis,
        replay_result=replay_result,
    )
    storage.update_run(
        run_id,
        status=status,
        analysis=analysis,
        replay_result=replay_result,
        narrative=enriched.get("narrative"),
        has_analysis=bool(analysis),
        has_replay=bool(replay_result),
    )
    result["run"] = storage.get_run(run_id, full=True)
    return jsonify(result)


@app.route('/runs/<run_id>/upload', methods=['POST'])
def upload_run_results_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404

    replay_result = None
    analysis = None

    if request.files:
        uploaded = request.files.get("file") or next(iter(request.files.values()), None)
        if uploaded is None:
            return jsonify({"error": "file is required"}), 400
        filename = (uploaded.filename or "").lower()
        raw = uploaded.read()
        if filename.endswith(".zip"):
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                names = zf.namelist()
                if "replay_result.json" in names:
                    replay_result = json.loads(zf.read("replay_result.json"))
                if "analysis.json" in names:
                    analysis = json.loads(zf.read("analysis.json"))
                # Unpack AIPerf-style artifact zip into run dir and normalize
                if replay_result is None and any(
                    n.endswith("profile_export_aiperf.json") or n == "profile_export_aiperf.json"
                    for n in names
                ):
                    dest = storage.run_dir(run_id) / "uploaded_aiperf"
                    if dest.exists():
                        import shutil
                        shutil.rmtree(dest)
                    dest.mkdir(parents=True, exist_ok=True)
                    zf.extractall(dest)
                    # Find directory containing profile_export_aiperf.json
                    candidates = list(dest.rglob("profile_export_aiperf.json"))
                    if not candidates:
                        return jsonify({"error": "zip missing profile_export_aiperf.json"}), 400
                    try:
                        replay_result = normalize_aiperf_run(candidates[0].parent)
                    except (FileNotFoundError, ValueError, OSError) as exc:
                        return jsonify({"error": str(exc)}), 400
        else:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return jsonify({"error": f"invalid JSON upload: {exc}"}), 400
            if payload.get("artifact_type") == "aiperf_replay_result" or payload.get("schema_version") == "tracerator.replay_result.v1":
                replay_result = payload
            elif "isl_distribution" in payload or payload.get("n_requests") is not None:
                # treat as manifest overlay — uncommon
                storage.update_run(run_id, manifest={**(run.get("manifest") or {}), **payload})
                return jsonify(storage.get_run(run_id, full=True))
            else:
                replay_result = payload
    else:
        body = request.get_json() or {}
        if body.get("artifact_dir"):
            try:
                replay_result = normalize_aiperf_run(body["artifact_dir"])
            except (FileNotFoundError, ValueError, OSError) as exc:
                return jsonify({"error": str(exc)}), 400
        elif body.get("replay_result"):
            replay_result = body["replay_result"]
        elif body.get("artifact_type") == "aiperf_replay_result":
            replay_result = body
        elif body.get("analysis"):
            analysis = body["analysis"]
        else:
            return jsonify({"error": "provide file, artifact_dir, or replay_result"}), 400

    if analysis:
        storage.save_json_artifact(run_id, "analysis.json", analysis)
    if replay_result:
        storage.save_json_artifact(run_id, "replay_result.json", replay_result)

    reqs = storage.load_trace_records(run_id)
    enriched = enrich_run(
        reqs,
        params=run.get("params"),
        manifest=run.get("manifest"),
        analysis=analysis or run.get("analysis"),
        replay_result=replay_result or run.get("replay_result"),
    )
    status = "replayed" if replay_result else (run.get("status") or "generated")
    if analysis and not replay_result and status == "generated":
        status = "validated"
    updated = storage.update_run(
        run_id,
        status=status,
        analysis=analysis if analysis else None,
        replay_result=replay_result if replay_result else None,
        narrative=enriched.get("narrative"),
        has_analysis=bool(analysis or run.get("analysis")),
        has_replay=bool(replay_result or run.get("has_replay")),
    )
    return jsonify(updated)


@app.route('/runs/<run_id>/download', methods=['GET'])
def download_run_route(run_id):
    zip_buffer, name = _build_run_zip(run_id)
    if zip_buffer is None:
        return jsonify({"error": "run not found"}), 404
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=name or f"tracerator-run-{run_id}.zip",
    )


@app.route('/runs/<run_id>/inspect', methods=['GET'])
def inspect_run_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    reqs = storage.load_trace_records(run_id)
    sampled = False
    max_inspect = parse_int(request.args.get("max_records"), 8000)
    if len(reqs) > max_inspect:
        step = max(1, len(reqs) // max_inspect)
        reqs = reqs[::step][:max_inspect]
        sampled = True
    if not reqs:
        return jsonify({
            "run": run,
            "inspect": enrich_run(
                [],
                params=run.get("params"),
                manifest=run.get("manifest"),
                analysis=run.get("analysis"),
                replay_result=run.get("replay_result"),
            ),
            "sampled": False,
        })
    inspect = enrich_run(
        reqs,
        params=run.get("params"),
        manifest=run.get("manifest"),
        analysis=run.get("analysis"),
        replay_result=run.get("replay_result"),
    )
    storage.update_run(run_id, narrative=inspect.get("narrative"))
    return jsonify({
        "run": storage.get_run(run_id, full=True),
        "inspect": inspect,
        "sampled": sampled,
    })


@app.route('/sizing/profiles', methods=['GET'])
def sizing_profiles_route():
    return jsonify(sizing_catalog())


@app.route('/runs/<run_id>/sizing', methods=['GET', 'POST'])
def run_sizing_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    if request.method == 'GET':
        result = storage.load_json_artifact(run_id, "sizing_result.json")
        if not result:
            return jsonify({"error": "sizing study not found"}), 404
        return jsonify({"sizing": result})

    records = storage.load_trace_records(run_id)
    if not records:
        return jsonify({"error": "Generate the trace before running a sizing study"}), 400
    payload = request.get_json() or {}
    try:
        result = build_sizing_study(
            records,
            manifest=run.get("manifest"),
            replay_result=run.get("replay_result"),
            options=payload,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    storage.save_json_artifact(run_id, "sizing_result.json", result)
    llmd_yaml = (result.get("llmd_config") or {}).get("yaml")
    if llmd_yaml:
        (storage.run_dir(run_id) / "llmd-simulator.yaml").write_text(
            llmd_yaml,
            encoding="utf-8",
        )
    return jsonify({"sizing": result})


@app.route('/runs/<run_id>/replay-scene', methods=['GET'])
def replay_scene_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    records = storage.load_trace_records(run_id)
    if not records:
        return jsonify({"error": "Generate the trace before opening Replay Map"}), 400
    try:
        scene = build_replay_scene(
            records,
            manifest=run.get("manifest"),
            replay_result=run.get("replay_result"),
            sizing_result=storage.load_json_artifact(run_id, "sizing_result.json"),
            max_requests=parse_int(request.args.get("max_requests"), 600),
            max_frames=parse_int(request.args.get("max_frames"), 180),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"scene": scene})


@app.route('/sources', methods=['GET'])
def sources():
    return jsonify({"sources": sources_for_api()})


@app.route('/sources/lightinferra-golden/options', methods=['GET'])
@app.route('/sources/lightinferra-ddn-golden/options', methods=['GET'])
def lightinferra_golden_options_route():
    return jsonify(lightinferra_golden_options())


@app.route('/trace-cards', methods=['GET'])
def trace_cards_route():
    return jsonify({"cards": list_builtin_trace_cards()})


@app.route('/runs/<run_id>/trace-card', methods=['GET'])
def run_trace_card_route(run_id):
    run = storage.get_run(run_id, full=True)
    if not run:
        return jsonify({"error": "run not found"}), 404
    card = storage.load_json_artifact(run_id, "trace_card.json")
    if not card:
        records = storage.load_trace_records(run_id)
        manifest = run.get("manifest") or {}
        if not records or not manifest:
            return jsonify({"error": "Generate the trace before opening its Trace Card"}), 404
        card = build_run_trace_card(run_id, run.get("name") or run_id, records, manifest)
        storage.save_json_artifact(run_id, "trace_card.json", card)
    return jsonify({"card": card})

@app.route('/kv-cache/models', methods=['GET'])
def kv_cache_models():
    return jsonify({
        "models": model_options(),
        "precisions": precision_options(),
        "default_capacity_gib_values": DEFAULT_CAPACITY_GIB,
    })

@app.route('/aiperf/import', methods=['POST'])
def import_aiperf_result():
    payload = request.get_json() or {}
    artifact_dir = payload.get("artifact_dir")
    if not artifact_dir:
        return jsonify({"error": "artifact_dir is required"}), 400
    try:
        result = normalize_aiperf_run(artifact_dir)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)

@app.route('/aiperf/status', methods=['GET'])
def aiperf_status_route():
    engine = request.args.get("engine") or "omlx"
    url = request.args.get("url")
    return jsonify(aiperf_status(engine=engine, url=url))

@app.route('/aiperf/dataset-styles', methods=['GET'])
def aiperf_dataset_styles_route():
    return jsonify({"styles": list_aiperf_dataset_styles()})

@app.route('/aiperf/public-datasets', methods=['GET'])
def aiperf_public_datasets_route():
    return jsonify({"datasets": list_aiperf_public_datasets()})

def _aiperf_validation_options(payload):
    return {
        "mode": payload.get("mode", "analyze"),
        "subset": payload.get("subset"),
        "engine": payload.get("engine"),
        "model": payload.get("model"),
        "tokenizer": payload.get("tokenizer"),
        "url": payload.get("url"),
        "fixed_schedule": payload.get("fixed_schedule", True),
        "slice_duration": payload.get("slice_duration"),
        "block_size": payload.get("block_size"),
        "api_key": payload.get("api_key"),
        "aiperf_dataset_style": payload.get("aiperf_dataset_style") or payload.get("custom_dataset_type"),
        "public_dataset": payload.get("public_dataset") or payload.get("aiperf_public_dataset"),
        "hf_subset": payload.get("hf_subset"),
        "endpoint_type": payload.get("endpoint_type"),
    }

def _trace_params_from_payload(payload):
    reserved = {
        "mode", "subset", "engine", "model", "tokenizer", "url",
        "fixed_schedule", "slice_duration", "block_size", "trace_params",
        "aiperf_dataset_style", "custom_dataset_type", "public_dataset",
        "aiperf_public_dataset", "hf_subset", "endpoint_type",
    }
    if isinstance(payload.get("trace_params"), dict):
        return dict(payload["trace_params"])
    return {key: value for key, value in payload.items() if key not in reserved}

@app.route('/aiperf/validate', methods=['POST'])
def aiperf_validate_route():
    payload = request.get_json() or {}
    trace_params = _trace_params_from_payload(payload)
    if payload.get("aiperf_dataset_style") or payload.get("custom_dataset_type"):
        trace_params["aiperf_dataset_style"] = payload.get("aiperf_dataset_style") or payload.get("custom_dataset_type")
    try:
        reqs, manifest = generate_trace_data(trace_params)
        trace_params = manifest.get("params") or trace_params
        attach_kv_planning(reqs, manifest, trace_params)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    result = validate_generated_trace(
        reqs,
        manifest,
        _aiperf_validation_options(payload),
    )
    return jsonify(result)

@app.route('/generate', methods=['GET', 'POST'])
def generate():
    params = dict(request.args) if request.method in ('GET', 'HEAD') else (request.get_json() or {})
    reqs, manifest = generate_trace_data(params)
    params = manifest.get("params") or params
    attach_kv_planning(reqs, manifest, params)

    # Build zip in memory
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # trace.jsonl
        trace_content = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs)
        zf.writestr('trace.jsonl', trace_content + '\n')
        # manifest.json
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        aiperf_style = normalize_aiperf_dataset_style(params.get("aiperf_dataset_style"))
        aiperf_records = "\n".join(
            json.dumps(row, separators=(",", ":"))
            for row in convert_mooncake_records(reqs, aiperf_style)
        )
        zf.writestr(dataset_file_name(aiperf_style), aiperf_records + ("\n" if aiperf_records else ""))
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

Tip: Launch Tracerator from the repository root with `docker compose up` (add `--build` after backend changes). Open http://localhost:8008 for the Workspace, Profiles, and Compare pages.

VALIDATING WITH AIPERF (recommended for perf handoff)
----------------------------------------------------
These trace.jsonl files use the Mooncake format and are natively supported by
NVIDIA AIPerf via --custom-dataset-type mooncake_trace.

**Canonical full instructions:**
See docs/how-to/validate-with-aiperf.md in this repository.

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
        download_name=trace_download_name(manifest, params, 'zip')
    )

@app.route('/manifest', methods=['POST'])
def manifest_preview():
    """Return just the manifest for preview in UI."""
    params = request.get_json() or {}
    reqs, manifest = generate_trace_data(params)
    params = manifest.get("params") or params
    attach_kv_planning(reqs, manifest, params)
    # Also include a small sample of the trace
    reqs, _ = generate_trace_data({**params, 'scale': min(0.1, float(params.get('scale',1)))})  # small for preview
    sample = '\n'.join(json.dumps(r, separators=(',', ':')) for r in reqs[:5])
    return jsonify({
        'manifest': manifest,
        'sample_trace': sample
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8008, debug=False)
