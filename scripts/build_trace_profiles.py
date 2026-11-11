#!/usr/bin/env python3
"""Build whole-trace profiles and session files for the trace pages.

Writes site/data/traces/<id>/profile.json and sessions.jsonl.gz.
The KV reuse stress scenario is generated at seed 42 and saved beside its profile.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trace_profile import attach_sessions, build_profile  # noqa: E402
from trace_sources import source_by_id  # noqa: E402

OUT = ROOT / "site" / "data" / "traces"

PAGE_COPY = {
    "conversation": {
        "title": "Mooncake Conversation",
        "family": "Mooncake",
        "page_summary": (
            "One hour of real multi-turn chat traffic from a production LLM service, "
            "released by the Mooncake team with block hashes for every 512-token prefix."
        ),
    },
    "toolagent": {
        "title": "Mooncake Tool and Agent",
        "family": "Mooncake",
        "page_summary": (
            "One hour of agent and tool traffic from the same Mooncake release. "
            "Turns come back within seconds, outputs stay short, and prefix blocks are shared heavily."
        ),
    },
    "synthetic": {
        "title": "Mooncake Synthetic",
        "family": "Mooncake",
        "page_summary": (
            "Long single prompts built from public data in the Mooncake FAST '25 release, "
            "with a few deep multi-turn chains mixed in."
        ),
    },
    "wall-clock-win": {
        "title": "KV Reuse Stress",
        "family": "Tracerator",
        "page_summary": (
            "A seeded workload of long prompts that return after a delay, sized so the "
            "working set spills past GPU memory. The same seed reproduces the trace."
        ),
        "footer": "Seeded, so every run matches",
    },
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_sessions(path: Path, sessions: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for session in sessions:
            handle.write(json.dumps(session, separators=(",", ":")) + "\n")


def _decorate(profile: dict, source_meta: dict) -> dict:
    copy = PAGE_COPY.get(profile["id"], {})
    profile["title"] = copy.get("title") or source_meta.get("label")
    profile["family"] = copy.get("family") or source_meta.get("family")
    profile["page_summary"] = copy.get("page_summary") or source_meta.get("description")
    profile["description"] = source_meta.get("description")
    profile["license"] = source_meta.get("license")
    profile["source_label"] = source_meta.get("source_label")
    profile["source_url"] = source_meta.get("source_url")
    profile["citation"] = source_meta.get("citation")
    profile["citation_url"] = source_meta.get("citation_url")
    profile["category"] = source_meta.get("category")
    if copy.get("footer"):
        profile["footer"] = copy["footer"]
    return profile


def _source_meta(source_id: str) -> dict:
    if source_id == "wall-clock-win":
        return {
            "label": "KV Reuse Stress",
            "family": "Tracerator",
            "description": (
                "Long prompts that come back after a delay, sized so the working set spills past GPU memory."
            ),
            "license": "Apache-2.0",
            "source_label": "Built-in scenario, generated",
            "source_url": "https://github.com/discoposse/tracerator",
            "citation": "Tracerator KV reuse stress scenario",
            "citation_url": "https://github.com/discoposse/tracerator",
            "category": "long-context",
        }
    source = source_by_id(source_id)
    return {
        "label": source.label,
        "family": source.family,
        "description": source.description,
        "license": source.license,
        "source_label": source.source_label,
        "source_url": source.source_url,
        "citation": source.citation,
        "citation_url": source.citation_url,
        "category": source.category,
    }


def build_one(trace_id: str, records: list) -> dict:
    meta = _source_meta(trace_id)
    profile = build_profile(records, trace_id=trace_id, footer=PAGE_COPY.get(trace_id, {}).get("footer"))
    profile = _decorate(profile, meta)
    sessions = attach_sessions(profile, records)
    out = OUT / trace_id
    _write_json(out / "profile.json", profile)
    _write_sessions(out / "sessions.jsonl.gz", sessions)
    if trace_id == "wall-clock-win":
        with (out / "trace.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps({
                    "timestamp": record["timestamp"],
                    "input_length": record["input_length"],
                    "output_length": record["output_length"],
                    "hash_ids": record["hash_ids"],
                }) + "\n")
    print(
        f"{trace_id}: {profile['request_count']} requests, "
        f"{profile['duration_min']} min, median {profile['median_input']}, "
        f"reuse {profile['blocks_reused']:.1%}, peak {profile['peak_per_second']}/s, "
        f"sessions {profile['sessions']['count']}"
    )
    return profile


def _scenario_records() -> list:
    from app import generate_trace_data

    records, _manifest = generate_trace_data({
        "trace_scenario": "wall_clock_win",
        "scale": 1,
        "input_mult": 1,
        "output_mult": 0.8,
        "reuse_bias": 0.92,
        "seed": 42,
    })
    return records


def _read_jsonl(path: Path) -> list:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def main() -> None:
    for source_id in ("conversation", "toolagent", "synthetic"):
        build_one(source_id, _read_jsonl(source_by_id(source_id).path))
    build_one("wall-clock-win", _scenario_records())


if __name__ == "__main__":
    main()
