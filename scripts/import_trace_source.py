#!/usr/bin/env python3
"""Import external LLM serving traces into AIPerf-ready Mooncake JSONL.

Supports:
- mooncake: traces that already use one hash_id per 512-token prompt block.
- component: traces such as RAGPulse where hash_ids identify ordered prompt
  components/documents/session pieces rather than fixed-size KV blocks.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACE_GEN = os.path.join(ROOT, "Mooncake", "trace_gen")
if TRACE_GEN not in sys.path:
    sys.path.insert(0, TRACE_GEN)

from generator import (  # noqa: E402
    BLOCK_SIZE,
    analyze_trace,
    blocks_for_length,
    isl_distribution,
    normalize_trace_for_aiperf,
    save_manifest,
    save_trace,
    validate_hash_block_consistency,
)


def _stable_int(value: Any, salt: str = "") -> int:
    raw = f"{salt}:{value}".encode("utf-8", "replace")
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, "big") % 9_000_000_000 + 1_000_000


def _read_jsonish_lines(path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("JSON input must be a list of records")
            records = [dict(r) for r in data]
        else:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return records


def _coerce_timestamp(value: Any, timestamp_unit: str) -> int:
    if value is None:
        return 0
    numeric = float(value)
    if timestamp_unit == "seconds":
        return int(round(numeric * 1000))
    if timestamp_unit == "milliseconds":
        return int(round(numeric))
    if timestamp_unit == "auto":
        # RAGPulse documents seconds from trace start; Mooncake uses ms. Very small
        # non-integral values are almost certainly seconds.
        if numeric < 10_000 and numeric != int(numeric):
            return int(round(numeric * 1000))
        return int(round(numeric))
    raise ValueError(f"unknown timestamp unit {timestamp_unit}")


def _component_hashes_to_blocks(components: Iterable[Any], needed_blocks: int, source_name: str, record_salt: str) -> List[int]:
    component_list = list(components)
    if not component_list:
        component_list = [f"cold:{record_salt}"]

    out: List[int] = []
    # Emit one stable "header" block per component first. This preserves ordered
    # component prefixes across requests even when their total ISL/block count differs.
    for i, component in enumerate(component_list):
        out.append(_stable_int(component, salt=f"{source_name}:component:{i}:header"))
        if len(out) == needed_blocks:
            return out

    # Then add deterministic body blocks round-robin. Repeated documents/components
    # remain shared at the same component position, but no synthetic random ids are
    # introduced until we genuinely run out of source structure.
    body_index = 0
    while len(out) < needed_blocks and component_list:
        for i, component in enumerate(component_list):
            out.append(_stable_int(component, salt=f"{source_name}:component:{i}:body:{body_index}"))
            if len(out) == needed_blocks:
                return out
        body_index += 1

    while len(out) < needed_blocks:
        out.append(_stable_int(f"pad:{len(out)}", salt=record_salt))
    return out


def _flatten_components(raw_hashes: Any) -> List[Any]:
    if isinstance(raw_hashes, dict):
        ordered_keys = ["sys_prompt", "passages_ids", "history", "web_search", "user_input"]
        flattened: List[Any] = []
        for key in ordered_keys:
            value = raw_hashes.get(key, [])
            if isinstance(value, list):
                flattened.extend((key, item) for item in value)
            elif value:
                flattened.append((key, value))
        for key, value in raw_hashes.items():
            if key in ordered_keys:
                continue
            if isinstance(value, list):
                flattened.extend((key, item) for item in value)
            elif value:
                flattened.append((key, value))
        return flattened
    if isinstance(raw_hashes, list):
        return list(raw_hashes)
    return []


def import_records(
    records: List[Dict[str, Any]],
    *,
    mode: str,
    source_name: str,
    timestamp_unit: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        input_length = int(record.get("input_length", record.get("input_tokens", 0)))
        output_length = int(record.get("output_length", record.get("output_tokens", 1)))
        if input_length <= 0:
            raise ValueError(f"record[{index}] missing positive input_length")
        if output_length <= 0:
            output_length = 1

        timestamp = _coerce_timestamp(record.get("timestamp", record.get("ts", 0)), timestamp_unit)
        needed = blocks_for_length(input_length, BLOCK_SIZE)
        raw_hashes = record.get("hash_ids") or record.get("hashes") or []

        if mode == "component":
            session = record.get("session_id", record.get("conversation_id", ""))
            record_salt = f"{source_name}:{session}:{index}"
            hash_ids = _component_hashes_to_blocks(
                _flatten_components(raw_hashes),
                needed,
                source_name=source_name,
                record_salt=record_salt,
            )
        else:
            hash_ids = [int(_stable_int(v, salt=source_name)) if not isinstance(v, int) else v for v in raw_hashes]

        imported = {
            "timestamp": timestamp,
            "input_length": input_length,
            "output_length": output_length,
            "hash_ids": hash_ids,
        }
        if "session_id" in record:
            imported["session_id"] = record["session_id"]
        out.append(imported)

    out.sort(key=lambda r: (r["timestamp"], r["input_length"]))
    if mode == "mooncake":
        out = normalize_trace_for_aiperf(out, BLOCK_SIZE)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="source JSONL or JSON list")
    parser.add_argument("-o", "--output", required=True, help="output Mooncake-format JSONL")
    parser.add_argument("--manifest", help="output manifest path (default: <output>.manifest.json)")
    parser.add_argument("--source-name", default=None, help="human source label for provenance")
    parser.add_argument("--mode", choices=["mooncake", "component"], default="mooncake")
    parser.add_argument(
        "--timestamp-unit",
        choices=["milliseconds", "seconds", "auto"],
        default="auto",
        help="source timestamp unit; RAGPulse uses seconds, Mooncake uses milliseconds",
    )
    args = parser.parse_args()

    source_name = args.source_name or os.path.splitext(os.path.basename(args.input))[0]
    records = _read_jsonish_lines(args.input)
    imported = import_records(records, mode=args.mode, source_name=source_name, timestamp_unit=args.timestamp_unit)
    errors = validate_hash_block_consistency(imported, BLOCK_SIZE)
    if errors:
        raise SystemExit("post-import integrity failed:\n" + "\n".join(errors[:10]))

    save_trace(imported, args.output)
    analysis = analyze_trace(imported, name=source_name)
    timestamp_counts = Counter(r["timestamp"] for r in imported)
    manifest = {
        "generator": "tracerator import_trace_source",
        "source_name": source_name,
        "source_file": os.path.abspath(args.input),
        "mode": args.mode,
        "schema": "mooncake_trace",
        "integrity": {
            "aiperf_ready": True,
            "block_size": BLOCK_SIZE,
            "hash_ids_rule": "len(hash_ids) == ceil(input_length / block_size)",
            "timestamp_unit": "milliseconds",
        },
        "stats": {
            "n_requests": len(imported),
            "duration_ms": analysis.duration_ms,
            "avg_rps": round(analysis.avg_rps, 3),
            "median_input": analysis.median_input,
            "median_output": analysis.median_output,
            "approx_cache_hit_ratio": round(analysis.approx_cache_hit_ratio, 3),
            "max_concurrency": max(timestamp_counts.values()) if timestamp_counts else 0,
            "isl_distribution": isl_distribution([r["input_length"] for r in imported]),
        },
        "notes": (
            "component mode expands ordered component/document hash_ids into deterministic "
            "512-token block hash_ids so shared prompt components remain shared prefixes "
            "for AIPerf mooncake_trace replay."
            if args.mode == "component"
            else "mooncake mode normalizes existing block hash_ids to the AIPerf block rule."
        ),
    }
    manifest_path = args.manifest or args.output + ".manifest.json"
    save_manifest(manifest, manifest_path)
    print(f"Wrote {len(imported)} records to {args.output}")
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
