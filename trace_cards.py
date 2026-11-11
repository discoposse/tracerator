#!/usr/bin/env python3
"""Portable metadata cards for built-in and generated Tracerator traces."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trace_profile import build_profile
from trace_sources import TraceSource, discover_trace_sources, load_source_records

SCHEMA_VERSION = "tracerator.trace_card.v2"
SAMPLE_LIMIT = 2_000
PROFILE_DIR = Path(__file__).resolve().parent / "site" / "data" / "traces"
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


def load_saved_profile(trace_id: str) -> Optional[Dict[str, Any]]:
    path = PROFILE_DIR / trace_id / "profile.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _charts_from_profile(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not profile or not profile.get("rpm"):
        return None
    histogram = profile.get("input_histogram") or []
    return {
        "rpm": profile.get("rpm") or [],
        "input_shares": [round(_number(bucket.get("share")), 6) for bucket in histogram],
        "input_labels": [bucket.get("label") for bucket in histogram],
        "duration_min": int(_number(profile.get("duration_min"))),
    }


def _stats_from_profile(profile: Dict[str, Any], manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest = manifest or {}
    return {
        "request_count": int(_number(manifest.get("n_requests"), profile.get("request_count"))),
        "sample_count": int(_number(profile.get("request_count"))),
        "duration_ms": int(_number(manifest.get("duration_ms"), profile.get("duration_ms"))),
        "avg_rps": round(_number(manifest.get("avg_rps"), profile.get("avg_rps")), 2),
        "max_concurrency": int(_number(manifest.get("max_concurrency"), profile.get("peak_per_second"))),
        "median_input": int(_number(manifest.get("median_input"), profile.get("median_input"))),
        "p95_input": int(_number(manifest.get("p95_input"), profile.get("p99_input"))),
        "median_output": int(_number(manifest.get("median_output"), profile.get("median_output"))),
        "cache_hit_ratio": round(_number(profile.get("blocks_reused")), 4),
        "blocks_reused": round(_number(profile.get("blocks_reused")), 4),
        "unique_blocks": int(_number(profile.get("unique_blocks"))),
        "peak_per_second": int(_number(profile.get("peak_per_second"))),
        "duration_min": int(_number(profile.get("duration_min"))),
        "request_count_is_sample": False,
    }


def _source_line(label: Optional[str], license_name: Optional[str]) -> str:
    text = label or "Tracerator"
    if license_name:
        return f"{text}, {license_name}"
    return text


def _build_card(
    *,
    card_id: str,
    title: str,
    family: str,
    description: str,
    stats: Dict[str, Any],
    available: bool,
    status: str,
    provenance: Dict[str, Any],
    cache_fidelity: str,
    category: Optional[str] = None,
    charts: Optional[Dict[str, Any]] = None,
    footer: Optional[str] = None,
    reference: bool = False,
    reference_note: Optional[str] = None,
    trace_url: Optional[str] = None,
    run_url: Optional[str] = None,
) -> Dict[str, Any]:
    distribution = stats.pop("isl_distribution", {})
    return {
        "artifact_type": "trace_card",
        "schema_version": SCHEMA_VERSION,
        "id": card_id,
        "title": title,
        "family": family,
        "description": description,
        "category": category,
        "source_line": _source_line(provenance.get("label"), provenance.get("license")),
        "availability": {
            "available": available,
            "status": status,
            "cache_fidelity": cache_fidelity,
            "reference": reference,
        },
        "provenance": provenance,
        "stats": stats,
        "distribution": distribution,
        "charts": charts,
        "footer": footer,
        "reference_note": reference_note,
        "traits": _traits(f"{family} {title}", description, stats, available),
        "links": {
            "trace": trace_url,
            "run": run_url,
            "original": provenance.get("url"),
        },
    }


def _profile_for_source(source: TraceSource) -> Optional[Dict[str, Any]]:
    saved = load_saved_profile(source.id)
    if saved:
        return saved
    if not source.available or source.mode in {"lightinferra_golden", "unsupported"}:
        return None
    records = load_source_records(source.id, max_records=None)
    return build_profile(records, trace_id=source.id)


def build_source_trace_card(source: TraceSource) -> Dict[str, Any]:
    status = source.status
    profile = None
    try:
        profile = _profile_for_source(source)
    except (OSError, ValueError, KeyError) as exc:
        status = f"adapter error: {exc}"
    charts = _charts_from_profile(profile)
    reference = (not source.available) or source.mode == "unsupported"
    if profile:
        stats = _stats_from_profile(profile)
        known_count = KNOWN_REQUEST_COUNTS.get(source.id)
        if known_count and not load_saved_profile(source.id):
            stats["request_count"] = known_count
        footer = profile.get("footer")
        available = True
    else:
        stats = _record_stats([])
        footer = None
        available = source.available and source.mode == "lightinferra_golden"
    if reference and source.mode == "unsupported":
        reference_note = "Not bundled. This release has no prefix hashes, so it stays reference-only."
    elif reference:
        reference_note = "Not bundled. Fetch it once with the import script and it replays like the others."
    elif source.mode == "lightinferra_golden":
        reference_note = (
            "Generated when you start a run. Pick a model and context, and Tracerator writes the Turn-1 warm and Turn-2 resume pair."
        )
        footer = "Open a run to generate it"
    else:
        reference_note = None
    return _build_card(
        card_id=source.id,
        title=source.label,
        family=source.family,
        description=source.description,
        stats=stats,
        available=available and not reference,
        status="ready" if available and not reference else status,
        provenance={
            "label": source.source_label or source.family,
            "url": source.source_url,
            "license": source.license,
            "adapter": source.mode,
            "citation": source.citation,
            "citation_url": source.citation_url,
        },
        cache_fidelity=source.cache_fidelity,
        category=source.category,
        charts=None if reference else charts,
        footer=footer,
        reference=reference,
        reference_note=reference_note,
        trace_url=f"/traces/{source.id}",
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
    profile = build_profile(records, trace_id=f"run-{run_id}")
    stats = _stats_from_profile(profile, manifest)
    return _build_card(
        card_id=f"run:{run_id}",
        title=title or base.get("label") or run_id,
        family=base.get("family") or "Tracerator run",
        description=base.get("description") or manifest.get("note") or "Generated Tracerator workload trace.",
        stats=stats,
        available=True,
        status="AIPerf ready" if integrity.get("aiperf_ready", True) else "review integrity",
        provenance={
            "label": source_label,
            "url": source_url,
            "license": base.get("license"),
            "adapter": base.get("mode"),
            "generated_from": base.get("id"),
        },
        cache_fidelity=base.get("cache_fidelity") or "not specified",
        category=None,
        charts=_charts_from_profile(profile),
        footer=profile.get("footer"),
        trace_url=f"/runs/{run_id}/view",
        run_url=f"/runs/{run_id}/view",
    )


def build_scenario_trace_card() -> Dict[str, Any]:
    profile = load_saved_profile("wall-clock-win")
    description = "Long prompts that come back after a delay, sized so the working set spills past GPU memory."
    if profile:
        stats = _stats_from_profile(profile)
        charts = _charts_from_profile(profile)
        footer = profile.get("footer") or "Seeded, so every run matches"
    else:
        stats = _record_stats([])
        charts = None
        footer = "Seeded, so every run matches"
    return _build_card(
        card_id="wall-clock-win",
        title="KV Reuse Stress",
        family="Tracerator",
        description=description,
        stats=stats,
        available=True,
        status="seeded",
        provenance={
            "label": "Built-in scenario, generated",
            "url": "https://github.com/discoposse/tracerator",
            "license": "Apache-2.0",
            "adapter": "scenario",
            "citation": "Tracerator KV reuse stress scenario",
            "citation_url": "https://github.com/discoposse/tracerator",
        },
        cache_fidelity="deterministic 512-token block hash_ids",
        category="long-context",
        charts=charts,
        footer=footer,
        trace_url="/traces/wall-clock-win",
    )


@lru_cache(maxsize=1)
def list_builtin_trace_cards() -> List[Dict[str, Any]]:
    cards = [build_source_trace_card(source) for source in discover_trace_sources()]
    scenario = build_scenario_trace_card()
    ordered = []
    for card in cards:
        if card["id"] == "synthetic":
            ordered.append(card)
            ordered.append(scenario)
        else:
            ordered.append(card)
    if scenario not in ordered:
        ordered.append(scenario)
    return ordered
