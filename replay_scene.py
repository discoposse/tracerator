"""Build a compact, deterministic scene model for trace replay visualization."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "tracerator.replay_scene.v1"


PHYSICAL_NODES = [
    {"id": "ingress", "label": "Ingress", "kind": "gateway", "position": [-7.0, 0.0, 0.0]},
    {"id": "queue", "label": "Queue", "kind": "queue", "position": [-4.2, 0.0, 0.0]},
    {"id": "prefill", "label": "Prefill", "kind": "compute", "position": [-1.4, 1.1, 0.0]},
    {"id": "cache", "label": "KV cache", "kind": "memory", "position": [-1.4, -1.5, 0.0]},
    {"id": "decode", "label": "Decode", "kind": "compute", "position": [2.0, 0.0, 0.0]},
    {"id": "egress", "label": "Response", "kind": "gateway", "position": [5.4, 0.0, 0.0]},
]


def _prefix_key(row: Dict[str, Any], depth: int = 3) -> Tuple[Any, ...]:
    hashes = list(row.get("hash_ids") or [])
    return tuple(hashes[: min(depth, len(hashes))])


def _prefix_id(key: Tuple[Any, ...]) -> str:
    if not key:
        return "cold"
    return "p-" + "-".join(str(value) for value in key)


def _causal_depths(records: Sequence[Dict[str, Any]]) -> List[int]:
    seen = set()
    depths = []
    for row in records:
        hashes = list(row.get("hash_ids") or [])
        depth = 0
        for index in range(1, len(hashes) + 1):
            if tuple(hashes[:index]) in seen:
                depth = index
            else:
                break
        depths.append(depth)
        for index in range(1, len(hashes) + 1):
            seen.add(tuple(hashes[:index]))
    return depths


def _prefix_nodes(records: Sequence[Dict[str, Any]], limit: int = 8) -> Tuple[List[Dict[str, Any]], Dict[Tuple[Any, ...], str]]:
    counts = Counter(_prefix_key(row) for row in records)
    hottest = [key for key, _ in counts.most_common(limit) if key]
    mapping = {key: _prefix_id(key) for key in hottest}
    nodes = [{
        "id": "workload",
        "label": "Workload",
        "kind": "gateway",
        "request_count": len(records),
        "hash_preview": [],
        "position": [0.0, 0.0, 0.0],
    }]
    radius = 4.5
    for index, key in enumerate(hottest):
        angle = (index / max(1, len(hottest))) * math.tau
        nodes.append({
            "id": mapping[key],
            "label": f"P{index + 1}",
            "kind": "prefix",
            "request_count": counts[key],
            "hash_preview": list(key),
            "position": [
                round(math.cos(angle) * radius, 4),
                round(math.sin(angle) * radius * 0.68, 4),
                round(math.sin(angle * 2) * 1.5, 4),
            ],
        })
    nodes.append({
        "id": "cold",
        "label": "Long tail",
        "kind": "prefix",
        "request_count": len(records) - sum(counts[key] for key in hottest),
        "hash_preview": [],
        "position": [0.0, -4.6, -0.8],
    })
    return nodes, mapping


def _request_samples(
    records: Sequence[Dict[str, Any]],
    prefix_mapping: Dict[Tuple[Any, ...], str],
    causal_depths: Sequence[int],
    sizing_result: Optional[Dict[str, Any]],
    max_requests: int,
) -> List[Dict[str, Any]]:
    timing = {
        int(item.get("index")): item
        for item in (sizing_result or {}).get("recommended_request_samples") or []
        if item.get("index") is not None
    }
    timestamps = [int(row.get("timestamp") or 0) for row in records]
    t0 = min(timestamps) if timestamps else 0
    step = max(1, len(records) // max_requests)
    sampled = []
    for index, row in enumerate(records):
        if index % step != 0 and index != len(records) - 1:
            continue
        hashes = list(row.get("hash_ids") or [])
        depth = int(causal_depths[index])
        timing_row = timing.get(index) or {}
        arrival = int(row.get("timestamp") or 0) - t0
        estimated_duration = max(
            40.0,
            float(row.get("input_length") or 0) / 50.0
            + float(row.get("output_length") or 0) * 2.0,
        )
        completion = float(timing_row.get("completion_ms") or (arrival + estimated_duration))
        sampled.append({
            "id": f"r-{index}",
            "index": index,
            "arrival_ms": round(float(timing_row.get("arrival_ms") or arrival), 3),
            "start_ms": round(float(timing_row.get("start_ms") or arrival), 3),
            "completion_ms": round(completion, 3),
            "queue_ms": round(float(timing_row.get("queue_ms") or 0), 3),
            "input_tokens": int(row.get("input_length") or 0),
            "output_tokens": int(row.get("output_length") or 0),
            "block_count": len(hashes),
            "prefix_hit_depth": depth,
            "hit_share": round(depth / len(hashes), 4) if hashes else 0.0,
            "prefix_id": prefix_mapping.get(_prefix_key(row), "cold"),
            "replica": int(timing_row.get("replica") or 0),
        })
        if len(sampled) >= max_requests:
            break
    return sampled


def _frames(
    records: Sequence[Dict[str, Any]],
    causal_depths: Sequence[int],
    sizing_result: Optional[Dict[str, Any]],
    max_frames: int,
) -> List[Dict[str, Any]]:
    if not records:
        return []
    timestamps = [int(row.get("timestamp") or 0) for row in records]
    t0 = min(timestamps)
    span = max(1, max(timestamps) - t0)
    bucket_ms = max(1, int(math.ceil(span / max_frames)))
    buckets: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "hit_share_sum": 0.0,
        "block_count": 0,
    })
    for index, row in enumerate(records):
        bucket = min(max_frames - 1, (int(row.get("timestamp") or 0) - t0) // bucket_ms)
        hashes = list(row.get("hash_ids") or [])
        item = buckets[int(bucket)]
        item["requests"] += 1
        item["input_tokens"] += int(row.get("input_length") or 0)
        item["output_tokens"] += int(row.get("output_length") or 0)
        item["block_count"] += len(hashes)
        item["hit_share_sum"] += causal_depths[index] / len(hashes) if hashes else 0.0

    recommendation = (sizing_result or {}).get("recommendation") or {}
    capacity = max(1, int(recommendation.get("capacity_slots") or 1))
    cache = (sizing_result or {}).get("cache") or {}
    working_set = float(cache.get("working_set_gib") or 0)
    cache_gib = float((sizing_result or {}).get("inputs", {}).get("cache_gib_per_replica") or 0)
    cache_pressure = min(1.0, working_set / cache_gib) if cache_gib > 0 else 0.0

    frames = []
    rolling_running = 0.0
    for index in range(max(buckets.keys(), default=0) + 1):
        item = buckets.get(index) or {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "hit_share_sum": 0.0,
            "block_count": 0,
        }
        rolling_running = max(0.0, rolling_running * 0.65 + item["requests"])
        frames.append({
            "index": index,
            "t_ms": index * bucket_ms,
            "request_count": item["requests"],
            "input_tokens": item["input_tokens"],
            "output_tokens": item["output_tokens"],
            "average_hit_share": round(
                item["hit_share_sum"] / item["requests"] if item["requests"] else 0.0,
                4,
            ),
            "running_estimate": round(min(capacity, rolling_running), 3),
            "queue_pressure": round(max(0.0, rolling_running - capacity) / capacity, 4),
            "cache_pressure": round(cache_pressure, 4),
        })
    return frames


def build_replay_scene(
    records: Sequence[Dict[str, Any]],
    *,
    manifest: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    sizing_result: Optional[Dict[str, Any]] = None,
    max_requests: int = 600,
    max_frames: int = 180,
) -> Dict[str, Any]:
    """Return a bounded scene document for physical and logical replay views."""
    if not records:
        raise ValueError("Replay Map requires a generated trace")
    max_requests = max(20, min(1200, int(max_requests)))
    max_frames = max(20, min(360, int(max_frames)))
    causal_depths = _causal_depths(records)
    logical_nodes, prefix_mapping = _prefix_nodes(records)
    requests = _request_samples(
        records,
        prefix_mapping,
        causal_depths,
        sizing_result,
        max_requests,
    )
    frames = _frames(records, causal_depths, sizing_result, max_frames)
    timestamps = [int(row.get("timestamp") or 0) for row in records]
    prefix_counts = Counter(request["prefix_id"] for request in requests)
    recommendation = (sizing_result or {}).get("recommendation") or {}
    replay_latency = (replay_result or {}).get("latency_ms") or {}
    return {
        "artifact_type": "trace_replay_scene",
        "schema_version": SCHEMA_VERSION,
        "source": {
            "request_count": len(records),
            "sampled_requests": len(requests),
            "t0_ms": min(timestamps),
            "span_ms": max(1, max(timestamps) - min(timestamps)),
            "manifest_generator": (manifest or {}).get("generator"),
            "has_replay": bool(replay_result),
            "has_sizing": bool(sizing_result),
        },
        "views": {
            "physical": {
                "nodes": PHYSICAL_NODES,
                "edges": [
                    ["ingress", "queue"],
                    ["queue", "prefill"],
                    ["prefill", "cache"],
                    ["cache", "decode"],
                    ["decode", "egress"],
                ],
            },
            "logical": {
                "nodes": logical_nodes,
                "edges": [
                    ["workload", node["id"]]
                    for node in logical_nodes
                    if node["request_count"] > 0
                ],
            },
        },
        "requests": requests,
        "frames": frames,
        "metrics": {
            "prefix_families": dict(prefix_counts),
            "causal_hit_share": round(
                sum(request["hit_share"] for request in requests) / max(1, len(requests)),
                6,
            ),
            "sizing": {
                "replicas": recommendation.get("replicas"),
                "gpus": recommendation.get("gpus"),
                "capacity_slots": recommendation.get("capacity_slots"),
                "ttft_p95_ms": (recommendation.get("ttft_ms") or {}).get("p95"),
            },
            "observed": {
                "ttft_p95_ms": (replay_latency.get("time_to_first_token") or {}).get("p95"),
                "request_latency_p95_ms": (replay_latency.get("request_latency") or {}).get("p95"),
            },
        },
        "encoding": {
            "particle": "sampled request",
            "particle_size": "input token count",
            "particle_brightness": "prefix hit share",
            "physical_position": "request lifecycle stage",
            "logical_orbit": "shared prefix family",
            "cache_fill": "working-set pressure",
        },
    }
