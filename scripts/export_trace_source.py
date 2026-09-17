#!/usr/bin/env python3
"""Export any Tracerator baseline source as an AIPerf-ready replay bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import attach_kv_planning, generate_trace_data, render_isl_distribution_text  # noqa: E402
from trace_sources import sources_for_api  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="sageserve-code", help="source id from /sources, e.g. sageserve-code")
    parser.add_argument("--output-dir", help="directory for trace.jsonl and manifest.json")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--input-mult", type=float, default=1.0)
    parser.add_argument("--output-mult", type=float, default=1.0)
    parser.add_argument("--reuse-bias", type=float, default=0.75)
    parser.add_argument("--new-sessions", type=int, default=0)
    parser.add_argument("--modeled-mix", type=float, default=0.0)
    parser.add_argument("--isl-profile", default="empirical", choices=["empirical", "rag", "balanced", "long_context", "short_chat"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--list-sources", action="store_true", help="print discovered sources and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_sources:
        for source in sources_for_api():
            status = "ready" if source["available"] else source["status"]
            print(f"{source['id']}\t{source['label']}\t{source['family']}\t{status}")
        return
    if not args.output_dir:
        raise SystemExit("--output-dir is required unless --list-sources is used")

    params = {
        "base": args.base,
        "scale": args.scale,
        "input_mult": args.input_mult,
        "output_mult": args.output_mult,
        "reuse_bias": args.reuse_bias,
        "new_sessions": args.new_sessions,
        "modeled_mix": args.modeled_mix,
        "isl_profile": args.isl_profile,
        "seed": args.seed,
    }
    reqs, manifest = generate_trace_data(params)
    attach_kv_planning(reqs, manifest, params)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"
    manifest_path = output_dir / "manifest.json"
    isl_path = output_dir / "isl_distribution.txt"

    with trace_path.open("w", encoding="utf-8") as handle:
        for req in reqs:
            handle.write(json.dumps(req, separators=(",", ":")) + "\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    isl_path.write_text(render_isl_distribution_text(manifest["isl_distribution"], "TRACE: trace.jsonl"), encoding="utf-8")

    print(f"Wrote {len(reqs)} requests to {trace_path}")
    print(f"Wrote manifest to {manifest_path}")
    print(f"Wrote ISL summary to {isl_path}")


if __name__ == "__main__":
    main()
