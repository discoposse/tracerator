"""Trace-aware memory and roofline estimates for LLM inference sizing.

The analytical envelope is intentionally separate from the serving simulation.
It explains physical feasibility and theoretical bottlenecks; llm-d and AIPerf
remain the authorities for queueing behavior and measured performance.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence


METHODOLOGY_SOURCE = "https://calc.inference.systems/#info"
BYTES_PER_GIB = 1024 ** 3


PRECISIONS: Dict[str, Dict[str, Any]] = {
    "bf16": {"id": "bf16", "label": "BF16 / FP16", "bytes": 2.0},
    "fp8": {"id": "fp8", "label": "FP8 / INT8", "bytes": 1.0},
    "int4": {"id": "int4", "label": "FP4 / INT4", "bytes": 0.5},
}


MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "llama-3.2-3b": {
        "id": "llama-3.2-3b",
        "label": "Llama 3.2 3B",
        "publisher": "Meta",
        "parameter_count": 3_210_000_000,
        "layers": 28,
        "hidden_size": 3072,
        "intermediate_size": 8192,
        "attention_heads": 24,
        "kv_heads": 8,
        "head_dim": 128,
        "source": "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct/blob/main/config.json",
    },
    "llama-3.1-8b": {
        "id": "llama-3.1-8b",
        "label": "Llama 3.1 8B",
        "publisher": "Meta",
        "parameter_count": 8_030_000_000,
        "layers": 32,
        "hidden_size": 4096,
        "intermediate_size": 14336,
        "attention_heads": 32,
        "kv_heads": 8,
        "head_dim": 128,
        "source": "https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct/blob/main/config.json",
    },
    "llama-3.1-70b": {
        "id": "llama-3.1-70b",
        "label": "Llama 3.1 70B",
        "publisher": "Meta",
        "parameter_count": 70_550_000_000,
        "layers": 80,
        "hidden_size": 8192,
        "intermediate_size": 28672,
        "attention_heads": 64,
        "kv_heads": 8,
        "head_dim": 128,
        "source": "https://huggingface.co/meta-llama/Llama-3.1-70B-Instruct/blob/main/config.json",
    },
}


HARDWARE_CATALOG: Dict[str, Dict[str, Any]] = {
    "l40s-48gb": {
        "id": "l40s-48gb",
        "label": "NVIDIA L40S 48GB",
        "memory_gb": 48.0,
        "memory_bandwidth_gbs": 864.0,
        "interconnect_gbs_per_direction": 32.0,
        "dense_tflops": {"bf16": 362.05, "fp8": 733.0, "int4": 733.0},
        "compute_efficiency": 0.55,
        "memory_efficiency": 0.80,
        "source": "https://www.nvidia.com/en-us/data-center/l40s/",
        "notes": "PCIe-only scale-up; planning efficiencies are conservative defaults, not vendor claims.",
    },
    "h100-sxm-80gb": {
        "id": "h100-sxm-80gb",
        "label": "NVIDIA H100 SXM 80GB",
        "memory_gb": 80.0,
        "memory_bandwidth_gbs": 3350.0,
        "interconnect_gbs_per_direction": 450.0,
        "dense_tflops": {"bf16": 989.0, "fp8": 1979.0, "int4": 1979.0},
        "compute_efficiency": 0.65,
        "memory_efficiency": 0.85,
        "source": "https://www.nvidia.com/en-us/data-center/h100/",
        "notes": "Dense Tensor Core throughput; NVLink bandwidth is converted to per-direction planning bandwidth.",
    },
    "h200-sxm-141gb": {
        "id": "h200-sxm-141gb",
        "label": "NVIDIA H200 SXM 141GB",
        "memory_gb": 141.0,
        "memory_bandwidth_gbs": 4800.0,
        "interconnect_gbs_per_direction": 450.0,
        "dense_tflops": {"bf16": 989.0, "fp8": 1979.0, "int4": 1979.0},
        "compute_efficiency": 0.65,
        "memory_efficiency": 0.85,
        "source": "https://www.nvidia.com/en-us/data-center/h200/",
        "notes": "Hopper compute envelope with H200 HBM3e capacity and bandwidth.",
    },
    "b200-sxm-180gb": {
        "id": "b200-sxm-180gb",
        "label": "NVIDIA B200 SXM 180GB",
        "memory_gb": 180.0,
        "memory_bandwidth_gbs": 8000.0,
        "interconnect_gbs_per_direction": 900.0,
        "dense_tflops": {"bf16": 2250.0, "fp8": 4500.0, "int4": 9000.0},
        "compute_efficiency": 0.60,
        "memory_efficiency": 0.85,
        "source": "https://www.nvidia.com/en-us/data-center/dgx-b200/",
        "notes": "Per-GPU values derived from the published eight-GPU DGX B200 dense system specifications.",
    },
}


PROFILE_DEFAULTS = {
    "small-l40s-edge": ("llama-3.2-3b", "l40s-48gb", 1),
    "8b-h100-balanced": ("llama-3.1-8b", "h100-sxm-80gb", 1),
    "70b-h100-tp8-throughput": ("llama-3.1-70b", "h100-sxm-80gb", 8),
}


def list_analytical_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return the user-facing analytical sizing catalog."""
    models = [
        {
            "id": item["id"],
            "label": item["label"],
            "publisher": item["publisher"],
            "parameter_count": item["parameter_count"],
            "source": item["source"],
        }
        for item in MODEL_CATALOG.values()
    ]
    hardware = [
        {
            "id": item["id"],
            "label": item["label"],
            "memory_gb": item["memory_gb"],
            "memory_bandwidth_gbs": item["memory_bandwidth_gbs"],
            "source": item["source"],
            "notes": item["notes"],
        }
        for item in HARDWARE_CATALOG.values()
    ]
    return {
        "models": models,
        "hardware": hardware,
        "precisions": [
            {"id": item["id"], "label": item["label"], "bytes": item["bytes"]}
            for item in PRECISIONS.values()
        ],
        "methodology_source": METHODOLOGY_SOURCE,
    }


def _number(value: Any, fallback: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    if not math.isfinite(parsed):
        parsed = fallback
    return max(minimum, parsed)


def _integer(value: Any, fallback: int, minimum: int = 1, maximum: int = 256) -> int:
    return max(minimum, min(maximum, int(_number(value, fallback, minimum))))


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


def _phase_time(
    *,
    flops: float,
    hbm_bytes: float,
    comms_bytes: float,
    hardware: Dict[str, Any],
    precision: str,
    tensor_parallel: int,
) -> Dict[str, Any]:
    tflops = float(hardware["dense_tflops"][precision])
    compute_s = flops / (
        tensor_parallel * tflops * float(hardware["compute_efficiency"]) * 1e12
    )
    memory_s = hbm_bytes / (
        float(hardware["memory_bandwidth_gbs"])
        * float(hardware["memory_efficiency"])
        * 1e9
    )
    comms_s = 0.0
    if tensor_parallel > 1:
        comms_s = comms_bytes / (
            float(hardware["interconnect_gbs_per_direction"]) * 1e9
        )
    components = {"compute": compute_s, "memory": memory_s, "comms": comms_s}
    regime = max(components, key=components.get)
    return {
        "time_ms": round(components[regime] * 1000.0, 6),
        "regime": regime,
        "components_ms": {
            key: round(value * 1000.0, 6) for key, value in components.items()
        },
        "arithmetic_intensity_flops_per_byte": round(
            flops / max(1.0, hbm_bytes), 6
        ),
    }


def build_analytical_envelope(
    records: Sequence[Dict[str, Any]],
    *,
    profile_id: str,
    max_num_seqs: int,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a memory, roofline, and disaggregation envelope from trace tails."""
    default_model, default_hardware, default_tp = PROFILE_DEFAULTS[profile_id]
    model_id = str(options.get("model_id") or default_model)
    hardware_id = str(options.get("hardware_id") or default_hardware)
    if model_id not in MODEL_CATALOG:
        raise ValueError(f"Unknown analytical model: {model_id}")
    if hardware_id not in HARDWARE_CATALOG:
        raise ValueError(f"Unknown analytical hardware: {hardware_id}")

    weight_precision = str(options.get("weight_precision") or "bf16")
    kv_precision = str(options.get("kv_precision") or "bf16")
    activation_precision = str(options.get("activation_precision") or "bf16")
    for precision in (weight_precision, kv_precision, activation_precision):
        if precision not in PRECISIONS:
            raise ValueError(f"Unknown analytical precision: {precision}")

    tensor_parallel = _integer(
        options.get("tensor_parallel"), default_tp, 1, 72
    )
    concurrency = _integer(
        options.get("analytical_concurrency"), max_num_seqs, 1, 4096
    )
    headroom_percent = _number(
        options.get("memory_headroom_percent"), 10.0, 0.0
    )
    headroom_percent = min(50.0, headroom_percent)

    model = MODEL_CATALOG[model_id]
    hardware = HARDWARE_CATALOG[hardware_id]
    input_lengths = [max(0, int(row.get("input_length") or 0)) for row in records]
    output_lengths = [max(1, int(row.get("output_length") or 1)) for row in records]
    prompt_p50 = max(1, int(round(_percentile(input_lengths, 0.50))))
    prompt_p95 = max(1, int(round(_percentile(input_lengths, 0.95))))
    output_p50 = max(1, int(round(_percentile(output_lengths, 0.50))))
    output_p95 = max(1, int(round(_percentile(output_lengths, 0.95))))
    context_tokens = prompt_p95 + output_p95

    weight_bytes = float(model["parameter_count"]) * PRECISIONS[weight_precision]["bytes"]
    weight_bytes_per_gpu = weight_bytes / tensor_parallel
    kv_shards = max(1, min(tensor_parallel, int(model["kv_heads"])))
    kv_bytes_per_token = (
        2
        * int(model["layers"])
        * int(model["kv_heads"])
        * int(model["head_dim"])
        * PRECISIONS[kv_precision]["bytes"]
    )
    kv_bytes_per_request_per_gpu = kv_bytes_per_token * context_tokens / kv_shards
    kv_bytes_per_gpu = kv_bytes_per_request_per_gpu * concurrency
    activation_bytes_per_request_per_gpu = (
        prompt_p95
        * (int(model["hidden_size"]) + int(model["intermediate_size"]))
        * PRECISIONS[activation_precision]["bytes"]
        * 2
        / tensor_parallel
    )
    activation_bytes_per_gpu = activation_bytes_per_request_per_gpu * concurrency
    required_bytes_per_gpu = (
        weight_bytes_per_gpu + kv_bytes_per_gpu + activation_bytes_per_gpu
    )
    physical_bytes = float(hardware["memory_gb"]) * 1e9
    usable_bytes = physical_bytes * (1.0 - headroom_percent / 100.0)
    per_sequence_bytes = (
        kv_bytes_per_request_per_gpu + activation_bytes_per_request_per_gpu
    )
    max_sequences = max(
        0,
        int(
            math.floor(
                max(0.0, usable_bytes - weight_bytes_per_gpu)
                / max(1.0, per_sequence_bytes)
            )
        ),
    )

    attention_width = (
        int(model["attention_heads"]) * int(model["head_dim"])
    )
    prefill_flops = (
        2 * float(model["parameter_count"]) * prompt_p95
        + 2
        * prompt_p95
        * (int(model["layers"]) * prompt_p95)
        * attention_width
    )
    decode_context = prompt_p95 + output_p50 / 2.0
    decode_flops = (
        2 * float(model["parameter_count"])
        + 2 * int(model["layers"]) * decode_context * attention_width
    ) * concurrency
    activation_element_bytes = PRECISIONS[activation_precision]["bytes"]
    prefill_comms = (
        4
        * int(model["layers"])
        * ((tensor_parallel - 1) / tensor_parallel)
        * prompt_p95
        * int(model["hidden_size"])
        * activation_element_bytes
    )
    decode_comms = (
        4
        * int(model["layers"])
        * ((tensor_parallel - 1) / tensor_parallel)
        * concurrency
        * int(model["hidden_size"])
        * activation_element_bytes
    )
    prefill = _phase_time(
        flops=prefill_flops,
        hbm_bytes=weight_bytes_per_gpu + activation_bytes_per_request_per_gpu,
        comms_bytes=prefill_comms,
        hardware=hardware,
        precision=activation_precision,
        tensor_parallel=tensor_parallel,
    )
    decode = _phase_time(
        flops=decode_flops,
        hbm_bytes=weight_bytes_per_gpu + kv_bytes_per_gpu,
        comms_bytes=decode_comms,
        hardware=hardware,
        precision=activation_precision,
        tensor_parallel=tensor_parallel,
    )
    aggregate_output_tokens_s = (
        concurrency / max(1e-9, decode["time_ms"] / 1000.0)
    )
    pd_ratio = (
        concurrency
        * (prefill["time_ms"] / 1000.0)
        / max(1e-9, output_p50 * (decode["time_ms"] / 1000.0))
    )
    fits = required_bytes_per_gpu <= usable_bytes

    return {
        "method": {
            "id": "trace_p95_roofline_v1",
            "source": METHODOLOGY_SOURCE,
            "description": (
                "Independent memory and max(compute, HBM, interconnect) roofline "
                "estimate using the trace's p95 prompt and output lengths."
            ),
            "role": "lower_bound_and_feasibility_check",
        },
        "selection": {
            "model_id": model_id,
            "hardware_id": hardware_id,
            "weight_precision": weight_precision,
            "kv_precision": kv_precision,
            "activation_precision": activation_precision,
            "tensor_parallel": tensor_parallel,
            "concurrency": concurrency,
            "memory_headroom_percent": round(headroom_percent, 3),
        },
        "model": dict(model),
        "hardware": dict(hardware),
        "workload_point": {
            "input_tokens_p50": prompt_p50,
            "input_tokens_p95": prompt_p95,
            "output_tokens_p50": output_p50,
            "output_tokens_p95": output_p95,
            "context_tokens_p95": context_tokens,
            "concurrency": concurrency,
        },
        "memory": {
            "fits": fits,
            "physical_gb_per_gpu": round(physical_bytes / 1e9, 6),
            "usable_gib_per_gpu": round(usable_bytes / BYTES_PER_GIB, 6),
            "required_gib_per_gpu": round(required_bytes_per_gpu / BYTES_PER_GIB, 6),
            "weights_gib_per_gpu": round(weight_bytes_per_gpu / BYTES_PER_GIB, 6),
            "kv_gib_per_gpu": round(kv_bytes_per_gpu / BYTES_PER_GIB, 6),
            "activations_gib_per_gpu": round(activation_bytes_per_gpu / BYTES_PER_GIB, 6),
            "headroom_gib_per_gpu": round(
                (usable_bytes - required_bytes_per_gpu) / BYTES_PER_GIB, 6
            ),
            "kv_kib_per_token": round(kv_bytes_per_token / 1024.0, 6),
            "max_sequences_at_p95_context": max_sequences,
        },
        "roofline": {
            "prefill": prefill,
            "decode": decode,
            "ttft_floor_ms": prefill["time_ms"],
            "tpot_floor_ms": decode["time_ms"],
            "aggregate_output_tokens_s": round(aggregate_output_tokens_s, 6),
        },
        "disaggregation": {
            "prefill_nodes_per_decode_node": round(pd_ratio, 6),
            "phase_asymmetry": prefill["regime"] != decode["regime"],
            "candidate": prefill["regime"] != decode["regime"] or pd_ratio > 1.5 or pd_ratio < 0.67,
            "narrative": (
                f"Prefill is {prefill['regime']}-bound and decode is "
                f"{decode['regime']}-bound; the analytical P:D ratio is "
                f"{pd_ratio:.2f}:1."
            ),
        },
        "confidence": {
            "level": "analytical",
            "compute_efficiency": hardware["compute_efficiency"],
            "memory_efficiency": hardware["memory_efficiency"],
            "limitations": [
                "Roofline times are lower bounds and do not include scheduler, kernel-launch, or queueing overhead.",
                "Activation memory is a coarse FlashAttention-style planning estimate.",
                "Model and hardware catalog entries are versioned assumptions with explicit source links.",
            ],
        },
    }


def seed_latency_profile(
    profile: Dict[str, Any],
    envelope: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """Optionally use the roofline result as a latency baseline or guardrail."""
    if mode not in ("informational", "guardrail", "analytical"):
        raise ValueError(
            "analytical_mode must be informational, guardrail, or analytical"
        )
    resolved = dict(profile)
    resolved["jitter"] = dict(profile.get("jitter") or {})
    if mode == "informational":
        return resolved

    workload = envelope["workload_point"]
    roofline = envelope["roofline"]
    prefill_per_token = float(roofline["ttft_floor_ms"]) / max(
        1, int(workload["input_tokens_p95"])
    )
    tpot = float(roofline["tpot_floor_ms"])
    if mode == "guardrail":
        resolved["prefill_time_per_token_ms"] = max(
            float(resolved["prefill_time_per_token_ms"]), prefill_per_token
        )
        resolved["inter_token_latency_ms"] = max(
            float(resolved["inter_token_latency_ms"]), tpot
        )
    else:
        resolved["prefill_overhead_ms"] = 0.0
        resolved["prefill_time_per_token_ms"] = max(0.001, prefill_per_token)
        resolved["inter_token_latency_ms"] = max(0.001, tpot)
    resolved["gpus_per_replica"] = int(
        envelope["selection"]["tensor_parallel"]
    )
    return resolved
