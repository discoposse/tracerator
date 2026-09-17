#!/usr/bin/env python3
"""Portable metadata cards for built-in and generated Tracerator traces."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional

from trace_sources import TraceSource, discover_trace_sources, load_source_records

SCHEMA_VERSION = "tracerator.trace_card.v1"
SAMPLE_LIMIT = 2_000
KNOWN_REQUEST_COUNTS = {
    "conversation": 12_031,
    "toolagent": 23_608,
    "synthetic": 3_993,
    "ragpulse": 7_106,
    "sageserve-code": 8_819,
    "sageserve-conversation": 19_366,
}
ISL_BINS = [
    ("<=1K", 1, 1024),
    ("1-2K", 1025, 2048),
    ("2-4K", 2049, 4096),
    ("4-8K", 4097, 8192),
    ("8-16K", 8193, 16384),
    ("16-32K", 16385, 32768),
    ("32-64K", 32769, 65536),
    ("64-128K", 65537, 131072),
    (">128K", 131073, 10**12),
]
PALETTES = [
    {"ink": "#0b1220", "accent": "#0f62fe", "signal": "#ff832b", "paper": "#f7f9fb"},
    {"ink": "#161616", "accent": "#198038", "signal": "#d02670", "paper": "#f4f4f4"},
    {"ink": "#082b36", "accent": "#007d79", "signal": "#f1c21b", "paper": "#f8f6ef"},
    {"ink": "#231f20", "accent": "#8a3ffc", "signal": "#24a148", "paper": "#f7f7f2"},
    {"ink": "#1c0f13", "accent": "#da1e28", "signal": "#1192e8", "paper": "#faf7f2"},
]


def _number(value: Any, fallback: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _percentile(values: Iterable[Any], quantile: float) -> int:
    ordered = sorted(int(_number(value)) for value in values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def _isl_distribution(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    counts = Counter()
    for record in records:
        length = max(1, int(_number(record.get("input_length"), 1)))
        for name, low, high in ISL_BINS:
            if low <= length <= high:
                counts[name] += 1
                break
    total = max(1, len(records))
    return {
        name: {"count": counts[name], "share": round(counts[name] / total, 6)}
        for name, _low, _high in ISL_BINS
    }


def _normalize_distribution(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for name, _low, _high in ISL_BINS:
        bucket = value.get(name, {})
        if isinstance(bucket, dict):
            result[name] = {
                "count": int(_number(bucket.get("count"))),
                "share": round(_number(bucket.get("share")), 6),
            }
        else:
            result[name] = {"count": 0, "share": round(_number(bucket), 6)}
    return result


def _record_stats(
    records: List[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    manifest = manifest or {}
    timestamps = [int(_number(record.get("timestamp"))) for record in records]
    inputs = [int(_number(record.get("input_length"))) for record in records]
    outputs = [int(_number(record.get("output_length"))) for record in records]
    duration_ms = max(timestamps) - min(timestamps) if timestamps else 0
    seen = set()
    reused = 0
    total_blocks = 0
    for record in records:
        hashes = record.get("hash_ids")
        if not isinstance(hashes, list):
            continue
        total_blocks += len(hashes)
        for block_id in hashes:
            if block_id in seen:
                reused += 1
            else:
                seen.add(block_id)
    measured_hit = reused / total_blocks if total_blocks else 0
    distribution = _normalize_distribution(manifest.get("isl_distribution"))
    if not distribution:
        distribution = _isl_distribution(records)
    request_count = int(_number(manifest.get("n_requests"), len(records)))
    return {
        "request_count": request_count,
        "sample_count": len(records),
        "duration_ms": int(_number(manifest.get("duration_ms"), duration_ms)),
        "avg_rps": round(
            _number(
                manifest.get("avg_rps"),
                len(records) / (duration_ms / 1000) if duration_ms > 0 else 0,
            ),
            2,
        ),
        "max_concurrency": int(
            _number(
                manifest.get("max_concurrency"),
                max(Counter(timestamps).values()) if timestamps else 0,
            )
        ),
        "median_input": int(_number(manifest.get("median_input"), _percentile(inputs, 0.5))),
        "p95_input": int(_number(manifest.get("p95_input"), _percentile(inputs, 0.95))),
        "median_output": int(_number(manifest.get("median_output"), _percentile(outputs, 0.5))),
        "cache_hit_ratio": round(
            _number(
                manifest.get("approx_cache_hit_ratio", manifest.get("reused_token_fraction")),
                measured_hit,
            ),
            4,
        ),
        "unique_blocks": int(_number(manifest.get("unique_block_ids"), len(seen))),
        "isl_distribution": distribution,
    }


def _fingerprint(card_id: str, distribution: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    digest = hashlib.blake2b(card_id.encode("utf-8"), digest_size=8).digest()
    palette = PALETTES[int.from_bytes(digest[:2], "big") % len(PALETTES)]
    bars = [
        round(_number((distribution.get(name) or {}).get("share")), 6)
        for name, _low, _high in ISL_BINS
    ]
    return {
        "serial": digest.hex().upper()[:8],
        "palette": palette,
        "bars": bars,
        "route": [byte % 9 for byte in digest],
    }


def _traits(family: str, description: str, stats: Dict[str, Any], available: bool) -> List[str]:
    text = f"{family} {description}".lower()
    traits: List[str] = []
    if not available:
        traits.append("REFERENCE")
    if re.search(r"\b(agent|agentic|code|tool-use|tool agent|toolagent)\b", text):
        traits.append("AGENTIC")
    elif "rag" in text:
        traits.append("RETRIEVAL")
    elif "conversation" in text or "chat" in text:
        traits.append("DIALOGUE")
    else:
        traits.append("MIXED")
    p95 = int(_number(stats.get("p95_input")))
    if p95 >= 32_768:
        traits.append("LONG TAIL")
    elif p95 >= 8_192:
        traits.append("LONG CONTEXT")
    else:
        traits.append("COMPACT")
    hit = _number(stats.get("cache_hit_ratio"))
    if hit >= 0.5:
        traits.append("HIGH REUSE")
    elif hit >= 0.15:
        traits.append("PREFIX REUSE")
    if int(_number(stats.get("max_concurrency"))) >= 16:
        traits.append("BURSTY")
    return traits[:4]


def _build_card(
    *,
    card_id: str,
    title: str,
    family: str,
    description: str,
    stats: Dict[str, Any],
    available: bool,
    status: str,
    edition: str,
    provenance: Dict[str, Any],
    cache_fidelity: str,
    run_url: Optional[str] = None,
) -> Dict[str, Any]:
    distribution = stats.pop("isl_distribution", {})
    fingerprint = _fingerprint(card_id, distribution)
    return {
        "artifact_type": "trace_card",
        "schema_version": SCHEMA_VERSION,
        "id": card_id,
        "title": title,
        "family": family,
        "description": description,
        "edition": edition,
        "availability": {
            "available": available,
            "status": status,
            "cache_fidelity": cache_fidelity,
        },
        "provenance": provenance,
        "stats": stats,
        "distribution": distribution,
        "traits": _traits(f"{family} {title}", description, stats, available),
        "fingerprint": fingerprint,
        "links": {
            "run": run_url,
            "original": provenance.get("url"),
        },
    }


def build_source_trace_card(source: TraceSource) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    status = source.status
    if source.available:
        try:
            records = load_source_records(source.id, max_records=SAMPLE_LIMIT)
        except (OSError, ValueError, KeyError) as exc:
            status = f"adapter error: {exc}"
    available = bool(records)
    stats = _record_stats(records)
    known_count = KNOWN_REQUEST_COUNTS.get(source.id)
    if known_count:
        stats["request_count"] = known_count
        stats["request_count_is_sample"] = False
    else:
        stats["request_count_is_sample"] = bool(
            available and len(records) >= SAMPLE_LIMIT
        )
    return _build_card(
        card_id=f"source:{source.id}",
        title=source.label,
        family=source.family,
        description=source.description,
        stats=stats,
        available=available,
        status="ready" if available else status,
        edition="BUILT-IN",
        provenance={
            "label": source.source_label or source.family,
            "url": source.source_url,
            "license": source.license,
            "adapter": source.mode,
            "sample_limit": SAMPLE_LIMIT if available else None,
        },
        cache_fidelity=source.cache_fidelity,
    )


def build_run_trace_card(
    run_id: str,
    title: str,
    records: List[Dict[str, Any]],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    base = manifest.get("base_source") or {}
    integrity = manifest.get("integrity") or {}
    source_url = base.get("source_url")
    source_label = base.get("source_label") or base.get("family") or "Tracerator"
    return _build_card(
        card_id=f"run:{run_id}",
        title=title or base.get("label") or run_id,
        family=base.get("family") or "Tracerator run",
        description=base.get("description") or manifest.get("note") or "Generated Tracerator workload trace.",
        stats=_record_stats(records, manifest),
        available=True,
        status="AIPerf ready" if integrity.get("aiperf_ready", True) else "review integrity",
        edition="GENERATED",
        provenance={
            "label": source_label,
            "url": source_url,
            "license": base.get("license"),
            "adapter": base.get("mode"),
            "generated_from": base.get("id"),
        },
        cache_fidelity=base.get("cache_fidelity") or "not specified",
        run_url=f"/runs/{run_id}",
    )


def build_scenario_trace_card() -> Dict[str, Any]:
    distribution = {
        "<=1K": {"count": 40, "share": 0.01},
        "1-2K": {"count": 80, "share": 0.02},
        "2-4K": {"count": 160, "share": 0.04},
        "4-8K": {"count": 320, "share": 0.08},
        "8-16K": {"count": 640, "share": 0.16},
        "16-32K": {"count": 920, "share": 0.23},
        "32-64K": {"count": 1120, "share": 0.28},
        "64-128K": {"count": 600, "share": 0.15},
        ">128K": {"count": 120, "share": 0.03},
    }
    stats = {
        "request_count": 4000,
        "sample_count": 4000,
        "duration_ms": 1_995_000,
        "avg_rps": 2.01,
        "max_concurrency": 20,
        "median_input": 32_768,
        "p95_input": 98_304,
        "median_output": 96,
        "cache_hit_ratio": 0.82,
        "unique_blocks": 48_000,
        "isl_distribution": distribution,
    }
    return _build_card(
        card_id="scenario:wall-clock-win",
        title="KV Reuse Stress Scenario",
        family="Tracerator scenario",
        description=(
            "Purpose-built prefix-cache pressure with delayed reuse, high fan-out, "
            "and a long-context working set."
        ),
        stats=stats,
        available=True,
        status="generator built-in",
        edition="SCENARIO",
        provenance={
            "label": "Tracerator",
            "url": "/runs/new",
            "license": None,
            "adapter": "scenario",
        },
        cache_fidelity="deterministic 512-token block hash_ids",
    )


@lru_cache(maxsize=1)
def list_builtin_trace_cards() -> List[Dict[str, Any]]:
    cards = [build_source_trace_card(source) for source in discover_trace_sources()]
    cards.append(build_scenario_trace_card())
    return cards
