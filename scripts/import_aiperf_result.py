#!/usr/bin/env python3
"""Normalize an AIPerf artifact directory into replay_result.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiperf_results import normalize_aiperf_run  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", help="AIPerf run directory containing profile_export_aiperf.json")
    parser.add_argument("-o", "--output", help="Output JSON path; defaults to <artifact_dir>/replay_result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = normalize_aiperf_run(args.artifact_dir)
    output = Path(args.output) if args.output else Path(args.artifact_dir) / "replay_result.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = result["summary"]
    print(f"Wrote {output}")
    print(
        "requests={request_count:.0f} errors={error_count:.0f} "
        "ttft_p95={ttft_p95:.2f}ms throughput={throughput:.3f}rps".format(
            request_count=summary.get("request_count") or 0,
            error_count=summary.get("error_count") or 0,
            ttft_p95=(result["latency_ms"]["time_to_first_token"].get("p95") or 0),
            throughput=summary.get("request_throughput") or 0,
        )
    )


if __name__ == "__main__":
    main()
