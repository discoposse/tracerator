#!/usr/bin/env python3
"""Normalize NVIDIA AIPerf replay artifacts for Tracerator comparison views."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ISL_BINS = [
    ("<=1K", 1, 1024),
    ("1-2K", 1025, 2048),
    ("2-4K", 2049, 4096),
    ("4-8K", 4097, 8192),
    ("8-16K", 8193, 16384),
    ("16-32K", 16385, 32768),
    ("32-64K", 32769, 65536),
    ("64-128K", 65537, 131072),
    (">128K", 131073, None),
]

STAT_KEYS = ("avg", "min", "max", "p50", "p90", "p95", "p99", "std", "count", "sum")


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _metric(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _stat(data: Dict[str, Any], key: str, stat: str = "avg") -> Optional[float]:
    value = _metric(data, key).get(stat)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _copy_stats(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    metric = _metric(data, key)
    out = {
        stat: float(metric[stat])
        for stat in STAT_KEYS
        if stat in metric and isinstance(metric.get(stat), (int, float))
    }
    if metric.get("unit"):
        out["unit"] = metric["unit"]
    return out


def _isl_bin_for_length(length: int) -> str:
    for name, lo, hi in ISL_BINS:
        if length >= lo and (hi is None or length <= hi):
            return name
    return ">128K"


def _distribution(lengths: Iterable[int]) -> Dict[str, Dict[str, float]]:
    values = list(lengths)
    total = max(1, len(values))
    counts = Counter(_isl_bin_for_length(int(v)) for v in values)
    return {
        name: {"count": int(counts.get(name, 0)), "share": round(counts.get(name, 0) / total, 6)}
        for name, _, _ in ISL_BINS
    }


def _read_profile_records(path: Path) -> Dict[str, Any]:
    input_lengths: List[int] = []
    output_lengths: List[int] = []
    error_count = 0
    success_count = 0
    error_types: Counter[str] = Counter()

    if not path.exists():
        return {
            "available": False,
            "success_count": None,
            "error_count": None,
            "observed_isl_distribution": None,
            "observed_osl_distribution": None,
            "error_types": {},
        }

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            metrics = row.get("metrics") or {}
            error = row.get("error")
            if error:
                error_count += 1
                error_types[str(error.get("type") or error.get("code") or "error")] += 1
            else:
                success_count += 1
            isl = metrics.get("input_sequence_length") or metrics.get("error_isl")
            osl = metrics.get("output_sequence_length") or metrics.get("output_token_count")
            if isinstance(isl, dict) and isinstance(isl.get("value"), (int, float)):
                input_lengths.append(int(isl["value"]))
            if isinstance(osl, dict) and isinstance(osl.get("value"), (int, float)):
                output_lengths.append(int(osl["value"]))

    return {
        "available": True,
        "success_count": success_count,
        "error_count": error_count,
        "observed_isl_distribution": _distribution(input_lengths),
        "observed_osl_distribution": _distribution(output_lengths),
        "error_types": dict(error_types),
    }


def _read_timeslices(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"available": False, "count": 0, "series": {}}

    if path.suffix == ".json":
        data = _load_json(path)
        slices = data.get("timeslices") if isinstance(data.get("timeslices"), list) else []
        series: Dict[str, List[Optional[float]]] = {}
        for item in slices:
            if not isinstance(item, dict):
                continue
            for key in ("time_to_first_token", "request_latency", "inter_token_latency", "output_token_throughput"):
                metric = item.get(key)
                series.setdefault(key, []).append(float(metric["avg"]) if isinstance(metric, dict) and isinstance(metric.get("avg"), (int, float)) else None)
        return {"available": True, "count": len(slices), "series": series}

    series = {}
    indexes = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("Stat") != "avg":
                continue
            indexes.add(row.get("Timeslice"))
            metric_key = str(row.get("Metric") or "").lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
            try:
                value = float(row.get("Value", ""))
            except ValueError:
                value = None
            series.setdefault(metric_key, []).append(value)
    return {"available": True, "count": len(indexes), "series": series}


def _model_name(input_config: Dict[str, Any]) -> Optional[str]:
    models = input_config.get("models")
    if isinstance(models, dict):
        items = models.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                return first.get("name")
    if isinstance(models, list) and models:
        first = models[0]
        if isinstance(first, dict):
            return first.get("name")
        return str(first)
    return None


def normalize_aiperf_run(run_dir: str | Path) -> Dict[str, Any]:
    """Return a compact Tracerator replay-result object for an AIPerf artifact directory."""
    root = Path(run_dir)
    if root.is_file():
        root = root.parent
    aggregate_path = root / "profile_export_aiperf.json"
    if not aggregate_path.exists():
        raise FileNotFoundError(f"missing {aggregate_path}")

    aggregate = _load_json(aggregate_path)
    records = _read_profile_records(root / "profile_export.jsonl")
    timeslices_json = root / "profile_export_aiperf_timeslices.json"
    timeslices_csv = root / "profile_export_aiperf_timeslices.csv"
    timeslices = _read_timeslices(timeslices_json if timeslices_json.exists() else timeslices_csv)

    request_count = _stat(aggregate, "request_count") or _stat(aggregate, "request_latency", "count") or 0
    error_count = _stat(aggregate, "error_request_count") or records.get("error_count") or 0
    success_count = max(0, int(round(request_count - error_count)))
    error_rate = float(error_count) / request_count if request_count else 0.0

    input_config = aggregate.get("input_config") if isinstance(aggregate.get("input_config"), dict) else {}
    run_info = aggregate.get("run_info") if isinstance(aggregate.get("run_info"), dict) else {}

    return {
        "artifact_type": "aiperf_replay_result",
        "schema_version": "tracerator.replay_result.v1",
        "source": {
            "artifact_dir": str(root),
            "aggregate_json": str(aggregate_path),
            "profile_jsonl": str(root / "profile_export.jsonl") if (root / "profile_export.jsonl").exists() else None,
            "timeslices": str(timeslices_json if timeslices_json.exists() else timeslices_csv) if timeslices.get("available") else None,
        },
        "run": {
            "benchmark_id": aggregate.get("benchmark_id") or run_info.get("benchmark_id"),
            "aiperf_version": aggregate.get("aiperf_version"),
            "start_time": aggregate.get("start_time"),
            "end_time": aggregate.get("end_time"),
            "cli_command": run_info.get("cli_command"),
            "model": _model_name(input_config),
            "endpoint": input_config.get("endpoint"),
        },
        "summary": {
            "request_count": request_count,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate": round(error_rate, 6),
            "benchmark_duration_sec": _stat(aggregate, "benchmark_duration"),
            "request_throughput": _stat(aggregate, "request_throughput"),
            "output_token_throughput": _stat(aggregate, "output_token_throughput"),
            "total_token_throughput": _stat(aggregate, "total_token_throughput"),
            "osl_mismatch_count": _stat(aggregate, "osl_mismatch_count"),
        },
        "latency_ms": {
            "request_latency": _copy_stats(aggregate, "request_latency"),
            "time_to_first_token": _copy_stats(aggregate, "time_to_first_token"),
            "time_to_second_token": _copy_stats(aggregate, "time_to_second_token"),
            "inter_token_latency": _copy_stats(aggregate, "inter_token_latency"),
            "http_waiting": _copy_stats(aggregate, "http_req_waiting"),
        },
        "tokens": {
            "input_sequence_length": _copy_stats(aggregate, "input_sequence_length"),
            "output_sequence_length": _copy_stats(aggregate, "output_sequence_length"),
            "error_input_sequence_length": _copy_stats(aggregate, "error_isl"),
            "total_input_sequence_length": _stat(aggregate, "total_isl"),
            "total_output_sequence_length": _stat(aggregate, "total_osl"),
        },
        "throughput": {
            "output_token_throughput_per_user": _copy_stats(aggregate, "output_token_throughput_per_user"),
            "prefill_throughput_per_user": _copy_stats(aggregate, "prefill_throughput_per_user"),
            "e2e_output_token_throughput": _copy_stats(aggregate, "e2e_output_token_throughput"),
        },
        "observed_isl_distribution": records.get("observed_isl_distribution"),
        "observed_osl_distribution": records.get("observed_osl_distribution"),
        "profile_records": {
            "available": records["available"],
            "success_count": records["success_count"],
            "error_count": records["error_count"],
            "error_types": records["error_types"],
        },
        "timeslices": timeslices,
        "error_summary": aggregate.get("error_summary") if isinstance(aggregate.get("error_summary"), list) else [],
    }
