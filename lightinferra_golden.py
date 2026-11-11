"""Build Turn-1 warm / Turn-2 resume traces for remote KV tests.

Published numbers from the cited methodology are reference points, not pass/fail
claims for another storage system. This module reproduces the observable workload shape:
an initial long-context prefill followed by a resume request with a fully shared
prefix and a small new tail. Run the same bundle with remote KV disabled
(cache regeneration) and enabled on an external KV store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kv_cache_planning import BYTES_PER_GIB, cache_bytes_per_token

SOURCE_BLOG_URL = (
    "https://www.lightbitslabs.com/blog/"
    "introducing-lightinferra-280x-improved-ai-token-economy-by-lightbits-labs/"
)
DEFAULT_LOGICAL_BLOCK_TOKENS = 512
DEFAULT_PHYSICAL_PAGE_TOKENS = 16
DEFAULT_RESUME_TAIL_TOKENS = 32
DEFAULT_OUTPUT_TOKENS = 32
DEFAULT_SEED = 20260310


@dataclass(frozen=True)
class ReferenceResult:
    context_tokens: int
    published_context_label: str
    regen_ttft_ms: float
    remote_ttft_ms: float
    published_speedup: float
    regen_effective_tokens_per_second: float | None = None
    remote_effective_tokens_per_second: float | None = None
    note: str = ""


@dataclass(frozen=True)
class GoldenModel:
    id: str
    published_model: str
    kv_model_id: str
    max_context_tokens: int
    curve_points: Sequence[int]
    references: Mapping[int, ReferenceResult] = field(default_factory=dict)


COMMON_CURVE_POINTS = (
    50,
    100,
    200,
    400,
    800,
    1_600,
    3_200,
    6_400,
    12_800,
    25_600,
    51_200,
    102_400,
)


GOLDEN_MODELS: dict[str, GoldenModel] = {
    "qwen2.5-7b": GoldenModel(
        id="qwen2.5-7b",
        published_model="Qwen/Qwen2.5-7B-Instruct-1M",
        kv_model_id="qwen2.5-7b-instruct-1m",
        max_context_tokens=1_010_000,
        curve_points=COMMON_CURVE_POINTS + (204_800, 409_600, 819_200, 1_010_000),
        references={
            409_600: ReferenceResult(
                context_tokens=409_600,
                published_context_label="410k",
                regen_ttft_ms=72_600,
                remote_ttft_ms=711,
                published_speedup=102,
                note=(
                    "The blog rounds this point to 410k; the trace uses the binary "
                    "sweep point 409,600 tokens visible in the published curve."
                ),
            ),
            1_010_000: ReferenceResult(
                context_tokens=1_010_000,
                published_context_label="1.01M",
                regen_ttft_ms=372_300,
                remote_ttft_ms=1_300,
                published_speedup=286,
                regen_effective_tokens_per_second=0.3,
                remote_effective_tokens_per_second=27.2,
            ),
        },
    ),
    "deepseek-r1-70b": GoldenModel(
        id="deepseek-r1-70b",
        published_model="neuralmagic/DeepSeek-R1-Distill-Llama-70B-FP8-dynamic",
        kv_model_id="deepseek-r1-distill-llama-70b",
        max_context_tokens=131_072,
        curve_points=COMMON_CURVE_POINTS + (131_072,),
        references={
            131_072: ReferenceResult(
                context_tokens=131_072,
                published_context_label="131k",
                regen_ttft_ms=70_800,
                remote_ttft_ms=465,
                published_speedup=152,
                regen_effective_tokens_per_second=1.7,
                remote_effective_tokens_per_second=18.7,
            ),
        },
    ),
    "llama4-scout-17b": GoldenModel(
        id="llama4-scout-17b",
        published_model="RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic",
        kv_model_id="llama4-scout-17b-16e",
        max_context_tokens=409_600,
        curve_points=COMMON_CURVE_POINTS + (204_800, 409_600),
        references={
            409_600: ReferenceResult(
                context_tokens=409_600,
                published_context_label="400k",
                regen_ttft_ms=103_000,
                remote_ttft_ms=457,
                published_speedup=226,
                note=(
                    "The blog rounds this point to 400k; the trace uses the binary "
                    "sweep point 409,600 tokens visible in the published curve."
                ),
            ),
        },
    ),
}


def _stable_id(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    value = int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
    return value & ((1 << 63) - 1)


def _hashes_for_turns(
    *,
    model_id: str,
    context_tokens: int,
    resume_tail_tokens: int,
    logical_block_tokens: int,
    repetition: int,
    seed: int,
) -> tuple[list[int], list[int], int]:
    full_shared_blocks = context_tokens // logical_block_tokens
    partial_tokens = context_tokens % logical_block_tokens
    shared = [
        _stable_id(seed, model_id, context_tokens, repetition, "shared", block_index)
        for block_index in range(full_shared_blocks)
    ]

    turn1 = list(shared)
    if partial_tokens:
        turn1.append(_stable_id(seed, model_id, context_tokens, repetition, "turn1-partial"))

    turn2_length = context_tokens + resume_tail_tokens
    turn2_needed = max(1, math.ceil(turn2_length / logical_block_tokens))
    turn2 = list(shared)
    for tail_index in range(turn2_needed - len(shared)):
        turn2.append(
            _stable_id(seed, model_id, context_tokens, repetition, "turn2-tail", tail_index)
        )
    return turn1, turn2, full_shared_blocks


def _recommended_gap_ms(model: GoldenModel, context_tokens: int) -> int:
    reference = model.references.get(context_tokens)
    if reference:
        baseline_ms = reference.regen_ttft_ms
    else:
        longest = max(model.references.values(), key=lambda item: item.context_tokens)
        baseline_ms = longest.regen_ttft_ms * context_tokens / longest.context_tokens
    # Leave enough room for Turn 1 to complete without Turn 2 queueing behind it.
    return max(10_000, int(math.ceil((baseline_ms * 1.25 + 30_000) / 1_000) * 1_000))


def _reference_dict(reference: ReferenceResult | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    return {
        "source": SOURCE_BLOG_URL,
        "published_context_label": reference.published_context_label,
        "regen_ttft_ms": reference.regen_ttft_ms,
        "remote_kv_ttft_ms": reference.remote_ttft_ms,
        "published_speedup_x": reference.published_speedup,
        "computed_speedup_x": round(reference.regen_ttft_ms / reference.remote_ttft_ms, 3),
        "regen_effective_tokens_per_second": reference.regen_effective_tokens_per_second,
        "remote_kv_effective_tokens_per_second": reference.remote_effective_tokens_per_second,
        "note": reference.note or None,
        "semantics": "external reference only; not an automatic pass/fail threshold",
    }


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), separators=(",", ":")) + "\n")


def _model_contexts(model: GoldenModel, suite: str) -> Sequence[int]:
    if suite == "headline":
        return tuple(model.references)
    if suite == "curve":
        return model.curve_points
    raise ValueError(f"Unsupported suite: {suite}")


def build_case(
    *,
    model: GoldenModel,
    context_tokens: int,
    repetitions: int,
    logical_block_tokens: int,
    physical_page_tokens: int,
    resume_tail_tokens: int,
    output_tokens: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if context_tokens > model.max_context_tokens:
        raise ValueError(f"{context_tokens} exceeds {model.id} context ceiling {model.max_context_tokens}")
    if min(repetitions, logical_block_tokens, physical_page_tokens, resume_tail_tokens, output_tokens) <= 0:
        raise ValueError("repetitions and token/page sizes must all be positive")

    kv = cache_bytes_per_token(model.kv_model_id, precision="bf16_fp16")
    bytes_per_token = int(kv["bytes_per_token"])
    physical_page_bytes = bytes_per_token * physical_page_tokens
    gap_ms = _recommended_gap_ms(model, context_tokens)
    turn1_rows: list[dict[str, Any]] = []
    turn2_rows: list[dict[str, Any]] = []
    combined_rows: list[dict[str, Any]] = []
    storage_ops: list[dict[str, Any]] = []
    shared_blocks_by_repetition: list[int] = []

    for repetition in range(repetitions):
        turn1_hashes, turn2_hashes, shared_blocks = _hashes_for_turns(
            model_id=model.id,
            context_tokens=context_tokens,
            resume_tail_tokens=resume_tail_tokens,
            logical_block_tokens=logical_block_tokens,
            repetition=repetition,
            seed=seed,
        )
        shared_blocks_by_repetition.append(shared_blocks)
        turn1 = {
            "timestamp": repetition * gap_ms * 2,
            "input_length": context_tokens,
            "output_length": output_tokens,
            "hash_ids": turn1_hashes,
        }
        turn2 = {
            "timestamp": repetition * gap_ms * 2 + gap_ms,
            "input_length": context_tokens + resume_tail_tokens,
            "output_length": output_tokens,
            "hash_ids": turn2_hashes,
        }
        turn1_rows.append({**turn1, "timestamp": repetition * gap_ms})
        turn2_rows.append({**turn2, "timestamp": repetition * gap_ms})
        combined_rows.extend((turn1, turn2))

        namespace = f"{model.id}/{context_tokens:07d}/rep-{repetition + 1:03d}"
        storage_ops.extend(
            (
                {
                    "phase": "turn1_store",
                    "repetition": repetition + 1,
                    "namespace": namespace,
                    "operation": "write",
                    "logical_tokens": context_tokens,
                    "bytes": context_tokens * bytes_per_token,
                    "physical_page_tokens": physical_page_tokens,
                    "physical_page_bytes": physical_page_bytes,
                    "physical_page_count": math.ceil(context_tokens / physical_page_tokens),
                    "access_order": "engine_defined",
                },
                {
                    "phase": "turn2_restore",
                    "repetition": repetition + 1,
                    "namespace": namespace,
                    "operation": "read",
                    "logical_tokens": shared_blocks * logical_block_tokens,
                    "bytes": shared_blocks * logical_block_tokens * bytes_per_token,
                    "physical_page_tokens": physical_page_tokens,
                    "physical_page_bytes": physical_page_bytes,
                    "physical_page_count": math.ceil(
                        shared_blocks * logical_block_tokens / physical_page_tokens
                    ),
                    "access_order": "engine_defined",
                    "coverage": (
                        "full shared prefix upper bound; actual sub-linear page selection "
                        "must come from the serving engine"
                    ),
                },
            )
        )

    expected_shared_tokens = shared_blocks_by_repetition[0] * logical_block_tokens
    expected_turn2_reuse = expected_shared_tokens / (context_tokens + resume_tail_tokens)
    reference = model.references.get(context_tokens)
    manifest = {
        "schema_version": 1,
        "case_id": f"{model.id}-{context_tokens}",
        "model": {
            "id": model.id,
            "published_model": model.published_model,
            "kv_model_id": model.kv_model_id,
            "max_context_tokens": model.max_context_tokens,
        },
        "workload": {
            "context_tokens": context_tokens,
            "resume_tail_tokens": resume_tail_tokens,
            "turn2_input_tokens": context_tokens + resume_tail_tokens,
            "output_tokens": output_tokens,
            "repetitions": repetitions,
            "recommended_turn2_delay_ms": gap_ms,
            "expected_shared_prefix_tokens": expected_shared_tokens,
            "expected_turn2_prefix_reuse_fraction": round(expected_turn2_reuse, 6),
            "turn_semantics": {
                "turn1": "prefill context and persist its KV state",
                "between_turns": (
                    "evict or bypass GPU/host KV while preserving the external KV namespace"
                ),
                "turn2": "resume the same context with a small unique tail and measure TTFT",
            },
        },
        "trace_contract": {
            "schema": "mooncake_trace",
            "timestamp_unit": "milliseconds",
            "logical_block_tokens": logical_block_tokens,
            "hash_ids_rule": "len(hash_ids) == ceil(input_length / logical_block_tokens)",
            "files": {
                "combined": "trace.jsonl",
                "turn1": "turn1_warm.jsonl",
                "turn2": "turn2_resume.jsonl",
                "storage_summary": "kv_io_summary.jsonl",
            },
        },
        "kv_geometry": {
            "precision": "bf16_fp16",
            "weight_quantization_is_not_kv_quantization": True,
            "bytes_per_token": bytes_per_token,
            "logical_block_bytes": bytes_per_token * logical_block_tokens,
            "physical_page_tokens": physical_page_tokens,
            "physical_page_bytes": physical_page_bytes,
            "full_context_gib": round(context_tokens * bytes_per_token / BYTES_PER_GIB, 6),
            "full_shared_prefix_read_gib_upper_bound": round(
                expected_shared_tokens * bytes_per_token / BYTES_PER_GIB,
                6,
            ),
            "warning": (
                "Tracerator hash blocks are logical prompt-prefix identifiers, not physical "
                "storage pages. The serving engine remains authoritative for I/O granularity."
            ),
        },
        "published_reference": _reference_dict(reference),
        "acceptance": {
            "required_functional_checks": [
                "all trace rows satisfy the Mooncake/AIPerf hash length rule",
                "Turn 2 reports a prefix hit for the expected shared-prefix tokens",
                "GPU and host KV caches are evicted or bypassed before Turn 2",
                "the external KV store records reads during Turn 2",
                "the cache-regeneration and remote-KV runs use identical model and trace inputs",
            ],
            "recommended_regression_gate": {
                "metric": "Turn 2 TTFT p95",
                "comparison": "approved golden result for identical hardware/software",
                "maximum_regression_percent": 10,
            },
            "published_numbers_are_reference_only": True,
        },
        "seed": seed,
        "sources": {
            "lightinferra_blog": SOURCE_BLOG_URL,
            "model_config": kv["source"],
        },
    }
    manifest["storage_operations"] = storage_ops
    return turn1_rows, turn2_rows, combined_rows, manifest


def write_bundle(
    output_dir: Path | str,
    *,
    suite: str = "headline",
    model_ids: Sequence[str] | None = None,
    repetitions: int = 1,
    logical_block_tokens: int = DEFAULT_LOGICAL_BLOCK_TOKENS,
    physical_page_tokens: int = DEFAULT_PHYSICAL_PAGE_TOKENS,
    resume_tail_tokens: int = DEFAULT_RESUME_TAIL_TOKENS,
    output_tokens: int = DEFAULT_OUTPUT_TOKENS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty output directory: {root}")
    root.mkdir(parents=True, exist_ok=True)

    selected_ids = list(model_ids or GOLDEN_MODELS)
    unknown = [model_id for model_id in selected_ids if model_id not in GOLDEN_MODELS]
    if unknown:
        raise ValueError(f"Unknown model ids: {', '.join(unknown)}")

    case_summaries: list[dict[str, Any]] = []
    for model_id in selected_ids:
        model = GOLDEN_MODELS[model_id]
        for context_tokens in _model_contexts(model, suite):
            case_dir = root / model.id / f"{context_tokens:07d}"
            case_dir.mkdir(parents=True, exist_ok=False)
            turn1, turn2, combined, manifest = build_case(
                model=model,
                context_tokens=context_tokens,
                repetitions=repetitions,
                logical_block_tokens=logical_block_tokens,
                physical_page_tokens=physical_page_tokens,
                resume_tail_tokens=resume_tail_tokens,
                output_tokens=output_tokens,
                seed=seed,
            )
            _write_jsonl(case_dir / "turn1_warm.jsonl", turn1)
            _write_jsonl(case_dir / "turn2_resume.jsonl", turn2)
            _write_jsonl(case_dir / "trace.jsonl", combined)
            _write_jsonl(case_dir / "kv_io_summary.jsonl", manifest.pop("storage_operations"))
            (case_dir / "case_manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            case_summaries.append(
                {
                    "case_id": manifest["case_id"],
                    "path": str(case_dir.relative_to(root)),
                    "model": model.id,
                    "context_tokens": context_tokens,
                    "reference_point": manifest["published_reference"] is not None,
                    "expected_turn2_prefix_reuse_fraction": manifest["workload"][
                        "expected_turn2_prefix_reuse_fraction"
                    ],
                    "full_context_gib": manifest["kv_geometry"]["full_context_gib"],
                }
            )

    bundle_manifest = {
        "schema_version": 1,
        "generator": "tracerator-lightinferra-golden",
        "suite": suite,
        "case_count": len(case_summaries),
        "models": selected_ids,
        "repetitions": repetitions,
        "logical_block_tokens": logical_block_tokens,
        "physical_page_tokens": physical_page_tokens,
        "resume_tail_tokens": resume_tail_tokens,
        "output_tokens": output_tokens,
        "seed": seed,
        "cases": case_summaries,
        "methodology": {
            "observable_equivalence": (
                "Turn 1 long-context prefill followed by Turn 2 resume from an externally "
                "persisted KV prefix"
            ),
            "not_reproduced": [
                "the cited methodology's page-selection logic",
                "ScaleFlux computational-storage behavior",
                "the original FarmGPU hardware/software environment",
            ],
            "comparison_arms": [
                "cache_regen: external KV disabled and Turn 2 recomputes the prefix",
                "remote_kv: external KV enabled on a POSIX mount or S3 namespace",
            ],
        },
        "sources": {
            "lightinferra_blog": SOURCE_BLOG_URL,
        },
    }
    (root / "golden_manifest.json").write_text(
        json.dumps(bundle_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(_bundle_readme(bundle_manifest), encoding="utf-8")
    return bundle_manifest


def _bundle_readme(manifest: Mapping[str, Any]) -> str:
    return f"""# Tracerator Turn-1 warm / Turn-2 resume traces

This `{manifest['suite']}` bundle contains {manifest['case_count']} deterministic
Turn-1/Turn-2 cases. Run every case twice against the same inference stack:

1. `cache_regen`: disable the external KV tier.
2. `remote_kv`: enable the external KV tier on a POSIX mount or S3 namespace.

For each case, `trace.jsonl` is the combined AIPerf/Mooncake trace. The separate
`turn1_warm.jsonl` and `turn2_resume.jsonl` files are for a phase-aware runner that
can explicitly evict GPU/host KV between turns while retaining the external KV
namespace. `kv_io_summary.jsonl` is an upper-bound semantic I/O description; it is
not a claim about the cited methodology's page-selection algorithm.

Use `aiperf analyze-trace` first, then profile with the same model, tokenizer,
fixed schedule, concurrency, and software versions for both comparison arms.
Profile with `--export-level records`, then use
`scripts/compare_lightinferra_golden.py` to isolate the odd-numbered Turn-2
requests; run-level aggregate TTFT mixes Turn 1 and Turn 2.
Published numbers in each case manifest are external reference points, not
automatic pass/fail thresholds. The methodology citation is stored on the source.
"""


def validate_bundle(root: Path | str) -> dict[str, int]:
    bundle_root = Path(root)
    bundle = json.loads((bundle_root / "golden_manifest.json").read_text())
    row_count = 0
    for case in bundle["cases"]:
        case_dir = bundle_root / case["path"]
        manifest = json.loads((case_dir / "case_manifest.json").read_text())
        block_tokens = int(manifest["trace_contract"]["logical_block_tokens"])
        rows = [json.loads(line) for line in (case_dir / "trace.jsonl").read_text().splitlines()]
        if len(rows) != int(manifest["workload"]["repetitions"]) * 2:
            raise ValueError(f"Unexpected row count in {case_dir}")
        for row in rows:
            needed = max(1, math.ceil(int(row["input_length"]) / block_tokens))
            if len(row.get("hash_ids") or []) != needed:
                raise ValueError(f"hash_ids integrity failure in {case_dir}")
            row_count += 1
        for offset in range(0, len(rows), 2):
            turn1 = rows[offset]
            turn2 = rows[offset + 1]
            expected_shared = int(manifest["workload"]["expected_shared_prefix_tokens"]) // block_tokens
            if turn1["hash_ids"][:expected_shared] != turn2["hash_ids"][:expected_shared]:
                raise ValueError(f"Turn-1/Turn-2 prefix mismatch in {case_dir}")
    return {"cases": len(bundle["cases"]), "rows": row_count}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--suite", choices=("headline", "curve"), default="headline")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=tuple(GOLDEN_MODELS),
        help="default: all published model profiles",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--logical-block-tokens", type=int, default=DEFAULT_LOGICAL_BLOCK_TOKENS)
    parser.add_argument("--physical-page-tokens", type=int, default=DEFAULT_PHYSICAL_PAGE_TOKENS)
    parser.add_argument("--resume-tail-tokens", type=int, default=DEFAULT_RESUME_TAIL_TOKENS)
    parser.add_argument("--output-tokens", type=int, default=DEFAULT_OUTPUT_TOKENS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.validate_only:
        manifest = write_bundle(
            args.output_dir,
            suite=args.suite,
            model_ids=args.models,
            repetitions=args.repetitions,
            logical_block_tokens=args.logical_block_tokens,
            physical_page_tokens=args.physical_page_tokens,
            resume_tail_tokens=args.resume_tail_tokens,
            output_tokens=args.output_tokens,
            seed=args.seed,
        )
        print(f"Wrote {manifest['case_count']} cases to {Path(args.output_dir).resolve()}")
    result = validate_bundle(args.output_dir)
    print(f"Validated {result['cases']} cases and {result['rows']} trace rows")


if __name__ == "__main__":
    main()
