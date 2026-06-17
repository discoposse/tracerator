#!/usr/bin/env python3
"""Model-aware KV cache sizing and trace-level cache policy simulation."""

from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
import heapq
import math
from typing import Any, Deque, Dict, Iterable, List, Sequence

BYTES_PER_GIB = 1024 ** 3
DEFAULT_BLOCK_SIZE = 512
DEFAULT_CAPACITY_GIB = [1, 2, 4, 8, 16, 32, 64, 128, 256]
POLICIES = ("fifo", "lru", "optimal")

PRECISIONS = {
    "bf16_fp16": {"label": "BF16 / FP16", "bytes_per_element": 2.0},
    "fp8_int8": {"label": "FP8 / INT8", "bytes_per_element": 1.0},
    "fp4_int4": {"label": "FP4 / INT4", "bytes_per_element": 0.5},
}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    family: str
    formula: str
    fields: Dict[str, Any]
    source: str


MODEL_CATALOG: Dict[str, ModelSpec] = {
    "kimi-k2.5": ModelSpec(
        id="kimi-k2.5",
        label="Kimi K2.5",
        family="Kimi",
        formula="mla",
        fields={"num_hidden_layers": 61, "kv_lora_rank": 512, "qk_rope_head_dim": 64},
        source="KVCache.ai model catalog, MLA latent KV fields",
    ),
    "deepseek-v3": ModelSpec(
        id="deepseek-v3",
        label="DeepSeek V3 / R1",
        family="DeepSeek",
        formula="mla",
        fields={
            "num_hidden_layers": 61,
            "kv_lora_rank": 512,
            "qk_rope_head_dim": 64,
            "num_nextn_predict_layers": 1,
        },
        source="KVCache.ai model catalog, MLA latent KV fields",
    ),
    "deepseek-v3.2": ModelSpec(
        id="deepseek-v3.2",
        label="DeepSeek V3.2",
        family="DeepSeek",
        formula="dsa_mla",
        fields={
            "num_hidden_layers": 61,
            "kv_lora_rank": 512,
            "qk_rope_head_dim": 64,
            "index_head_dim": 128,
            "indexer_full_layers": 61,
            "num_nextn_predict_layers": 1,
            "draft_indexer_layers": 1,
        },
        source="KVCache.ai model catalog, DSA/MLA with indexer fields",
    ),
    "qwen3-32b-gqa": ModelSpec(
        id="qwen3-32b-gqa",
        label="Qwen-style 32B GQA",
        family="Qwen",
        formula="standard_gqa",
        fields={"num_hidden_layers": 64, "num_key_value_heads": 8, "head_dim": 128},
        source="Generic production GQA profile for capacity what-if analysis",
    ),
    "llama3.1-70b-gqa": ModelSpec(
        id="llama3.1-70b-gqa",
        label="Llama 3.1 70B GQA",
        family="Llama",
        formula="standard_gqa",
        fields={"num_hidden_layers": 80, "num_key_value_heads": 8, "head_dim": 128},
        source="Generic Llama-family GQA capacity profile",
    ),
}


def model_options() -> List[Dict[str, str]]:
    return [
        {
            "id": model.id,
            "label": model.label,
            "family": model.family,
            "formula": model.formula,
            "source": model.source,
        }
        for model in MODEL_CATALOG.values()
    ]


def precision_options() -> List[Dict[str, Any]]:
    return [
        {"id": precision_id, **profile}
        for precision_id, profile in PRECISIONS.items()
    ]


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = fallback
    return max(1, parsed)


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return parsed if parsed > 0 else fallback


def _nonnegative_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return parsed if parsed >= 0 else fallback


def _bool(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes", "on"}


def _precision_bytes(precision_id: str | None, fallback: str = "bf16_fp16") -> float:
    profile = PRECISIONS.get(precision_id or "", PRECISIONS[fallback])
    return float(profile["bytes_per_element"])


def _draft_layers(model: ModelSpec, include_draft_kv: bool) -> int:
    if not include_draft_kv:
        return 0
    return _positive_int(model.fields.get("num_nextn_predict_layers", 0), 0) if model.fields.get("num_nextn_predict_layers") else 0


def cache_bytes_per_token(
    model_id: str,
    precision: str = "bf16_fp16",
    indexer_precision: str | None = None,
    include_draft_kv: bool = False,
) -> Dict[str, Any]:
    model = MODEL_CATALOG.get(model_id) or MODEL_CATALOG["kimi-k2.5"]
    kv_bytes = _precision_bytes(precision)
    indexer_bytes = _precision_bytes(indexer_precision or precision, precision)
    fields = model.fields
    draft_layers = _draft_layers(model, include_draft_kv)
    layers = _positive_int(fields.get("num_hidden_layers"), 1) + draft_layers

    if model.formula == "standard_gqa":
        elements = (
            layers
            * 2
            * _positive_int(fields.get("num_key_value_heads"), 1)
            * _positive_int(fields.get("head_dim"), 1)
        )
        kv_payload = elements * kv_bytes
        indexer_payload = 0.0
    elif model.formula == "mla":
        elements = layers * (
            _positive_int(fields.get("kv_lora_rank"), 1)
            + _positive_int(fields.get("qk_rope_head_dim"), 1)
        )
        kv_payload = elements * kv_bytes
        indexer_payload = 0.0
    elif model.formula == "dsa_mla":
        kv_elements = layers * (
            _positive_int(fields.get("kv_lora_rank"), 1)
            + _positive_int(fields.get("qk_rope_head_dim"), 1)
        )
        main_indexer_layers = _positive_int(fields.get("indexer_full_layers"), layers)
        draft_indexer_layers = (
            _positive_int(fields.get("draft_indexer_layers"), 0)
            if include_draft_kv and fields.get("draft_indexer_layers")
            else 0
        )
        indexer_elements = (
            (main_indexer_layers + draft_indexer_layers)
            * _positive_int(fields.get("index_head_dim"), 1)
        )
        kv_payload = kv_elements * kv_bytes
        indexer_payload = indexer_elements * indexer_bytes
        elements = kv_elements + indexer_elements
    else:
        raise ValueError(f"Unsupported KV cache formula: {model.formula}")

    return {
        "model_id": model.id,
        "model_label": model.label,
        "formula": model.formula,
        "precision": precision,
        "precision_bytes": kv_bytes,
        "indexer_precision": indexer_precision or precision,
        "indexer_precision_bytes": indexer_bytes,
        "include_draft_kv": include_draft_kv,
        "elements_per_token": elements,
        "kv_bytes_per_token": kv_payload,
        "indexer_bytes_per_token": indexer_payload,
        "bytes_per_token": kv_payload + indexer_payload,
        "source": model.source,
    }


def estimate_cache_size(
    model_id: str,
    tokens: int,
    sequences: int,
    precision: str = "bf16_fp16",
    indexer_precision: str | None = None,
    include_draft_kv: bool = False,
    tensor_parallel: int = 1,
) -> Dict[str, Any]:
    token_profile = cache_bytes_per_token(
        model_id,
        precision=precision,
        indexer_precision=indexer_precision,
        include_draft_kv=include_draft_kv,
    )
    safe_tokens = _positive_int(tokens, DEFAULT_BLOCK_SIZE)
    safe_sequences = _positive_int(sequences, 1)
    safe_tp = _positive_int(tensor_parallel, 1)
    total_bytes = token_profile["bytes_per_token"] * safe_tokens * safe_sequences
    return {
        **token_profile,
        "tokens": safe_tokens,
        "sequences": safe_sequences,
        "tensor_parallel": safe_tp,
        "total_bytes": total_bytes,
        "total_gib": total_bytes / BYTES_PER_GIB,
        "per_device_gib": total_bytes / safe_tp / BYTES_PER_GIB,
        "bytes_per_block": token_profile["bytes_per_token"] * DEFAULT_BLOCK_SIZE,
    }


def _block_tokens(input_length: int, block_size: int, index: int, count: int) -> int:
    if count <= 0:
        return 0
    remaining = max(1, int(input_length)) - index * block_size
    if remaining <= 0:
        return 1
    return max(1, min(block_size, remaining))


def _events(reqs: Sequence[Dict[str, Any]], block_size: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for request_index, request in enumerate(reqs):
        ids = request.get("hash_ids") or []
        input_length = _positive_int(request.get("input_length"), 1)
        count = len(ids)
        for block_index, block_id in enumerate(ids):
            events.append(
                {
                    "id": int(block_id),
                    "tokens": _block_tokens(input_length, block_size, block_index, count),
                    "request_index": request_index,
                }
            )
    return events


def _finish(policy: str, cache_blocks: int, hit_tokens: int, measured_tokens: int, warmup_requests: int) -> Dict[str, Any]:
    hit_rate = hit_tokens / measured_tokens if measured_tokens else 0.0
    miss_fraction = max(1.0 - hit_rate, 0.001)
    return {
        "policy": policy,
        "cache_blocks": cache_blocks,
        "warmup_requests": warmup_requests,
        "hit_tokens": hit_tokens,
        "measured_tokens": measured_tokens,
        "hit_rate": round(hit_rate, 6),
        "ideal_prefill_speedup": round(min(1000.0, 1.0 / miss_fraction), 3),
    }


def _simulate_fifo_lru(events: List[Dict[str, Any]], cache_blocks: int, warmup_requests: int, policy: str) -> Dict[str, Any]:
    hit_tokens = 0
    measured_tokens = 0
    if policy == "lru":
        cache: OrderedDict[int, None] = OrderedDict()
    else:
        cache_set = set()
        queue: Deque[int] = deque()

    for event in events:
        block_id = event["id"]
        measured = event["request_index"] >= warmup_requests
        if measured:
            measured_tokens += event["tokens"]

        if policy == "lru":
            hit = block_id in cache
            if hit:
                cache.move_to_end(block_id)
            elif cache_blocks > 0:
                while len(cache) >= cache_blocks:
                    cache.popitem(last=False)
                cache[block_id] = None
        else:
            hit = block_id in cache_set
            if not hit and cache_blocks > 0:
                while len(cache_set) >= cache_blocks:
                    victim = queue.popleft()
                    cache_set.discard(victim)
                cache_set.add(block_id)
                queue.append(block_id)

        if measured and hit:
            hit_tokens += event["tokens"]

    return _finish(policy, cache_blocks, hit_tokens, measured_tokens, warmup_requests)


def _simulate_optimal(events: List[Dict[str, Any]], cache_blocks: int, warmup_requests: int) -> Dict[str, Any]:
    future_positions: Dict[int, Deque[int]] = defaultdict(deque)
    for index, event in enumerate(events):
        future_positions[event["id"]].append(index)

    cache = set()
    heap: List[tuple[float, int]] = []
    hit_tokens = 0
    measured_tokens = 0
    infinity = math.inf

    def next_use(block_id: int) -> float:
        positions = future_positions.get(block_id)
        return float(positions[0]) if positions else infinity

    def push_cached(block_id: int) -> None:
        heapq.heappush(heap, (-next_use(block_id), block_id))

    def pop_farthest_cached() -> int | None:
        while heap:
            neg_next, block_id = heapq.heappop(heap)
            if block_id in cache and neg_next == -next_use(block_id):
                return block_id
        return None

    for index, event in enumerate(events):
        block_id = event["id"]
        positions = future_positions[block_id]
        if positions and positions[0] == index:
            positions.popleft()

        measured = event["request_index"] >= warmup_requests
        if measured:
            measured_tokens += event["tokens"]

        hit = block_id in cache
        if not hit and cache_blocks > 0:
            if len(cache) < cache_blocks:
                cache.add(block_id)
                push_cached(block_id)
            else:
                candidate_next = next_use(block_id)
                victim = pop_farthest_cached()
                victim_next = next_use(victim) if victim is not None else -1
                if victim is not None and candidate_next < victim_next:
                    cache.remove(victim)
                    cache.add(block_id)
                    push_cached(block_id)
                elif victim is not None:
                    push_cached(victim)
        elif hit:
            push_cached(block_id)

        if measured and hit:
            hit_tokens += event["tokens"]

    return _finish("optimal", cache_blocks, hit_tokens, measured_tokens, warmup_requests)


def _simulate_ceiling(events: List[Dict[str, Any]], warmup_requests: int) -> float:
    seen = set()
    hit_tokens = 0
    measured_tokens = 0
    for event in events:
        measured = event["request_index"] >= warmup_requests
        if measured:
            measured_tokens += event["tokens"]
            if event["id"] in seen:
                hit_tokens += event["tokens"]
        seen.add(event["id"])
    return hit_tokens / measured_tokens if measured_tokens else 0.0


def parse_capacity_gib_values(value: Any) -> List[float]:
    if value is None or value == "":
        return list(DEFAULT_CAPACITY_GIB)
    if isinstance(value, (list, tuple)):
        raw_values: Iterable[Any] = value
    else:
        raw_values = str(value).replace(";", ",").split(",")
    parsed = []
    for item in raw_values:
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            parsed.append(numeric)
    return parsed or list(DEFAULT_CAPACITY_GIB)


def plan_kv_cache(
    reqs: Sequence[Dict[str, Any]],
    model_id: str = "kimi-k2.5",
    precision: str = "bf16_fp16",
    indexer_precision: str | None = None,
    include_draft_kv: bool = False,
    tensor_parallel: int = 1,
    block_size: int = DEFAULT_BLOCK_SIZE,
    capacity_gib_values: Sequence[float] | None = None,
    warmup_fraction: float = 0.5,
    policies: Sequence[str] = POLICIES,
) -> Dict[str, Any]:
    safe_block_size = _positive_int(block_size, DEFAULT_BLOCK_SIZE)
    safe_warmup = min(0.95, max(0.0, _nonnegative_float(warmup_fraction, 0.5)))
    safe_policies = [policy for policy in policies if policy in POLICIES] or ["lru"]
    events = _events(reqs, safe_block_size)
    warmup_requests = min(len(reqs), max(0, int(len(reqs) * safe_warmup)))
    token_profile = cache_bytes_per_token(
        model_id,
        precision=precision,
        indexer_precision=indexer_precision,
        include_draft_kv=include_draft_kv,
    )
    bytes_per_block = token_profile["bytes_per_token"] * safe_block_size
    unique_blocks = len({event["id"] for event in events})
    total_input_tokens = sum(event["tokens"] for event in events)
    measured_tokens = sum(event["tokens"] for event in events if event["request_index"] >= warmup_requests)
    peak_sequence_tokens = max((int(r.get("input_length", 0)) for r in reqs), default=0)
    peak_sequences = max(1, max((sum(1 for r in reqs if r.get("timestamp") == ts) for ts in {r.get("timestamp") for r in reqs}), default=1))
    peak_estimate = estimate_cache_size(
        model_id,
        tokens=max(1, peak_sequence_tokens),
        sequences=peak_sequences,
        precision=precision,
        indexer_precision=indexer_precision,
        include_draft_kv=include_draft_kv,
        tensor_parallel=tensor_parallel,
    )

    points = []
    for gib in capacity_gib_values or DEFAULT_CAPACITY_GIB:
        cache_blocks = int((float(gib) * BYTES_PER_GIB) // bytes_per_block) if bytes_per_block > 0 else 0
        results = {}
        for policy in safe_policies:
            if policy == "optimal":
                results[policy] = _simulate_optimal(events, cache_blocks, warmup_requests)
            else:
                results[policy] = _simulate_fifo_lru(events, cache_blocks, warmup_requests, policy)
        points.append({"gib": float(gib), "cache_blocks": cache_blocks, "results": results})

    return {
        "model": {
            "id": token_profile["model_id"],
            "label": token_profile["model_label"],
            "formula": token_profile["formula"],
            "source": token_profile["source"],
        },
        "settings": {
            "precision": precision,
            "precision_label": PRECISIONS.get(precision, PRECISIONS["bf16_fp16"])["label"],
            "indexer_precision": indexer_precision or precision,
            "include_draft_kv": _bool(include_draft_kv),
            "tensor_parallel": _positive_int(tensor_parallel, 1),
            "block_size": safe_block_size,
            "warmup_fraction": safe_warmup,
            "policies": safe_policies,
        },
        "bytes_per_token": round(token_profile["bytes_per_token"], 3),
        "bytes_per_block": round(bytes_per_block, 3),
        "unique_blocks": unique_blocks,
        "working_set_gib": round(unique_blocks * bytes_per_block / BYTES_PER_GIB, 6),
        "total_input_tokens": total_input_tokens,
        "measured_tokens": measured_tokens,
        "warmup_requests": warmup_requests,
        "reuse_ceiling": round(_simulate_ceiling(events, warmup_requests), 6),
        "peak_concurrency_estimate": {
            "max_input_tokens": peak_sequence_tokens,
            "sequences": peak_sequences,
            "total_gib": round(peak_estimate["total_gib"], 6),
            "per_device_gib": round(peak_estimate["per_device_gib"], 6),
        },
        "points": points,
    }
