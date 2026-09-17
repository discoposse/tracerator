"""Trace-driven sizing studies using llm-d inference simulator semantics.

Tracerator remains the workload and KV-cache authority. This module applies the
documented llm-d per-token latency and load-factor model to that workload, then
sweeps replica counts against a latency SLO. The emitted YAML can be used to
calibrate the estimate with llm-d-inference-sim and AIPerf.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analytical_sizing import (
    PROFILE_DEFAULTS,
    build_analytical_envelope,
    list_analytical_catalog,
    seed_latency_profile,
)


SCHEMA_VERSION = "tracerator.sizing_result.v1"
LLMD_SOURCE = "https://github.com/llm-d/llm-d-inference-sim"


PROFILES: Dict[str, Dict[str, Any]] = {
    "8b-h100-balanced": {
        "id": "8b-h100-balanced",
        "label": "8B on H100, balanced",
        "description": "Single H100 replica with moderate concurrency.",
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "hardware": "NVIDIA H100",
        "gpus_per_replica": 1,
        "max_num_seqs": 16,
        "prefill_overhead_ms": 30.0,
        "prefill_time_per_token_ms": 0.25,
        "inter_token_latency_ms": 12.0,
        "kv_transfer_time_per_token_ms": 0.003,
        "time_factor_under_load": 2.0,
        "jitter": {
            "prefill_ms": 5.0,
            "inter_token_ms": 2.0,
            "kv_transfer_ms": 0.2,
        },
    },
    "70b-h100-tp8-throughput": {
        "id": "70b-h100-tp8-throughput",
        "label": "70B on 8x H100, throughput",
        "description": "Tensor-parallel H100 replica tuned near saturation.",
        "model": "meta-llama/Llama-3.1-70B-Instruct",
        "hardware": "8x NVIDIA H100",
        "gpus_per_replica": 8,
        "max_num_seqs": 64,
        "prefill_overhead_ms": 80.0,
        "prefill_time_per_token_ms": 0.5,
        "inter_token_latency_ms": 25.0,
        "kv_transfer_time_per_token_ms": 0.008,
        "time_factor_under_load": 3.0,
        "jitter": {
            "prefill_ms": 15.0,
            "inter_token_ms": 4.0,
            "kv_transfer_ms": 0.5,
        },
    },
    "small-l40s-edge": {
        "id": "small-l40s-edge",
        "label": "1-3B on L40S, low latency",
        "description": "Small edge model with low sequence concurrency.",
        "model": "meta-llama/Llama-3.2-3B-Instruct",
        "hardware": "NVIDIA L40S",
        "gpus_per_replica": 1,
        "max_num_seqs": 4,
        "prefill_overhead_ms": 20.0,
        "prefill_time_per_token_ms": 0.35,
        "inter_token_latency_ms": 15.0,
        "kv_transfer_time_per_token_ms": 0.012,
        "time_factor_under_load": 1.5,
        "jitter": {
            "prefill_ms": 3.0,
            "inter_token_ms": 2.0,
            "kv_transfer_ms": 0.5,
        },
    },
}


def list_sizing_profiles() -> List[Dict[str, Any]]:
    """Return stable UI-facing profile metadata."""
    return [
        {
            "id": profile["id"],
            "label": profile["label"],
            "description": profile["description"],
            "model": profile["model"],
            "hardware": profile["hardware"],
            "gpus_per_replica": profile["gpus_per_replica"],
            "max_num_seqs": profile["max_num_seqs"],
            "source": LLMD_SOURCE,
            "analytical_defaults": {
                "model_id": PROFILE_DEFAULTS[profile["id"]][0],
                "hardware_id": PROFILE_DEFAULTS[profile["id"]][1],
                "tensor_parallel": PROFILE_DEFAULTS[profile["id"]][2],
            },
        }
        for profile in PROFILES.values()
    ]


def sizing_catalog() -> Dict[str, Any]:
    """Return latency profiles and the analytical model/hardware catalog."""
    return {
        "profiles": list_sizing_profiles(),
        **list_analytical_catalog(),
    }


def _number(value: Any, default: float, minimum: Optional[float] = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _integer(value: Any, default: int, minimum: int = 1, maximum: int = 256) -> int:
    return max(minimum, min(maximum, int(_number(value, default, minimum))))


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _causal_hit_rate(records: Sequence[Dict[str, Any]], block_size: int = 512) -> float:
    seen = set()
    hit_tokens = 0
    total_tokens = 0
    for row in records:
        hashes = list(row.get("hash_ids") or [])
        input_length = max(0, int(row.get("input_length") or 0))
        total_tokens += input_length
        for index, block_id in enumerate(hashes):
            block_tokens = min(block_size, max(0, input_length - index * block_size))
            if block_id in seen:
                hit_tokens += block_tokens
            seen.add(block_id)
    return hit_tokens / total_tokens if total_tokens else 0.0


def _cache_point(
    manifest: Optional[Dict[str, Any]],
    cache_gib: float,
    fallback_hit_rate: float,
) -> Dict[str, Any]:
    planning = (manifest or {}).get("kv_cache_planning") or {}
    points = planning.get("points") or []
    if points:
        point = min(points, key=lambda item: abs(float(item.get("gib") or 0) - cache_gib))
        lru = (point.get("results") or {}).get("lru") or {}
        hit_rate = lru.get("hit_rate")
        return {
            "requested_gib": round(cache_gib, 3),
            "modeled_gib": float(point.get("gib") or cache_gib),
            "hit_rate": float(fallback_hit_rate if hit_rate is None else hit_rate),
            "cache_blocks": int(point.get("cache_blocks") or 0),
            "bytes_per_block": float(planning.get("bytes_per_block") or 0),
            "working_set_gib": float(planning.get("working_set_gib") or 0),
            "source": "tracerator_kv_cache_planning",
        }
    return {
        "requested_gib": round(cache_gib, 3),
        "modeled_gib": round(cache_gib, 3),
        "hit_rate": fallback_hit_rate,
        "cache_blocks": 0,
        "bytes_per_block": 0,
        "working_set_gib": None,
        "source": "causal_prefix_estimate",
    }


def _calibrated_profile(
    profile: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    replay_result: Optional[Dict[str, Any]],
    calibration: str,
    hit_rate: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    resolved = dict(profile)
    resolved["jitter"] = dict(profile.get("jitter") or {})
    use_replay = calibration in ("auto", "replay") and replay_result
    latency = (replay_result or {}).get("latency_ms") or {}
    observed_ttft = (latency.get("time_to_first_token") or {}).get("p50")
    observed_itl = (latency.get("inter_token_latency") or {}).get("p50")
    if not use_replay or observed_ttft is None:
        return resolved, {
            "mode": "profile",
            "source": profile["id"],
            "observed_ttft_p50_ms": None,
            "observed_itl_p50_ms": None,
        }

    uncached_inputs = [
        max(1.0, float(row.get("input_length") or 0) * max(0.001, 1.0 - hit_rate))
        for row in records
    ]
    median_uncached = _percentile(uncached_inputs, 0.5)
    observed_ttft = _number(observed_ttft, resolved["prefill_overhead_ms"], 1.0)
    overhead = max(5.0, observed_ttft * 0.2)
    resolved["prefill_overhead_ms"] = round(overhead, 6)
    resolved["prefill_time_per_token_ms"] = round(
        max(0.001, (observed_ttft - overhead) / max(1.0, median_uncached)),
        6,
    )
    if observed_itl is not None:
        resolved["inter_token_latency_ms"] = round(_number(observed_itl, resolved["inter_token_latency_ms"], 0.1), 6)
    return resolved, {
        "mode": "replay",
        "source": "aiperf_replay_result",
        "observed_ttft_p50_ms": observed_ttft,
        "observed_itl_p50_ms": observed_itl,
        "median_uncached_tokens": round(median_uncached, 3),
        "note": "Profile prefill slope and ITL were anchored to attached AIPerf p50 observations.",
    }


@dataclass
class _Replica:
    completions: List[float]


def _candidate_slot(replica: _Replica, arrival_ms: float, max_num_seqs: int) -> float:
    while replica.completions and replica.completions[0] <= arrival_ms:
        heapq.heappop(replica.completions)
    if len(replica.completions) < max_num_seqs:
        return arrival_ms
    return replica.completions[0]


def _simulate_candidate(
    records: Sequence[Dict[str, Any]],
    *,
    replicas: int,
    profile: Dict[str, Any],
    hit_rate: float,
    topology: str,
    target_ttft_p95_ms: float,
    max_samples: int = 320,
) -> Dict[str, Any]:
    max_num_seqs = int(profile["max_num_seqs"])
    workers = [_Replica([]) for _ in range(replicas)]
    ordered = sorted(enumerate(records), key=lambda item: (int(item[1].get("timestamp") or 0), item[0]))
    if not ordered:
        return {
            "replicas": replicas,
            "gpus": replicas * int(profile["gpus_per_replica"]),
            "meets_slo": False,
            "reason": "empty_trace",
        }

    t0 = float(int(ordered[0][1].get("timestamp") or 0))
    ttfts: List[float] = []
    queues: List[float] = []
    latencies: List[float] = []
    active_levels: List[int] = []
    samples: List[Dict[str, Any]] = []
    sample_step = max(1, len(ordered) // max_samples)
    total_service_ms = 0.0
    final_completion = t0

    for position, (source_index, row) in enumerate(ordered):
        arrival = float(int(row.get("timestamp") or 0))
        candidates = [
            (_candidate_slot(worker, arrival, max_num_seqs), len(worker.completions), index)
            for index, worker in enumerate(workers)
        ]
        start, _, worker_index = min(candidates)
        worker = workers[worker_index]
        while worker.completions and worker.completions[0] <= start:
            heapq.heappop(worker.completions)

        concurrency = min(max_num_seqs, len(worker.completions) + 1)
        if max_num_seqs <= 1:
            load_factor = 1.0
        else:
            load_factor = 1.0 + (
                (float(profile["time_factor_under_load"]) - 1.0)
                * ((concurrency - 1) / (max_num_seqs - 1))
            )

        input_tokens = max(0, int(row.get("input_length") or 0))
        output_tokens = max(1, int(row.get("output_length") or 1))
        uncached_tokens = input_tokens * max(0.001, 1.0 - hit_rate)
        prefill_ms = (
            float(profile["prefill_overhead_ms"])
            + uncached_tokens * float(profile["prefill_time_per_token_ms"])
        ) * load_factor
        transfer_ms = (
            input_tokens * float(profile["kv_transfer_time_per_token_ms"])
            if topology == "disaggregated"
            else 0.0
        )
        decode_ms = (
            max(0, output_tokens - 1)
            * float(profile["inter_token_latency_ms"])
            * load_factor
        )
        queue_ms = max(0.0, start - arrival)
        ttft_ms = queue_ms + prefill_ms + transfer_ms
        request_latency_ms = ttft_ms + decode_ms
        completion = arrival + request_latency_ms
        heapq.heappush(worker.completions, completion)

        ttfts.append(ttft_ms)
        queues.append(queue_ms)
        latencies.append(request_latency_ms)
        active_levels.append(concurrency)
        total_service_ms += prefill_ms + transfer_ms + decode_ms
        final_completion = max(final_completion, completion)

        if position % sample_step == 0 or position == len(ordered) - 1:
            samples.append({
                "index": source_index,
                "arrival_ms": round(arrival - t0, 3),
                "start_ms": round(start - t0, 3),
                "completion_ms": round(completion - t0, 3),
                "queue_ms": round(queue_ms, 3),
                "ttft_ms": round(ttft_ms, 3),
                "latency_ms": round(request_latency_ms, 3),
                "replica": worker_index,
                "concurrency": concurrency,
            })

    span_ms = max(1.0, final_completion - t0)
    capacity_slots = replicas * max_num_seqs
    utilization = min(1.0, total_service_ms / (span_ms * max(1, capacity_slots)))
    ttft_p95 = _percentile(ttfts, 0.95)
    queue_p95 = _percentile(queues, 0.95)
    return {
        "replicas": replicas,
        "gpus": replicas * int(profile["gpus_per_replica"]),
        "max_num_seqs_per_replica": max_num_seqs,
        "capacity_slots": capacity_slots,
        "meets_slo": ttft_p95 <= target_ttft_p95_ms,
        "ttft_ms": {
            "p50": round(_percentile(ttfts, 0.5), 3),
            "p95": round(ttft_p95, 3),
            "p99": round(_percentile(ttfts, 0.99), 3),
        },
        "queue_ms": {
            "p50": round(_percentile(queues, 0.5), 3),
            "p95": round(queue_p95, 3),
            "p99": round(_percentile(queues, 0.99), 3),
        },
        "request_latency_ms": {
            "p50": round(_percentile(latencies, 0.5), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "p99": round(_percentile(latencies, 0.99), 3),
        },
        "mean_active_sequences": round(mean(active_levels), 3),
        "modeled_utilization": round(utilization, 6),
        "modeled_span_ms": round(span_ms, 3),
        "request_samples": samples,
    }


def _duration(value_ms: float) -> str:
    if value_ms < 1.0:
        return f"{round(value_ms * 1000, 3):g}us"
    return f"{round(value_ms, 3):g}ms"


def _llmd_config(
    profile: Dict[str, Any],
    cache: Dict[str, Any],
    topology: str,
) -> Dict[str, Any]:
    tracerator_blocks = int(cache.get("cache_blocks") or 0)
    sim_block_size = 128
    sim_blocks = tracerator_blocks * 4 if tracerator_blocks else 0
    config: Dict[str, Any] = {
        "port": 8000,
        "model": profile["model"],
        "max-num-seqs": int(profile["max_num_seqs"]),
        "mode": "random",
        "latency-calculator": "per-token",
        "inter-token-latency": _duration(float(profile["inter_token_latency_ms"])),
        "inter-token-latency-std-dev": _duration(float(profile["jitter"]["inter_token_ms"])),
        "prefill-overhead": _duration(float(profile["prefill_overhead_ms"])),
        "prefill-time-per-token": _duration(float(profile["prefill_time_per_token_ms"])),
        "prefill-time-std-dev": _duration(float(profile["jitter"]["prefill_ms"])),
        "kv-cache-transfer-time-per-token": _duration(float(profile["kv_transfer_time_per_token_ms"])),
        "kv-cache-transfer-time-std-dev": _duration(float(profile["jitter"]["kv_transfer_ms"])),
        "time-factor-under-load": float(profile["time_factor_under_load"]),
        "block-size": sim_block_size,
    }
    if sim_blocks:
        config["kv-cache-size"] = sim_blocks
    if topology == "disaggregated":
        config["role"] = "both"

    quoted = {"model", "mode", "latency-calculator", "role"}
    lines = [
        "# Generated by Tracerator from an llm-d latency profile.",
        "# Calibrate with AIPerf before treating this estimate as production capacity.",
    ]
    for key, value in config.items():
        rendered = f'"{value}"' if key in quoted else str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"{key}: {rendered}")
    return {
        "format": "llm-d-inference-sim-config",
        "source": LLMD_SOURCE,
        "config": config,
        "yaml": "\n".join(lines) + "\n",
        "block_translation": {
            "tracerator_tokens_per_block": 512,
            "llmd_tokens_per_block": sim_block_size,
            "reason": "llm-d-inference-sim supports block sizes up to 128 tokens.",
        },
    }


def build_sizing_study(
    records: Sequence[Dict[str, Any]],
    *,
    manifest: Optional[Dict[str, Any]] = None,
    replay_result: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a deterministic trace-driven sizing sweep."""
    options = options or {}
    if not records:
        raise ValueError("Sizing requires a generated trace")

    profile_id = str(options.get("profile") or "8b-h100-balanced")
    if profile_id not in PROFILES:
        raise ValueError(f"Unknown sizing profile: {profile_id}")
    base_profile = dict(PROFILES[profile_id])
    base_profile["jitter"] = dict(PROFILES[profile_id]["jitter"])

    if options.get("max_num_seqs") is not None:
        base_profile["max_num_seqs"] = _integer(options["max_num_seqs"], base_profile["max_num_seqs"], 1, 512)
    analytical_mode = str(options.get("analytical_mode") or "informational")
    analytical = build_analytical_envelope(
        records,
        profile_id=profile_id,
        max_num_seqs=int(base_profile["max_num_seqs"]),
        options=options,
    )
    base_profile = seed_latency_profile(
        base_profile,
        analytical,
        analytical_mode,
    )
    cache_gib = _number(options.get("cache_gib"), 40.0, 0.1)
    target_ttft = _number(options.get("target_ttft_p95_ms"), 1000.0, 1.0)
    max_replicas = _integer(options.get("max_replicas"), 16, 1, 128)
    topology = str(options.get("topology") or "aggregated")
    if topology not in ("aggregated", "disaggregated"):
        raise ValueError("topology must be aggregated or disaggregated")
    calibration = str(options.get("calibration") or "auto")
    if calibration not in ("auto", "profile", "replay"):
        raise ValueError("calibration must be auto, profile, or replay")
    if calibration == "replay" and not replay_result:
        raise ValueError("Replay calibration requires an attached AIPerf replay result")

    causal_hit_rate = _causal_hit_rate(records)
    cache = _cache_point(manifest, cache_gib, causal_hit_rate)
    profile, calibration_info = _calibrated_profile(
        base_profile,
        records,
        replay_result,
        calibration,
        float(cache["hit_rate"]),
    )

    sweep = [
        _simulate_candidate(
            records,
            replicas=replicas,
            profile=profile,
            hit_rate=float(cache["hit_rate"]),
            topology=topology,
            target_ttft_p95_ms=target_ttft,
        )
        for replicas in range(1, max_replicas + 1)
    ]
    recommendation = next((candidate for candidate in sweep if candidate.get("meets_slo")), sweep[-1])
    configuration_fits = bool((analytical.get("memory") or {}).get("fits"))
    met = bool(recommendation.get("meets_slo")) and configuration_fits

    timestamps = [int(row.get("timestamp") or 0) for row in records]
    span_ms = max(1, max(timestamps) - min(timestamps))
    total_input = sum(max(0, int(row.get("input_length") or 0)) for row in records)
    total_output = sum(max(0, int(row.get("output_length") or 0)) for row in records)
    result = {
        "artifact_type": "sizing_study",
        "schema_version": SCHEMA_VERSION,
        "method": {
            "id": "llmd_per_token_trace_sweep",
            "source": LLMD_SOURCE,
            "description": (
                "Discrete-event replica sweep using llm-d per-token prefill, decode, "
                "KV-transfer, and time-factor-under-load semantics."
            ),
        },
        "inputs": {
            "profile": profile_id,
            "topology": topology,
            "cache_gib_per_replica": round(cache_gib, 3),
            "target_ttft_p95_ms": round(target_ttft, 3),
            "max_replicas": max_replicas,
            "calibration": calibration,
            "analytical_mode": analytical_mode,
            "model_id": analytical["selection"]["model_id"],
            "hardware_id": analytical["selection"]["hardware_id"],
            "weight_precision": analytical["selection"]["weight_precision"],
            "kv_precision": analytical["selection"]["kv_precision"],
            "activation_precision": analytical["selection"]["activation_precision"],
            "tensor_parallel": analytical["selection"]["tensor_parallel"],
            "analytical_concurrency": analytical["selection"]["concurrency"],
            "memory_headroom_percent": analytical["selection"]["memory_headroom_percent"],
        },
        "workload": {
            "request_count": len(records),
            "span_ms": span_ms,
            "average_rps": round(len(records) / (span_ms / 1000.0), 6),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "input_tokens_p50": round(_percentile([int(row.get("input_length") or 0) for row in records], 0.5), 3),
            "input_tokens_p95": round(_percentile([int(row.get("input_length") or 0) for row in records], 0.95), 3),
            "output_tokens_p50": round(_percentile([int(row.get("output_length") or 0) for row in records], 0.5), 3),
            "causal_prefix_hit_rate": round(causal_hit_rate, 6),
        },
        "profile": profile,
        "calibration": calibration_info,
        "analytical": analytical,
        "cache": cache,
        "recommendation": {
            **{key: value for key, value in recommendation.items() if key != "request_samples"},
            "slo_met": met,
            "configuration_fits_hbm": configuration_fits,
            "status": (
                "sized"
                if met
                else "configuration_does_not_fit"
                if not configuration_fits
                else "capacity_limit_reached"
            ),
            "replica_gpu_count": int(profile["gpus_per_replica"]),
            "total_cache_gib": round(cache_gib * int(recommendation["replicas"]), 3),
            "narrative": (
                f"{recommendation['replicas']} replica(s) / {recommendation['gpus']} GPU(s) "
                f"meet the {target_ttft:g} ms p95 TTFT target."
                if met
                else (
                    f"The selected {analytical['hardware']['label']} x"
                    f"{analytical['selection']['tensor_parallel']} configuration does not "
                    "fit the trace-derived p95 memory envelope."
                )
                if not configuration_fits
                else (
                    f"No candidate through {max_replicas} replicas met the {target_ttft:g} ms "
                    "p95 TTFT target; extend the sweep or recalibrate the profile."
                )
            ),
        },
        "recommended_request_samples": recommendation.get("request_samples") or [],
        "sweep": [
            {key: value for key, value in candidate.items() if key != "request_samples"}
            for candidate in sweep
        ],
        "llmd_config": _llmd_config(profile, cache, topology),
        "aiperf_handoff": {
            "dataset": "trace.jsonl",
            "custom_dataset_type": "mooncake_trace",
            "server_url": "http://localhost:8000",
            "note": "Run this trace through AIPerf against llm-d-inference-sim and attach the replay result to calibrate the next study.",
        },
        "limitations": [
            "This is a sizing estimate, not a replacement for an AIPerf measurement on target hardware.",
            "The analytical roofline is a feasibility check and lower bound; the llm-d sweep models serving and queueing behavior.",
            "Tracerator models 512-token cache blocks; emitted llm-d config translates capacity to 128-token blocks.",
            "Replica routing is least-available-slot and does not yet model a production router's prefix-affinity policy.",
        ],
    }
    return result
