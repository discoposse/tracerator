"""Pure enrichers: Mooncake trace + manifest (+ optional replay) → inspect view models.

No Flask dependency. Mirrors Post Prompt Viewer's enrich.py pattern for
workload / AIPerf inspection.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BLOCK_SIZE = 512

ISL_BINS = [
    ("<=1K", 1, 1024),
    ("1-2K", 1025, 2048),
    ("2-4K", 2049, 4096),
    ("4-8K", 4097, 8192),
    ("8-16K", 8193, 16384),
    ("16-32K", 16385, 32768),
    ("32-64K", 32769, 65536),
    ("64-128K", 65537, 131072),
    (">128K", 131073, None),
]


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def _isl_bin(length: int) -> str:
    for name, lo, hi in ISL_BINS:
        if length >= lo and (hi is None or length <= hi):
            return name
    return ">128K"


def _distribution(lengths: Iterable[int]) -> Dict[str, Dict[str, float]]:
    values = list(lengths)
    total = max(1, len(values))
    counts = Counter(_isl_bin(int(v)) for v in values)
    return {
        name: {"count": int(counts.get(name, 0)), "share": round(counts.get(name, 0) / total, 6)}
        for name, _, _ in ISL_BINS
    }


def _integrity_ok(records: Sequence[Dict[str, Any]], block_size: int = BLOCK_SIZE) -> Tuple[bool, int]:
    bad = 0
    for row in records:
        isl = int(row.get("input_length") or 0)
        hashes = row.get("hash_ids") or []
        expected = math.ceil(isl / block_size) if isl > 0 else 0
        if not isinstance(hashes, list) or len(hashes) != expected:
            bad += 1
    return bad == 0, bad


def _causal_prefix_hits(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Approximate prefix cache hit ratio by causal block reuse order."""
    seen: set = set()
    hit_blocks = 0
    total_blocks = 0
    block_hits: Counter = Counter()
    for row in records:
        hashes = row.get("hash_ids") or []
        if not isinstance(hashes, list):
            continue
        for hid in hashes:
            total_blocks += 1
            block_hits[hid] += 1
            if hid in seen:
                hit_blocks += 1
            else:
                seen.add(hid)
    ratio = (hit_blocks / total_blocks) if total_blocks else 0.0
    hot = block_hits.most_common(12)
    return {
        "approx_cache_hit_ratio": round(ratio, 6),
        "unique_block_ids": len(seen),
        "total_blocks": total_blocks,
        "hit_blocks": hit_blocks,
        "hot_blocks": [{"block_id": bid, "count": count} for bid, count in hot],
    }


def _prefix_depth(hashes: List[Any], seen_prefixes: Dict[Tuple[Any, ...], int]) -> int:
    """Longest matching prefix length against previously seen requests."""
    best = 0
    for depth in range(1, len(hashes) + 1):
        key = tuple(hashes[:depth])
        if key in seen_prefixes:
            best = depth
        else:
            break
    return best


def _session_groups(records: Sequence[Dict[str, Any]], root_depth: int = 3) -> Dict[str, Any]:
    roots: Counter = Counter()
    for row in records:
        hashes = row.get("hash_ids") or []
        if isinstance(hashes, list) and len(hashes) >= root_depth:
            roots[tuple(hashes[:root_depth])] += 1
    multi = [(root, count) for root, count in roots.items() if count >= 2]
    multi.sort(key=lambda item: item[1], reverse=True)
    return {
        "root_depth": root_depth,
        "distinct_roots": len(roots),
        "multi_turn_roots": len(multi),
        "top_sessions": [
            {"prefix": list(root), "request_count": count}
            for root, count in multi[:15]
        ],
    }


def build_overview(
    records: Sequence[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    manifest = manifest or {}
    timestamps = [int(r.get("timestamp") or 0) for r in records]
    inputs = [int(r.get("input_length") or 0) for r in records]
    outputs = [int(r.get("output_length") or 0) for r in records]
    duration_ms = (max(timestamps) - min(timestamps)) if timestamps else 0
    duration_s = duration_ms / 1000.0 if duration_ms > 0 else 0.0
    rps = (len(records) / duration_s) if duration_s > 0 else None

    by_ts: Counter = Counter(timestamps)
    max_burst = max(by_ts.values()) if by_ts else 0
    integrity_ok, integrity_bad = _integrity_ok(records)
    prefix = _causal_prefix_hits(records)

    summary_bits = []
    n = len(records)
    summary_bits.append(f"{n:,} requests")
    if rps is not None:
        summary_bits.append(f"~{rps:.2f} RPS")
    summary_bits.append(f"burst max {max_burst}")
    summary_bits.append(f"~{prefix['approx_cache_hit_ratio'] * 100:.0f}% prefix hit")
    if replay_result:
        err = (replay_result.get("summary") or {}).get("error_count")
        if err:
            summary_bits.append(f"replay errors {int(err)}")
        else:
            summary_bits.append("replay attached")

    return {
        "request_count": n,
        "duration_ms": duration_ms,
        "duration_s": round(duration_s, 3),
        "rps": round(rps, 4) if rps is not None else None,
        "max_concurrency": max_burst,
        "isl": {
            "p50": _percentile(inputs, 0.5),
            "p95": _percentile(inputs, 0.95),
            "mean": round(sum(inputs) / n, 1) if n else None,
            "distribution": manifest.get("isl_distribution") or _distribution(inputs),
        },
        "osl": {
            "p50": _percentile(outputs, 0.5),
            "p95": _percentile(outputs, 0.95),
            "mean": round(sum(outputs) / n, 1) if n else None,
            "distribution": _distribution(outputs),
        },
        "prefix": prefix,
        "integrity": {
            "ok": integrity_ok,
            "bad_rows": integrity_bad,
            "schema": (manifest.get("integrity") or {}).get("schema", "mooncake_trace"),
            "aiperf_ready": (manifest.get("integrity") or {}).get("aiperf_ready", integrity_ok),
        },
        "source": (manifest.get("base_source") or {}).get("id") or manifest.get("source"),
        "scenario": (manifest.get("trace_scenario") or {}).get("id"),
        "has_analysis": bool(analysis),
        "has_replay": bool(replay_result),
        "auto_summary": " · ".join(summary_bits),
    }


def build_arrival_timeline(
    records: Sequence[Dict[str, Any]],
    *,
    max_points: int = 400,
) -> Dict[str, Any]:
    if not records:
        return {"available": False, "points": [], "buckets": []}

    timestamps = [int(r.get("timestamp") or 0) for r in records]
    t0 = min(timestamps)
    t1 = max(timestamps)
    by_ts: Dict[int, int] = defaultdict(int)
    for ts in timestamps:
        by_ts[ts] += 1

    # Bucket into ~max_points for charting
    span = max(1, t1 - t0)
    bucket_ms = max(1, span // max_points)
    buckets: Dict[int, int] = defaultdict(int)
    for ts, count in by_ts.items():
        key = ((ts - t0) // bucket_ms) * bucket_ms
        buckets[key] += count

    ordered = sorted(buckets.items())
    concurrency_series = [{"t_ms": t, "count": c} for t, c in ordered]
    burst_events = [
        {"t_ms": ts - t0, "absolute_ms": ts, "count": count}
        for ts, count in sorted(by_ts.items(), key=lambda x: -x[1])[:25]
        if count > 1
    ]

    return {
        "available": True,
        "t0_ms": t0,
        "t1_ms": t1,
        "span_ms": span,
        "bucket_ms": bucket_ms,
        "unique_timestamps": len(by_ts),
        "max_burst": max(by_ts.values()) if by_ts else 0,
        "mean_burst": round(sum(by_ts.values()) / len(by_ts), 2) if by_ts else 0,
        "points": concurrency_series,
        "burst_events": burst_events,
    }


def build_prefix_trace(
    records: Sequence[Dict[str, Any]],
    *,
    sample_limit: int = 80,
) -> Dict[str, Any]:
    if not records:
        return {"available": False, "exchanges": [], "sessions": {}}

    prefix_stats = _causal_prefix_hits(records)
    sessions = _session_groups(records)
    seen_prefixes: Dict[Tuple[Any, ...], int] = {}
    exchanges: List[Dict[str, Any]] = []

    step = max(1, len(records) // sample_limit)
    for idx, row in enumerate(records):
        hashes = list(row.get("hash_ids") or [])
        depth = _prefix_depth(hashes, seen_prefixes) if hashes else 0
        cold = max(0, len(hashes) - depth)
        # Register all prefix lengths for subsequent matches
        for d in range(1, len(hashes) + 1):
            key = tuple(hashes[:d])
            seen_prefixes[key] = seen_prefixes.get(key, 0) + 1

        if idx % step != 0 and idx != len(records) - 1:
            continue

        hit_share = (depth / len(hashes)) if hashes else 0.0
        if hit_share >= 0.75:
            verdict = "hot prefix — mostly cacheable prefill"
        elif hit_share >= 0.35:
            verdict = "mixed — partial session / scaffold reuse"
        elif depth > 0:
            verdict = "shallow hit — shared root only"
        else:
            verdict = "cold start — unique blocks"

        exchanges.append({
            "index": idx,
            "timestamp": int(row.get("timestamp") or 0),
            "input_length": int(row.get("input_length") or 0),
            "output_length": int(row.get("output_length") or 0),
            "block_count": len(hashes),
            "prefix_hit_depth": depth,
            "cold_blocks": cold,
            "hit_share": round(hit_share, 4),
            "stages": [
                {"id": "shared", "label": "Shared prefix", "blocks": depth, "ms_weight": depth},
                {"id": "unique", "label": "Unique prefill", "blocks": cold, "ms_weight": cold},
                {"id": "decode", "label": "Decode (OSL)", "blocks": max(1, int(row.get("output_length") or 0) // BLOCK_SIZE), "ms_weight": int(row.get("output_length") or 0)},
            ],
            "hero_blocks": depth,
            "verdict": verdict,
            "hash_preview": hashes[:8],
        })

    return {
        "available": True,
        "sample_count": len(exchanges),
        "total_requests": len(records),
        "prefix": prefix_stats,
        "sessions": sessions,
        "exchanges": exchanges,
    }


def build_waterfall(
    records: Sequence[Dict[str, Any]],
    replay_result: Optional[Dict[str, Any]] = None,
    *,
    max_lanes: int = 120,
) -> Dict[str, Any]:
    if not records:
        return {"available": False, "lanes": []}

    timestamps = [int(r.get("timestamp") or 0) for r in records]
    t0 = min(timestamps)
    # Estimate duration from OSL when no replay latency (tokens as proxy ms)
    ttft_p50 = None
    latency_p50 = None
    if replay_result:
        lat = replay_result.get("latency_ms") or {}
        ttft = lat.get("time_to_first_token") or {}
        req = lat.get("request_latency") or {}
        ttft_p50 = ttft.get("p50") or ttft.get("avg")
        latency_p50 = req.get("p50") or req.get("avg")

    step = max(1, len(records) // max_lanes)
    lanes: List[Dict[str, Any]] = []
    for idx, row in enumerate(records):
        if idx % step != 0 and idx != len(records) - 1:
            continue
        start = int(row.get("timestamp") or 0) - t0
        osl = int(row.get("output_length") or 0)
        isl = int(row.get("input_length") or 0)
        if latency_p50:
            duration = float(latency_p50)
        else:
            # Heuristic: prefill ~ isl/10 + decode ~ osl*2 (display units only)
            duration = max(5.0, isl / 50.0 + osl * 2.0)
        lanes.append({
            "index": idx,
            "start_ms": start,
            "duration_ms": round(duration, 2),
            "end_ms": round(start + duration, 2),
            "input_length": isl,
            "output_length": osl,
            "ttft_ms": ttft_p50,
            "has_replay_latency": bool(latency_p50),
        })

    span = max((lane["end_ms"] for lane in lanes), default=0)
    return {
        "available": True,
        "t0_ms": t0,
        "span_ms": span,
        "lane_count": len(lanes),
        "total_requests": len(records),
        "lanes": lanes,
        "latency_overlay": {
            "ttft_p50_ms": ttft_p50,
            "request_latency_p50_ms": latency_p50,
            "from_replay": bool(replay_result),
        },
    }


def build_events(
    *,
    params: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    overview: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    align: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params or {}
    manifest = manifest or {}
    overview = overview or {}
    events: List[Dict[str, Any]] = []

    source = (manifest.get("base_source") or {}).get("label") or params.get("source") or "unknown"
    scenario = (manifest.get("trace_scenario") or {}).get("label") or params.get("scenario") or "standard"
    aiperf_dataset = manifest.get("aiperf_dataset") or {}
    events.append({
        "type": "generate",
        "title": f"Generated from {source}",
        "detail": f"Scenario: {scenario}. Scale={params.get('scale', manifest.get('effective_scale', '?'))}.",
        "severity": "ok",
    })

    integrity = overview.get("integrity") or {}
    if integrity.get("ok"):
        events.append({
            "type": "integrity",
            "title": "Trace integrity passed",
            "detail": "hash_ids length matches ceil(input_length / 512); Mooncake / AIPerf ready.",
            "severity": "ok",
        })
    else:
        events.append({
            "type": "integrity",
            "title": "Integrity issues detected",
            "detail": f"{integrity.get('bad_rows', '?')} rows fail the hash_ids rule.",
            "severity": "error",
        })

    n = overview.get("request_count")
    burst = overview.get("max_concurrency")
    hit = (overview.get("prefix") or {}).get("approx_cache_hit_ratio")
    events.append({
        "type": "workload",
        "title": "Workload shape",
        "detail": f"{n} requests, burst max {burst}, approx prefix hit {hit}.",
        "severity": "info",
    })
    events.append({
        "type": "aiperf_dataset",
        "title": f"AIPerf dataset: {aiperf_dataset.get('label') or aiperf_dataset.get('id') or 'Mooncake trace replay'}",
        "detail": aiperf_dataset.get("best_for") or "AIPerf-ready custom dataset replay.",
        "severity": "info",
    })

    if analysis:
        events.append({
            "type": "analyze",
            "title": "AIPerf analyze-trace completed",
            "detail": "Static analysis attached to this run.",
            "severity": "ok",
        })
    else:
        events.append({
            "type": "analyze",
            "title": "AIPerf analyze pending",
            "detail": "Run Validate to confirm the trace structure with aiperf analyze-trace.",
            "severity": "pending",
        })

    if replay_result:
        summary = replay_result.get("summary") or {}
        err = summary.get("error_count") or 0
        events.append({
            "type": "replay",
            "title": "Replay results attached",
            "detail": (
                f"{int(summary.get('request_count') or 0)} requests, "
                f"{int(err)} errors, "
                f"throughput {summary.get('request_throughput')}"
            ),
            "severity": "warn" if err else "ok",
        })
        ttft = ((replay_result.get("latency_ms") or {}).get("time_to_first_token") or {})
        if ttft.get("p95") is not None:
            events.append({
                "type": "latency",
                "title": f"TTFT p95 = {ttft.get('p95'):.0f} ms",
                "detail": f"p50={ttft.get('p50')}, avg={ttft.get('avg')}",
                "severity": "info",
            })
    else:
        events.append({
            "type": "replay",
            "title": "No replay yet",
            "detail": "Replay in AIPerf or upload a replay_result.json / artifact directory.",
            "severity": "pending",
        })

    if align and align.get("available"):
        mismatch = align.get("isl_mismatch_bins") or []
        if mismatch:
            events.append({
                "type": "align",
                "title": "Expected vs observed ISL drift",
                "detail": f"Bins with largest share delta: {', '.join(mismatch[:3])}",
                "severity": "warn",
            })
        else:
            events.append({
                "type": "align",
                "title": "Observed ISL aligns with manifest",
                "detail": "Replay length mix matches the generated workload distribution.",
                "severity": "ok",
            })

    return {"available": True, "events": events}


def align_expected_vs_observed(
    manifest: Optional[Dict[str, Any]],
    replay_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not replay_result:
        return {"available": False, "reason": "no_replay"}

    expected = (manifest or {}).get("isl_distribution") or {}
    observed = replay_result.get("observed_isl_distribution") or {}
    bins = []
    mismatch_bins: List[str] = []
    for name, _, _ in ISL_BINS:
        exp_share = float((expected.get(name) or {}).get("share") or 0)
        obs_share = float((observed.get(name) or {}).get("share") or 0)
        delta = round(obs_share - exp_share, 6)
        bins.append({
            "bin": name,
            "expected_share": exp_share,
            "observed_share": obs_share,
            "expected_count": (expected.get(name) or {}).get("count"),
            "observed_count": (observed.get(name) or {}).get("count"),
            "delta_share": delta,
        })
        if abs(delta) >= 0.05:
            mismatch_bins.append(name)

    mismatch_bins.sort(
        key=lambda b: abs(next(x["delta_share"] for x in bins if x["bin"] == b)),
        reverse=True,
    )

    summary = replay_result.get("summary") or {}
    profile = replay_result.get("profile_records") or {}
    timeslices = replay_result.get("timeslices") or {}

    return {
        "available": True,
        "isl_bins": bins,
        "isl_mismatch_bins": mismatch_bins,
        "error_types": profile.get("error_types") or {},
        "error_count": summary.get("error_count") or profile.get("error_count"),
        "success_count": summary.get("success_count") or profile.get("success_count"),
        "latency_ms": replay_result.get("latency_ms") or {},
        "timeslices": timeslices,
        "osl_mismatch_count": summary.get("osl_mismatch_count"),
    }


def build_narrative(
    records: Sequence[Dict[str, Any]],
    *,
    params: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    overview: Optional[Dict[str, Any]] = None,
    prefix_trace: Optional[Dict[str, Any]] = None,
    timeline: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    align: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    overview = overview or build_overview(records, manifest, replay_result)
    timeline = timeline or build_arrival_timeline(records)
    prefix_trace = prefix_trace or build_prefix_trace(records)
    params = params or {}
    manifest = manifest or {}

    source = (manifest.get("base_source") or {}).get("label") or params.get("source") or "baseline"
    scenario = (manifest.get("trace_scenario") or {}).get("label") or "standard"
    aiperf_dataset = manifest.get("aiperf_dataset") or {}
    dataset_label = aiperf_dataset.get("label") or aiperf_dataset.get("id") or "Mooncake trace replay"
    dataset_version = aiperf_dataset.get("version") or "trace_replay"
    n = overview["request_count"]
    rps = overview.get("rps")
    burst = overview.get("max_concurrency")
    hit = (overview.get("prefix") or {}).get("approx_cache_hit_ratio") or 0
    isl_p50 = (overview.get("isl") or {}).get("p50")
    isl_p95 = (overview.get("isl") or {}).get("p95")
    osl_p50 = (overview.get("osl") or {}).get("p50")
    sessions = (prefix_trace.get("sessions") or {})
    multi = sessions.get("multi_turn_roots") or 0

    paragraphs = []
    paragraphs.append(
        f"This run generated a Mooncake-schema workload from **{source}** "
        f"({scenario}) with **{n:,}** requests"
        + (f" at roughly **{rps:.2f} RPS**" if rps else "")
        + f". Arrival pattern shows a maximum simultaneous burst of **{burst}** "
        f"requests at a single timestamp"
        + (
            f" (mean burst {timeline.get('mean_burst')})."
            if timeline.get("available")
            else "."
        )
    )
    paragraphs.append(
        f"The aiperf-facing dataset profile is **{dataset_label}** "
        f"(`{dataset_version}`): {aiperf_dataset.get('description') or 'AIPerf-ready trace replay dataset.'}"
    )
    paragraphs.append(
        f"Input lengths are heavy-tailed (p50 **{isl_p50}**, p95 **{isl_p95}** tokens); "
        f"outputs center near p50 **{osl_p50}**. Causal prefix analysis estimates "
        f"**{hit * 100:.1f}%** block hit ratio across **{(overview.get('prefix') or {}).get('unique_block_ids')}** "
        f"unique blocks, with **{multi}** multi-request session roots at depth "
        f"{sessions.get('root_depth', 3)} — the enterprise interleaved pattern AIPerf "
        f"exercises for KV reuse."
    )

    bullets = [
        f"Integrity: {'passed' if (overview.get('integrity') or {}).get('ok') else 'FAILED'} for AIPerf {(aiperf_dataset.get('id') or 'mooncake_trace')} replay",
        f"AIPerf dataset profile: {dataset_label} — {aiperf_dataset.get('best_for') or 'custom dataset replay'}",
        f"Hot blocks: top reuse counts {[b['count'] for b in ((overview.get('prefix') or {}).get('hot_blocks') or [])[:5]]}",
        f"Generation params: scale={params.get('scale')}, reuse_bias={params.get('reuse_bias')}, isl_profile={params.get('isl_profile')}",
    ]

    if replay_result:
        summary = replay_result.get("summary") or {}
        ttft = ((replay_result.get("latency_ms") or {}).get("time_to_first_token") or {})
        paragraphs.append(
            f"Replay against **{(replay_result.get('run') or {}).get('model') or 'the inference server'}** "
            f"completed **{int(summary.get('success_count') or 0)}** successful of "
            f"**{int(summary.get('request_count') or 0)}** requests "
            f"(errors: **{int(summary.get('error_count') or 0)}**). "
            f"TTFT p50/p95 were **{ttft.get('p50')}** / **{ttft.get('p95')}** ms."
        )
        bullets.append(
            f"Throughput: request={summary.get('request_throughput')}, "
            f"output_tokens={summary.get('output_token_throughput')}"
        )
        if align and align.get("isl_mismatch_bins"):
            bullets.append(
                f"ISL drift vs manifest in bins: {', '.join(align['isl_mismatch_bins'][:5])}"
            )
        else:
            bullets.append("Observed ISL distribution aligns with the generated manifest")
    else:
        paragraphs.append(
            "No replay results are attached yet. Validate with AIPerf analyze-trace, "
            "then replay or upload `replay_result.json` to complete the performance narrative."
        )

    markdown = "## Workload narrative\n\n" + "\n\n".join(paragraphs) + "\n\n### Highlights\n\n"
    markdown += "\n".join(f"- {b}" for b in bullets) + "\n"

    return {
        "summary": paragraphs[0],
        "paragraphs": paragraphs,
        "bullets": bullets,
        "markdown": markdown,
        "source": source,
        "scenario": scenario,
    }


def enrich_run(
    records: Sequence[Dict[str, Any]],
    *,
    params: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    overview = build_overview(records, manifest, replay_result, analysis)
    timeline = build_arrival_timeline(records)
    prefix = build_prefix_trace(records)
    waterfall = build_waterfall(records, replay_result)
    align = align_expected_vs_observed(manifest, replay_result)
    events = build_events(
        params=params,
        manifest=manifest,
        overview=overview,
        analysis=analysis,
        replay_result=replay_result,
        align=align,
    )
    narrative = build_narrative(
        records,
        params=params,
        manifest=manifest,
        overview=overview,
        prefix_trace=prefix,
        timeline=timeline,
        replay_result=replay_result,
        align=align,
    )
    sample = [
        {
            "timestamp": r.get("timestamp"),
            "input_length": r.get("input_length"),
            "output_length": r.get("output_length"),
            "hash_ids_len": len(r.get("hash_ids") or []),
            "hash_ids_preview": (r.get("hash_ids") or [])[:6],
        }
        for r in records[:8]
    ]
    return {
        "overview": overview,
        "timeline": timeline,
        "prefix": prefix,
        "waterfall": waterfall,
        "events": events,
        "align": align,
        "narrative": narrative,
        "sample_trace": sample,
        "workload": {
            "isl_distribution": overview["isl"]["distribution"],
            "osl_distribution": overview["osl"]["distribution"],
            "params": params or {},
            "manifest_stats": {
                "n_requests": manifest.get("n_requests") if manifest else None,
                "approx_cache_hit_ratio": manifest.get("approx_cache_hit_ratio") if manifest else None,
                "max_concurrency": manifest.get("max_concurrency") if manifest else None,
            },
        },
    }
