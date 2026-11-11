"""Evaluate Turn-2 AIPerf records for a Turn-1 warm / Turn-2 resume case."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any


def _metric_value(record: dict[str, Any], name: str) -> float | None:
    metric = (record.get("metrics") or {}).get(name)
    if not isinstance(metric, dict) or not isinstance(metric.get("value"), (int, float)):
        return None
    value = float(metric["value"])
    unit = str(metric.get("unit") or "").lower()
    if unit in {"s", "sec", "second", "seconds"}:
        value *= 1_000
    elif unit in {"us", "microsecond", "microseconds"}:
        value /= 1_000
    elif unit in {"ns", "nanosecond", "nanoseconds"}:
        value /= 1_000_000
    return value


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile for an empty list")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "avg": round(mean(values), 3),
        "p50": round(median(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "unit": "ms",
    }


def load_turn2_ttft(
    run_dir: Path | str,
    *,
    repetitions: int,
    expected_input_tokens: int,
) -> list[float]:
    path = Path(run_dir) / "profile_export.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"missing AIPerf per-request export: {path}")

    records: dict[int, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        metadata = record.get("metadata") or {}
        phase = str(metadata.get("benchmark_phase") or "").lower()
        if phase and phase not in {"profiling", "profile"}:
            continue
        session_num = metadata.get("session_num")
        if isinstance(session_num, int):
            records[session_num] = record

    expected_sessions = [2 * repetition + 1 for repetition in range(repetitions)]
    missing = [session for session in expected_sessions if session not in records]
    if missing:
        raise ValueError(f"missing Turn-2 AIPerf session records: {missing}")

    ttft_values: list[float] = []
    for session in expected_sessions:
        record = records[session]
        if record.get("error"):
            raise ValueError(f"Turn-2 session {session} failed: {record['error']}")
        observed_input = _metric_value(record, "input_sequence_length")
        if observed_input is not None and round(observed_input) != expected_input_tokens:
            raise ValueError(
                f"Turn-2 session {session} input length {observed_input} does not match "
                f"case manifest {expected_input_tokens}"
            )
        ttft = _metric_value(record, "time_to_first_token")
        if ttft is None:
            raise ValueError(
                f"Turn-2 session {session} has no TTFT; profile with streaming and "
                "--export-level records"
            )
        ttft_values.append(ttft)
    return ttft_values


def compare_case_results(
    case_manifest_path: Path | str,
    regen_run_dir: Path | str,
    remote_kv_run_dir: Path | str,
    *,
    remote_kv_read_bytes: int | None = None,
) -> dict[str, Any]:
    case_path = Path(case_manifest_path)
    case = json.loads(case_path.read_text(encoding="utf-8"))
    repetitions = int(case["workload"]["repetitions"])
    expected_input = int(case["workload"]["turn2_input_tokens"])
    regen = load_turn2_ttft(
        regen_run_dir,
        repetitions=repetitions,
        expected_input_tokens=expected_input,
    )
    remote_kv = load_turn2_ttft(
        remote_kv_run_dir,
        repetitions=repetitions,
        expected_input_tokens=expected_input,
    )
    regen_summary = _summary(regen)
    remote_summary = _summary(remote_kv)
    read_verified = remote_kv_read_bytes is not None and remote_kv_read_bytes > 0
    published = case.get("published_reference")
    return {
        "artifact_type": "tracerator.lightinferra_remote_kv_comparison.v1",
        "case_id": case["case_id"],
        "case_manifest": str(case_path.resolve()),
        "turn2_ttft_ms": {
            "cache_regen": regen_summary,
            "remote_kv": remote_summary,
        },
        "observed_speedup_x": {
            "p50": round(regen_summary["p50"] / remote_summary["p50"], 3),
            "p95": round(regen_summary["p95"] / remote_summary["p95"], 3),
            "p99": round(regen_summary["p99"] / remote_summary["p99"], 3),
        },
        "functional_evidence": {
            "remote_kv_read_bytes": remote_kv_read_bytes,
            "remote_kv_read_verified": read_verified,
            "status": "valid" if read_verified else "incomplete",
            "note": (
                "A performance comparison is not functionally valid until storage or "
                "client telemetry proves Turn-2 external-KV reads."
            ),
        },
        "published_reference": published,
        "automatic_published_target_pass_fail": None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-manifest", required=True)
    parser.add_argument("--regen-run-dir", required=True)
    parser.add_argument("--remote-kv-run-dir", required=True)
    parser.add_argument("--remote-kv-read-bytes", type=int)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    remote_kv_run_dir = args.remote_kv_run_dir
    remote_kv_read_bytes = args.remote_kv_read_bytes
    result = compare_case_results(
        args.case_manifest,
        args.regen_run_dir,
        remote_kv_run_dir,
        remote_kv_read_bytes=remote_kv_read_bytes,
    )
    output = Path(args.output)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(
        "Turn-2 TTFT p95: regen={regen:.3f} ms, remote_kv={remote:.3f} ms, "
        "speedup={speedup:.3f}x, storage_evidence={evidence}".format(
            regen=result["turn2_ttft_ms"]["cache_regen"]["p95"],
            remote=result["turn2_ttft_ms"]["remote_kv"]["p95"],
            speedup=result["observed_speedup_x"]["p95"],
            evidence=result["functional_evidence"]["status"],
        )
    )


if __name__ == "__main__":
    main()
