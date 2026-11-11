#!/usr/bin/env python3
"""Whole-trace profiles for Tracerator cards and trace pages.

Sessions follow the prefix-chain rule: the parent of a request is the latest
earlier request whose full block-hash list is a prefix of this request, with
at least two shared blocks so a one-block system prompt does not chain the
trace. The first child continues the parent's session. Later siblings start
a branch session.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

BLOCK_SIZE = 512
MIN_SHARED_BLOCKS = 2

INPUT_BINS = [
    ("1K", 1, 1024),
    ("2K", 1025, 2048),
    ("4K", 2049, 4096),
    ("8K", 4097, 8192),
    ("16K", 8193, 16384),
    ("32K", 16385, 32768),
    ("64K", 32769, 65536),
    ("128K", 65537, 131072),
    (">128K", 131073, 10**12),
]

GAP_BINS = [
    ("1s", 0.0, 1.0),
    ("10s", 1.0, 10.0),
    ("1m", 10.0, 60.0),
    ("5m", 60.0, 300.0),
    ("15m", 300.0, 900.0),
    (">15m", 900.0, 10**12),
]

# Public config sizes. Whole-trace KV is unique 512-token blocks times this rate.
KV_MODELS = [
    {
        "id": "qwen3-32b",
        "name": "Qwen3-32B",
        "layout": "GQA, 8 KV heads, 64 layers",
        "bytes_per_token": 256 * 1024,
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek-V3",
        "layout": "MLA, 576 values per layer, 61 layers",
        "bytes_per_token": 69 * 1024,
    },
    {
        "id": "nemotron-3-super",
        "name": "Nemotron 3 Super 120B-A12B",
        "layout": "8 attention layers, plus Mamba state per sequence",
        "bytes_per_token": 8 * 1024,
    },
]


class _Node:
    __slots__ = ("children", "latest_end")

    def __init__(self) -> None:
        self.children: Dict[Any, _Node] = {}
        self.latest_end: Optional[int] = None


def _number(value: Any, fallback: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _hashes(record: Dict[str, Any]) -> List[Any]:
    raw = record.get("hash_ids")
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if isinstance(item, bool):
            cleaned.append(int(item))
        elif isinstance(item, int):
            cleaned.append(item)
        else:
            try:
                cleaned.append(int(item))
            except (TypeError, ValueError):
                cleaned.append(str(item))
    return cleaned


def percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def assign_parents(
    records: Sequence[Dict[str, Any]],
    min_shared_blocks: int = MIN_SHARED_BLOCKS,
) -> tuple:
    """Latest earlier request with the longest shared hash prefix.

    A one-block system prompt does not count. The match does not require the
    earlier request's whole hash list to be a prefix, because a later turn can
    diverge after the shared blocks. This is the chain that keeps the published
    Mooncake session counts.
    """
    root = _Node()
    parents: List[Optional[int]] = []
    shared: List[int] = []
    for index, record in enumerate(records):
        hashes = _hashes(record)
        node = root
        best: Optional[int] = None
        best_depth = 0
        for depth, block in enumerate(hashes, start=1):
            child = node.children.get(block)
            if child is None:
                child = _Node()
                node.children[block] = child
            node = child
            if depth >= min_shared_blocks and node.latest_end is not None and depth >= best_depth:
                best = node.latest_end
                best_depth = depth
        parents.append(best)
        shared.append(best_depth if best is not None else 0)
        node = root
        for block in hashes:
            child = node.children.get(block)
            if child is None:
                child = _Node()
                node.children[block] = child
            node = child
            node.latest_end = index
    return parents, shared


def build_session_indexes(parents: Sequence[Optional[int]]) -> Dict[str, Any]:
    continued_parents = set()
    sessions: List[List[int]] = []
    session_of: List[Optional[int]] = [None] * len(parents)
    continued = 0
    branches = 0
    for index, parent in enumerate(parents):
        if parent is None:
            sessions.append([index])
            session_of[index] = len(sessions) - 1
        elif parent not in continued_parents:
            continued_parents.add(parent)
            session_id = session_of[parent]
            if session_id is None:
                sessions.append([index])
                session_of[index] = len(sessions) - 1
            else:
                sessions[session_id].append(index)
                session_of[index] = session_id
                continued += 1
        else:
            branches += 1
            sessions.append([index])
            session_of[index] = len(sessions) - 1
    return {
        "sessions": sessions,
        "session_of": session_of,
        "continued": continued,
        "branches": branches,
        "with_parent": continued + branches,
    }


def sessions_to_jsonl(
    records: Sequence[Dict[str, Any]],
    parents: Sequence[Optional[int]],
    shared: Sequence[int],
    sessions: Sequence[Sequence[int]],
) -> List[Dict[str, Any]]:
    payload = []
    for session_id, indexes in enumerate(sessions):
        steps = []
        for position, index in enumerate(indexes):
            record = records[index]
            input_length = max(1, int(_number(record.get("input_length"), 1)))
            output_length = max(1, int(_number(record.get("output_length"), 1)))
            parent = parents[index]
            if position == 0 or parent is None:
                new_tokens = input_length
                gap = 0.0
                kind = "start"
            else:
                shared_blocks = shared[index]
                new_tokens = max(0, input_length - shared_blocks * BLOCK_SIZE)
                gap = max(0.0, (int(_number(record.get("timestamp"))) - int(_number(records[parent].get("timestamp")))) / 1000)
                kind = "branch" if position == 0 else "continue"
            steps.append({
                "input": input_length,
                "new": new_tokens,
                "output": output_length,
                "gap": round(gap, 3),
                "kind": kind,
            })
        payload.append({"id": f"s{session_id}", "steps": steps})
    return payload


def _histogram(values: Iterable[int], bins: Sequence[tuple]) -> List[Dict[str, Any]]:
    counts = Counter()
    total = 0
    for value in values:
        total += 1
        for name, low, high in bins:
            if low <= value <= high:
                counts[name] += 1
                break
    safe_total = max(1, total)
    return [
        {"label": name, "count": counts[name], "share": round(counts[name] / safe_total, 6)}
        for name, _low, _high in bins
    ]


def _gap_histogram(gaps: Sequence[float]) -> List[Dict[str, Any]]:
    counts = Counter()
    for gap in gaps:
        for name, low, high in GAP_BINS:
            if low < gap <= high or (name == GAP_BINS[0][0] and gap <= high):
                counts[name] += 1
                break
    total = max(1, len(gaps))
    return [
        {"label": name, "count": counts[name], "share": round(counts[name] / total, 6)}
        for name, _low, _high in GAP_BINS
    ]


def _turn_histogram(sessions: Sequence[Sequence[int]]) -> List[Dict[str, Any]]:
    labels = ["1", "2", "3", "4", "5", "6", "7", "8+"]
    counts = Counter()
    for session in sessions:
        length = len(session)
        label = "8+" if length >= 8 else str(length)
        counts[label] += 1
    total = max(1, len(sessions))
    return [
        {"label": label, "count": counts[label], "share": round(counts[label] / total, 6)}
        for label in labels
    ]


def format_bytes(num_bytes: float) -> str:
    if num_bytes >= 0.1 * 1024 ** 4:
        return f"{num_bytes / 1024 ** 4:.1f} TiB"
    if num_bytes >= 0.01 * 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GiB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.1f} MiB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KiB"
    return f"{num_bytes:.0f} B"


def format_kib(num_bytes: int) -> str:
    kib = num_bytes / 1024
    if abs(kib - round(kib)) < 0.05:
        return f"{kib:.0f} KiB"
    return f"{kib:.1f} KiB"


def compact_tokens(value: float) -> str:
    number = int(round(value))
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1000:
        scaled = number / 1000
        if scaled >= 10:
            return f"{scaled:.1f}K"
        return f"{scaled:.1f}K"
    return f"{number:,}"


def _duration_phrase(seconds: float) -> str:
    if seconds >= 90:
        minutes = seconds / 60
        rounded = int(round(minutes))
        unit = "minute" if rounded == 1 else "minutes"
        return f"{rounded} {unit}"
    if seconds >= 1.5:
        rounded = int(round(seconds))
        unit = "second" if rounded == 1 else "seconds"
        return f"{rounded} {unit}"
    return "under a second"


def _requests_per_minute(timestamps: Sequence[int]) -> List[int]:
    if not timestamps:
        return []
    start = min(timestamps)
    end = max(timestamps)
    buckets = max(1, int(math.floor((end - start) / 60000)) + 1)
    counts = [0] * buckets
    for timestamp in timestamps:
        index = min(buckets - 1, max(0, int((timestamp - start) // 60000)))
        counts[index] += 1
    return counts


def _peak_per_second(timestamps: Sequence[int]) -> int:
    if not timestamps:
        return 0
    return max(Counter(int(timestamp // 1000) for timestamp in timestamps).values())


def kv_table(unique_blocks: int, median_input: float) -> List[Dict[str, Any]]:
    tokens = unique_blocks * BLOCK_SIZE
    rows = []
    for model in KV_MODELS:
        per_token = model["bytes_per_token"]
        rows.append({
            "id": model["id"],
            "name": model["name"],
            "layout": model["layout"],
            "bytes_per_token": per_token,
            "per_token": format_kib(per_token),
            "whole_trace_bytes": int(tokens * per_token),
            "whole_trace": format_bytes(tokens * per_token),
            "median_prompt_bytes": int(median_input * per_token),
            "median_prompt": format_bytes(median_input * per_token),
        })
    return rows


def build_profile(records: Sequence[Dict[str, Any]], *, trace_id: str, footer: Optional[str] = None) -> Dict[str, Any]:
    ordered = [dict(record) for record in records]
    ordered.sort(key=lambda record: int(_number(record.get("timestamp"))))
    timestamps = [int(_number(record.get("timestamp"))) for record in ordered]
    inputs = [max(1, int(_number(record.get("input_length"), 1))) for record in ordered]
    outputs = [max(0, int(_number(record.get("output_length")))) for record in ordered]
    duration_ms = (max(timestamps) - min(timestamps)) if timestamps else 0
    duration_min = int(round(duration_ms / 60000)) if duration_ms else 0
    parents, shared = assign_parents(ordered)
    session_info = build_session_indexes(parents)
    sessions = session_info["sessions"]

    seen = set()
    reused = 0
    chain_reused = 0
    total_blocks = 0
    for index, record in enumerate(ordered):
        hashes = _hashes(record)
        prefix_len = shared[index]
        for position, block in enumerate(hashes):
            total_blocks += 1
            if block in seen:
                reused += 1
                if position < prefix_len:
                    chain_reused += 1
            else:
                seen.add(block)

    gaps = []
    for index, parent in enumerate(parents):
        if parent is None:
            continue
        gaps.append(max(0.0, (timestamps[index] - timestamps[parent]) / 1000))

    request_count = len(ordered)
    avg_rps = request_count / (duration_ms / 1000) if duration_ms else 0
    peak = _peak_per_second(timestamps)
    rpm = _requests_per_minute(timestamps)
    mean_rpm = (sum(rpm) / len(rpm)) if rpm else 0
    median_input = percentile(inputs, 0.5) if inputs else 0
    p90_input = percentile(inputs, 0.9) if inputs else 0
    p99_input = percentile(inputs, 0.99) if inputs else 0
    median_output = percentile(outputs, 0.5) if outputs else 0
    reuse_ratio = (reused / total_blocks) if total_blocks else 0
    chain_share = (chain_reused / reused) if reused else 0
    median_gap = percentile(gaps, 0.5) if gaps else 0
    p90_gap = percentile(gaps, 0.9) if gaps else 0
    longest = max((len(session) for session in sessions), default=0)
    input_histogram = _histogram(inputs, INPUT_BINS)
    output_histogram = _histogram(outputs, INPUT_BINS)
    kv_rows = kv_table(len(seen), median_input)

    window_ms = 10 * 60 * 1000
    window_indexes = [index for index, timestamp in enumerate(timestamps) if timestamps and timestamp - timestamps[0] <= window_ms]
    window_sessions = {session_info["session_of"][index] for index in window_indexes}

    if footer is None:
        unit = "request" if peak == 1 else "requests"
        footer = f"Peak {peak:,} {unit} in one second"

    qwen = kv_rows[0]["whole_trace"] if kv_rows else "0 B"
    hybrid = kv_rows[-1]["whole_trace"] if kv_rows else "0 B"
    notes = {
        "arrival": (
            f"Load holds near {mean_rpm:.0f} a minute with short spikes. "
            f"Within a second it peaks at {peak:,} requests, which is the burst a Poisson generator at the same average almost never makes."
        ),
        "prompts": (
            f"Half the prompts are under {compact_tokens(median_input)} tokens, and one in ten is over {compact_tokens(p90_input)}. "
            "The long tail is where prefill time and KV size grow fastest."
        ),
        "turns": (
            "A turn joins a session when its leading block hashes match an earlier request for at least two blocks, using the longest match. "
            f"{session_info['continued']:,} requests continue an earlier one, and that chain accounts for {round(chain_share * 100):.0f}% of all reused blocks."
        ),
        "gaps": (
            f"The median follow-up lands {_duration_phrase(median_gap)} later and one in ten waits over {_duration_phrase(p90_gap)}. "
            "A cache has to hold that context the whole time or the next turn pays full prefill again."
            if gaps else
            "This trace has no follow-up turn, so there is no gap to cache across."
        ),
        "kv": (
            f"This trace needs about {qwen} of KV on a dense GQA model and {hybrid} on a hybrid model with few attention layers. "
            "That gap decides whether host memory is enough or a shared tier is needed."
        ),
    }

    return {
        "schema_version": "tracerator.trace_profile.v1",
        "id": trace_id,
        "request_count": request_count,
        "duration_ms": duration_ms,
        "duration_min": duration_min,
        "avg_rps": round(avg_rps, 2),
        "peak_per_second": peak,
        "median_input": int(median_input),
        "p90_input": int(p90_input),
        "p99_input": int(p99_input),
        "median_output": int(median_output),
        "blocks_reused": round(reuse_ratio, 4),
        "unique_blocks": len(seen),
        "total_blocks": total_blocks,
        "reused_blocks": reused,
        "chain_reused_blocks": chain_reused,
        "chain_reuse_share": round(chain_share, 4),
        "rpm": rpm,
        "input_histogram": input_histogram,
        "output_histogram": output_histogram,
        "footer": footer,
        "sessions": {
            "count": len(sessions),
            "continued": session_info["continued"],
            "branches": session_info["branches"],
            "with_parent": session_info["with_parent"],
            "longest": longest,
            "turn_histogram": _turn_histogram(sessions),
        },
        "gaps": {
            "count": len(gaps),
            "median_s": round(median_gap, 3),
            "p90_s": round(p90_gap, 3),
            "histogram": _gap_histogram(gaps) if gaps else _gap_histogram([]),
        },
        "window_10m": {
            "requests": len(window_indexes),
            "sessions": len(window_sessions),
        },
        "kv": kv_rows,
        "notes": notes,
        "kpis": [
            {"value": f"{request_count:,}", "label": f"requests in {duration_min} min" if duration_min else "requests"},
            {"value": f"{avg_rps:.1f}/s", "label": f"average, peak {peak:,} in 1 s"},
            {"value": compact_tokens(median_input), "label": f"median input, p99 {compact_tokens(p99_input)}"},
            {"value": f"{int(median_output):,}", "label": "median output tokens"},
            {"value": f"{round(reuse_ratio * 100):.0f}%", "label": "of prefix blocks seen before"},
            {"value": f"{len(sessions):,}", "label": f"sessions, longest {longest} turns"},
        ],
    }


def attach_sessions(profile: Dict[str, Any], records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = [dict(record) for record in records]
    ordered.sort(key=lambda record: int(_number(record.get("timestamp"))))
    parents, shared = assign_parents(ordered)
    sessions = build_session_indexes(parents)["sessions"]
    return sessions_to_jsonl(ordered, parents, shared, sessions)
