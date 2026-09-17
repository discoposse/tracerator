#!/usr/bin/env python3
"""Local baseline trace discovery and adapters for Tracerator."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
DOCS = ROOT.parent
BLOCK_SIZE = 512

MOONCAKE_DIR = ROOT / "Mooncake" / "traces"


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


RAGPULSE_DIR = _first_existing(
    Path(os.environ.get("TRACERATOR_RAGPULSE_DIR", "/external/RAGPulse")),
    DOCS / "RAGPulse",
)
CC_WEKA_DIR = _first_existing(
    Path(os.environ.get("TRACERATOR_CC_WEKA_DIR", "/external/cc-traces-weka-with-subagents-051826")),
    DOCS / "cc-traces-weka-with-subagents-051826",
)
CODEX_SWEBENCHPRO_DIR = _first_existing(
    Path(os.environ.get("TRACERATOR_CODEX_SWEBENCHPRO_DIR", "/external/codex_swebenchpro_traces")),
    DOCS / "codex_swebenchpro_traces",
)
AIPERF_TOOLKIT = _first_existing(
    Path(os.environ.get("TRACERATOR_AIPERF_TOOLKIT_DIR", "/external/aiperf-toolkit")),
    DOCS / "aiperf-toolkit",
)
SAGESERVE_DIR = _first_existing(
    Path(os.environ.get("TRACERATOR_SAGESERVE_DIR", "/external/SageServe")),
    DOCS / "SageServe",
)
RAGPULSE_TRACE = RAGPULSE_DIR / "0_trace.jsonl"
CC_WEKA_TRACE = CC_WEKA_DIR / "traces.jsonl"
CODEX_SWEBENCHPRO_TRACE = CODEX_SWEBENCHPRO_DIR / "codex_swebenchpro.json"
SAGESERVE_ENTERPRISE_TRACE = SAGESERVE_DIR / "traces" / "week-dp.csv"
SAGESERVE_CODE_TRACE = SAGESERVE_DIR / "data" / "code_distributions.csv"
SAGESERVE_CONV_TRACE = SAGESERVE_DIR / "data" / "conv_distributions.csv"
LIGHTINFERRA_GOLDEN_GENERATOR = ROOT / "lightinferra_golden.py"
LIGHTINFERRA_GOLDEN_SOURCE_ID = "lightinferra-golden"
LIGHTINFERRA_GOLDEN_SOURCE_ALIASES = frozenset({"lightinferra-ddn-golden"})


def canonicalize_source_id(source_id: str) -> str:
    value = str(source_id or "").strip()
    if value in LIGHTINFERRA_GOLDEN_SOURCE_ALIASES:
        return LIGHTINFERRA_GOLDEN_SOURCE_ID
    return value


def is_lightinferra_golden_source(source_id: str) -> bool:
    return canonicalize_source_id(source_id) == LIGHTINFERRA_GOLDEN_SOURCE_ID


@dataclass(frozen=True)
class TraceSource:
    id: str
    label: str
    family: str
    path: Path
    mode: str
    description: str
    cache_fidelity: str
    available: bool
    status: str = "ready"
    fetch_command: Optional[str] = None
    source_url: Optional[str] = None
    source_label: Optional[str] = None
    license: Optional[str] = None


def blocks_for_length(length: int, block_size: int = BLOCK_SIZE) -> int:
    return max(1, (max(1, int(length)) + block_size - 1) // block_size)


def _stable_int(value: Any, salt: str = "") -> int:
    raw = f"{salt}:{value}".encode("utf-8", "replace")
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, "big") % 9_000_000_000 + 1_000_000


def _is_lfs_pointer(path: Path) -> bool:
    if not path.exists() or path.stat().st_size > 1024:
        return False
    try:
        return path.read_text(errors="ignore").startswith("version https://git-lfs.github.com/spec/")
    except OSError:
        return False


def discover_trace_sources() -> List[TraceSource]:
    sources = [
        TraceSource(
            id=LIGHTINFERRA_GOLDEN_SOURCE_ID,
            label="LightInferra-style golden",
            family="External KV",
            path=LIGHTINFERRA_GOLDEN_GENERATOR,
            mode="lightinferra_golden",
            description=(
                "Deterministic Turn-1 warm and Turn-2 resume workload for comparing "
                "cache regeneration with an external KV store."
            ),
            cache_fidelity="exact deterministic 512-token shared-prefix blocks",
            available=LIGHTINFERRA_GOLDEN_GENERATOR.exists(),
            status="ready" if LIGHTINFERRA_GOLDEN_GENERATOR.exists() else "generator missing",
            source_url=(
                "https://www.lightbitslabs.com/blog/"
                "introducing-lightinferra-280x-improved-ai-token-economy-by-lightbits-labs/"
            ),
            source_label="LightInferra public benchmark methodology",
        ),
        TraceSource(
            id="conversation",
            label="Mooncake Conversation",
            family="Mooncake",
            path=MOONCAKE_DIR / "conversation_trace.jsonl",
            mode="mooncake",
            description="Real multi-turn Kimi chat traffic with long contexts and prefix reuse.",
            cache_fidelity="block hash_ids",
            available=(MOONCAKE_DIR / "conversation_trace.jsonl").exists(),
            source_url="https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces",
            source_label="Mooncake FAST '25 traces",
            license="Apache-2.0",
        ),
        TraceSource(
            id="toolagent",
            label="Mooncake Tool & Agent",
            family="Mooncake",
            path=MOONCAKE_DIR / "toolagent_trace.jsonl",
            mode="mooncake",
            description="Agent/tool traffic with high shared-prefix reuse and bursty arrivals.",
            cache_fidelity="block hash_ids",
            available=(MOONCAKE_DIR / "toolagent_trace.jsonl").exists(),
            source_url="https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces",
            source_label="Mooncake FAST '25 traces",
            license="Apache-2.0",
        ),
        TraceSource(
            id="synthetic",
            label="Mooncake Synthetic",
            family="Mooncake",
            path=MOONCAKE_DIR / "synthetic_trace.jsonl",
            mode="mooncake",
            description="Long-context public-data baseline with lower sharing.",
            cache_fidelity="block hash_ids",
            available=(MOONCAKE_DIR / "synthetic_trace.jsonl").exists(),
            source_url="https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces",
            source_label="Mooncake FAST '25 traces",
            license="Apache-2.0",
        ),
        TraceSource(
            id="ragpulse",
            label="RAGPulse",
            family="RAGPulse",
            path=RAGPULSE_TRACE,
            mode="ragpulse",
            description="Real RAG workload from campus Q&A; component hashes are expanded to KV blocks.",
            cache_fidelity="component hash_ids expanded to 512-token blocks",
            available=RAGPULSE_TRACE.exists() and not _is_lfs_pointer(RAGPULSE_TRACE),
            source_url="https://github.com/flashserve/RAGPulse",
            source_label="flashserve/RAGPulse",
        ),
        TraceSource(
            id="cc-weka-subagents",
            label="CC Weka With Subagents",
            family="SemiAnalysis CC",
            path=CC_WEKA_TRACE,
            mode="cc_weka",
            description="Agentic coding traces with sub-agent fan-out; requires local Git LFS payload.",
            cache_fidelity="64-token local hash_ids folded to 512-token blocks",
            available=CC_WEKA_TRACE.exists() and not _is_lfs_pointer(CC_WEKA_TRACE),
            status="ready" if CC_WEKA_TRACE.exists() and not _is_lfs_pointer(CC_WEKA_TRACE) else "Git LFS payload missing",
            fetch_command="scripts/fetch-baseline-traces.sh cc-weka-subagents",
            source_url="https://huggingface.co/datasets/semianalysisai/cc-traces-weka-with-subagents-051826",
            source_label="SemiAnalysis CC traces",
            license="Apache-2.0",
        ),
        TraceSource(
            id="codex-swebenchpro",
            label="Codex SWE-bench Pro",
            family="Codex",
            path=CODEX_SWEBENCHPRO_TRACE,
            mode="codex_swebenchpro",
            description="Real SWE-bench Pro agentic workload traces generated by Codex agent.",
            cache_fidelity="pending schema inspection after Git LFS payload is present",
            available=CODEX_SWEBENCHPRO_TRACE.exists() and not _is_lfs_pointer(CODEX_SWEBENCHPRO_TRACE),
            status="ready" if CODEX_SWEBENCHPRO_TRACE.exists() and not _is_lfs_pointer(CODEX_SWEBENCHPRO_TRACE) else "Git LFS payload missing",
            source_label="Local Codex SWE-bench Pro trace",
        ),
        TraceSource(
            id="sageserve-enterprise",
            label="SageServe Enterprise Sydney",
            family="SageServe",
            path=SAGESERVE_ENTERPRISE_TRACE,
            mode="sageserve_request_csv",
            description="Enterprise multi-model prod/dev request trace with SageServe arrival, model, region, and workload metadata.",
            cache_fidelity="deterministic enterprise prefix families expanded to 512-token blocks",
            available=SAGESERVE_ENTERPRISE_TRACE.exists() and not _is_lfs_pointer(SAGESERVE_ENTERPRISE_TRACE),
            status="ready" if SAGESERVE_ENTERPRISE_TRACE.exists() else "not found: SageServe traces/week-dp.csv",
            source_url="https://github.com/shashwatj07/SageServe",
            source_label="shashwatj07/SageServe",
        ),
        TraceSource(
            id="sageserve-code",
            label="SageServe Code Distribution",
            family="SageServe",
            path=SAGESERVE_CODE_TRACE,
            mode="sageserve_distribution_csv",
            description="Timestamped code workload distribution from SageServe with prompt and generated token sizes.",
            cache_fidelity="deterministic code prefix families expanded to 512-token blocks",
            available=SAGESERVE_CODE_TRACE.exists() and not _is_lfs_pointer(SAGESERVE_CODE_TRACE),
            status="ready" if SAGESERVE_CODE_TRACE.exists() else "not found",
            source_url="https://github.com/shashwatj07/SageServe",
            source_label="shashwatj07/SageServe",
        ),
        TraceSource(
            id="sageserve-conversation",
            label="SageServe Conversation Distribution",
            family="SageServe",
            path=SAGESERVE_CONV_TRACE,
            mode="sageserve_distribution_csv",
            description="Timestamped enterprise conversation distribution from SageServe with prompt and generated token sizes.",
            cache_fidelity="deterministic conversation prefix families expanded to 512-token blocks",
            available=SAGESERVE_CONV_TRACE.exists() and not _is_lfs_pointer(SAGESERVE_CONV_TRACE),
            status="ready" if SAGESERVE_CONV_TRACE.exists() else "not found",
            source_url="https://github.com/shashwatj07/SageServe",
            source_label="shashwatj07/SageServe",
        ),
        TraceSource(
            id="azure-conversation",
            label="Azure LLM Conversation",
            family="aiperf-toolkit",
            path=AIPERF_TOOLKIT / "examples" / "trace-replay-elastic-viewer" / "traces" / "AzureLLMInferenceTrace_conv_1week.csv",
            mode="unsupported",
            description="Available in aiperf-toolkit, but no KV hash_ids are present for prefix-cache integrity.",
            cache_fidelity="unavailable",
            available=False,
            status="unsupported: no hash_ids",
            source_url="https://github.com/NVIDIA/aiperf",
            source_label="NVIDIA AIPerf toolkit",
        ),
        TraceSource(
            id="burstgpt",
            label="BurstGPT",
            family="aiperf-toolkit",
            path=AIPERF_TOOLKIT / "examples" / "trace-replay-elastic-viewer" / "traces" / "BurstGPT_without_fails_1.csv",
            mode="unsupported",
            description="Available in aiperf-toolkit, but no KV hash_ids are present for prefix-cache integrity.",
            cache_fidelity="unavailable",
            available=False,
            status="unsupported: no hash_ids",
            source_url="https://github.com/HPMLL/BurstGPT",
            source_label="BurstGPT",
        ),
    ]
    return sources


def source_by_id(source_id: str) -> TraceSource:
    wanted = canonicalize_source_id(source_id)
    for source in discover_trace_sources():
        if source.id == wanted:
            return source
    raise KeyError(f"unknown trace source {source_id!r}")


def sources_for_api() -> List[Dict[str, Any]]:
    out = []
    for source in discover_trace_sources():
        out.append({
            "id": source.id,
            "label": source.label,
            "family": source.family,
            "mode": source.mode,
            "path": str(source.path),
            "description": source.description,
            "cache_fidelity": source.cache_fidelity,
            "available": source.available,
            "status": source.status,
            "fetch_command": source.fetch_command,
            "source_url": source.source_url,
            "source_label": source.source_label,
            "license": source.license,
        })
    return out


def load_source_records(source_id: str, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    source = source_by_id(source_id)
    if not source.available:
        raise ValueError(f"{source.label} is not available: {source.status}")
    if source.mode == "mooncake":
        records = _load_mooncake(source.path, max_records=max_records)
    elif source.mode == "ragpulse":
        records = _load_ragpulse(source.path, max_records=max_records)
    elif source.mode == "cc_weka":
        records = _load_cc_weka(source.path, max_records=max_records)
    elif source.mode == "codex_swebenchpro":
        records = _load_codex_swebenchpro(source.path, max_records=max_records)
    elif source.mode == "sageserve_request_csv":
        records = _load_sageserve_request_csv(source.path, source.id, max_records=max_records)
    elif source.mode == "sageserve_distribution_csv":
        records = _load_sageserve_distribution_csv(source.path, source.id, max_records=max_records)
    elif source.mode == "lightinferra_golden":
        raise ValueError(
            f"{source.label} requires model and context parameters; generate it through "
            "Tracerator's run workflow"
        )
    else:
        raise ValueError(f"{source.label} cannot be used as a generated baseline: {source.status}")
    records.sort(key=lambda r: (r["timestamp"], r["input_length"]))
    return normalize_records(records)


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _parse_wall_clock_timestamp_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(text[:26], fmt).timestamp() * 1000)
        except ValueError:
            continue
    return int(round(float(text) * 1000))


def _relative_ms_from_numeric(value: Any) -> int:
    numeric = float(value or 0)
    # SageServe simulator traces use seconds. If a caller provides an already-ms
    # timestamp, keep it rather than multiplying a large value again.
    if numeric > 10_000_000:
        return int(round(numeric))
    return int(round(numeric * 1000))


def _enterprise_prefix_components(row: Dict[str, Any], source_id: str, index: int) -> List[Any]:
    workload = str(row.get("workload_type") or row.get("workload") or source_id).lower()
    model = str(row.get("model_type") or row.get("model") or "model").lower()
    regions = str(row.get("regions") or row.get("region") or "global").zfill(3)
    tenant = str(row.get("client_tenant") or row.get("tenant") or "tenant")
    scenario = str(row.get("scenario") or source_id)
    request_id = str(row.get("request_id") or index)
    hour_bucket = _relative_ms_from_numeric(row.get("arrival_timestamp", 0)) // 3_600_000
    minute_bucket = _relative_ms_from_numeric(row.get("arrival_timestamp", 0)) // 60_000
    return [
        ("enterprise", source_id),
        ("workload", workload),
        ("model", model),
        ("region-priority", regions[:1]),
        ("scenario", scenario),
        ("tenant", tenant),
        ("hour", hour_bucket),
        ("minute", minute_bucket),
        ("request", request_id),
    ]


def _distribution_prefix_components(source_id: str, timestamp_ms: int, index: int) -> List[Any]:
    family = "code" if "code" in source_id else "conversation"
    return [
        ("enterprise", "sageserve"),
        ("family", family),
        ("hour", timestamp_ms // 3_600_000),
        ("minute", timestamp_ms // 60_000),
        ("burst", timestamp_ms // 1000),
        ("request", index),
    ]


def _load_sageserve_request_csv(path: Path, source_id: str, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            if max_records is not None and len(records) >= max_records:
                break
            input_length = _coerce_int(row.get("prompt_size", row.get("input_length")), 0)
            if input_length <= 0:
                continue
            output_length = max(1, _coerce_int(row.get("token_size", row.get("output_length")), 1))
            timestamp = _relative_ms_from_numeric(row.get("arrival_timestamp", 0))
            needed = blocks_for_length(input_length)
            components = _enterprise_prefix_components(row, source_id, idx)
            records.append({
                "timestamp": timestamp,
                "input_length": input_length,
                "output_length": output_length,
                "hash_ids": _component_hashes_to_blocks(
                    components,
                    needed,
                    source_name=source_id,
                    record_salt=str(row.get("request_id") or idx),
                ),
                "model": row.get("model_type"),
                "workload_type": row.get("workload_type"),
                "regions": row.get("regions"),
            })
    return records


def _load_sageserve_distribution_csv(path: Path, source_id: str, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    first_timestamp: Optional[int] = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            if max_records is not None and len(records) >= max_records:
                break
            input_length = _coerce_int(row.get("ContextTokens", row.get("prompt_size")), 0)
            if input_length <= 0:
                continue
            output_length = max(1, _coerce_int(row.get("GeneratedTokens", row.get("token_size")), 1))
            absolute_ms = _parse_wall_clock_timestamp_ms(row.get("TIMESTAMP", row.get("timestamp", idx)))
            if first_timestamp is None:
                first_timestamp = absolute_ms
            timestamp = max(0, absolute_ms - first_timestamp)
            needed = blocks_for_length(input_length)
            components = _distribution_prefix_components(source_id, timestamp, idx)
            records.append({
                "timestamp": timestamp,
                "input_length": input_length,
                "output_length": output_length,
                "hash_ids": _component_hashes_to_blocks(
                    components,
                    needed,
                    source_name=source_id,
                    record_salt=str(idx),
                ),
                "workload_type": "code" if "code" in source_id else "conversation",
            })
    return records


def _load_mooncake(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records = []
    for idx, row in enumerate(_iter_jsonl(path)):
        if max_records is not None and idx >= max_records:
            break
        records.append({
            "timestamp": int(float(row["timestamp"])),
            "input_length": int(row["input_length"]),
            "output_length": max(1, int(row["output_length"])),
            "hash_ids": [int(v) for v in row.get("hash_ids", [])],
        })
    return records


def _flatten_ragpulse_components(hash_ids: Any) -> List[Any]:
    if isinstance(hash_ids, dict):
        ordered_keys = ["sys_prompt", "passages_ids", "history", "web_search", "user_input"]
        flattened: List[Any] = []
        for key in ordered_keys:
            value = hash_ids.get(key, [])
            if isinstance(value, list):
                flattened.extend((key, item) for item in value)
            elif value:
                flattened.append((key, value))
        for key, value in hash_ids.items():
            if key in ordered_keys:
                continue
            if isinstance(value, list):
                flattened.extend((key, item) for item in value)
            elif value:
                flattened.append((key, value))
        return flattened
    if isinstance(hash_ids, list):
        return list(hash_ids)
    return []


def _component_hashes_to_blocks(components: List[Any], needed_blocks: int, source_name: str, record_salt: str) -> List[int]:
    if not components:
        components = [f"cold:{record_salt}"]
    out: List[int] = []
    for i, component in enumerate(components):
        out.append(_stable_int(component, salt=f"{source_name}:component:{i}:header"))
        if len(out) == needed_blocks:
            return out
    body_index = 0
    while len(out) < needed_blocks:
        for i, component in enumerate(components):
            out.append(_stable_int(component, salt=f"{source_name}:component:{i}:body:{body_index}"))
            if len(out) == needed_blocks:
                return out
        body_index += 1
    return out


def _load_ragpulse(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records = []
    for idx, row in enumerate(_iter_jsonl(path)):
        if max_records is not None and idx >= max_records:
            break
        input_length = int(row["input_length"])
        needed = blocks_for_length(input_length)
        session_id = row.get("session_id", "")
        components = _flatten_ragpulse_components(row.get("hash_ids", []))
        records.append({
            "timestamp": int(round(float(row.get("timestamp", 0)) * 1000)),
            "input_length": input_length,
            "output_length": max(1, int(row.get("output_length", 1))),
            "hash_ids": _component_hashes_to_blocks(
                components,
                needed,
                source_name="ragpulse",
                record_salt=f"{session_id}:{idx}",
            ),
            "session_id": session_id,
        })
    return records


def _fold_64_token_hashes(hash_ids: List[Any], trace_id: str, needed_blocks: int) -> List[int]:
    out = []
    for block_idx in range(needed_blocks):
        start = block_idx * 8
        chunk = tuple(hash_ids[start:start + 8])
        if not chunk:
            chunk = (f"pad:{block_idx}",)
        out.append(_stable_int(chunk, salt=f"cc-weka:{trace_id}:512:{block_idx}"))
    return out


def _append_cc_request(records: List[Dict[str, Any]], req: Dict[str, Any], trace_id: str, trace_offset_ms: int) -> None:
    input_length = int(req.get("in", req.get("input_length", 0)))
    if input_length <= 0:
        return
    needed = blocks_for_length(input_length)
    records.append({
        "timestamp": trace_offset_ms + int(round(float(req.get("t", 0)) * 1000)),
        "input_length": input_length,
        "output_length": max(1, int(req.get("out", req.get("output_length", 1)))),
        "hash_ids": _fold_64_token_hashes(list(req.get("hash_ids", [])), trace_id, needed),
        "model": req.get("model"),
        "trace_id": trace_id,
    })


def _load_cc_weka(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    trace_offset_ms = 0
    for trace_index, trace in enumerate(_iter_jsonl(path)):
        trace_id = str(trace.get("id", trace_index))
        for entry in trace.get("requests", []):
            if max_records is not None and len(records) >= max_records:
                return records
            if entry.get("type") == "subagent":
                for inner in entry.get("requests", []):
                    if max_records is not None and len(records) >= max_records:
                        return records
                    _append_cc_request(records, inner, trace_id, trace_offset_ms)
            else:
                _append_cc_request(records, entry, trace_id, trace_offset_ms)
        if records:
            trace_offset_ms = max(r["timestamp"] for r in records) + 10_000
    return records


def _load_codex_swebenchpro(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Load Codex SWE-bench Pro once its LFS payload is present.

    The local checkout currently contains only a Git LFS pointer, and the README does
    not document the full JSON schema. This loader supports common trace shapes and
    intentionally fails loud if the payload uses a new shape we have not mapped yet.
    """
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        if isinstance(data.get("requests"), list):
            rows = data["requests"]
        elif isinstance(data.get("traces"), list):
            rows = []
            for trace_idx, trace in enumerate(data["traces"]):
                trace_id = str(trace.get("id", trace_idx)) if isinstance(trace, dict) else str(trace_idx)
                for req in (trace.get("requests", []) if isinstance(trace, dict) else []):
                    item = dict(req)
                    item.setdefault("trace_id", trace_id)
                    rows.append(item)
        else:
            raise ValueError("Unsupported Codex SWE-bench Pro JSON object shape")
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("Unsupported Codex SWE-bench Pro JSON shape")

    records: List[Dict[str, Any]] = []
    current_ts = 0
    for idx, row in enumerate(rows):
        if max_records is not None and len(records) >= max_records:
            break
        if not isinstance(row, dict):
            continue
        input_length = row.get("input_length", row.get("input_tokens", row.get("in")))
        output_length = row.get("output_length", row.get("output_tokens", row.get("out", 1)))
        hash_ids = row.get("hash_ids", row.get("hashes", []))
        if input_length is None or not hash_ids:
            raise ValueError("Codex SWE-bench Pro payload lacks input_length/hash_ids fields in a supported shape")
        if "timestamp" in row:
            timestamp = int(round(float(row["timestamp"])))
        elif "t" in row:
            timestamp = int(round(float(row["t"]) * 1000))
        elif "pre_gap" in row:
            current_ts += int(round(float(row["pre_gap"]) * 1000))
            timestamp = current_ts
        else:
            timestamp = idx * 1000
        records.append({
            "timestamp": timestamp,
            "input_length": int(input_length),
            "output_length": max(1, int(output_length)),
            "hash_ids": [int(_stable_int(v, salt="codex-swebenchpro")) if not isinstance(v, int) else v for v in hash_ids],
            "trace_id": row.get("trace_id") or row.get("session_id"),
            "model": row.get("model"),
        })
    return records


def normalize_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    fresh = 10_000_000_000
    for row in records:
        input_length = max(1, int(row["input_length"]))
        needed = blocks_for_length(input_length)
        hashes = [int(v) for v in row.get("hash_ids", [])]
        if len(hashes) > needed:
            hashes = hashes[:needed]
        while len(hashes) < needed:
            hashes.append(fresh)
            fresh += 1
        rec = dict(row)
        rec["timestamp"] = int(row.get("timestamp", 0))
        rec["input_length"] = input_length
        rec["output_length"] = max(1, int(row.get("output_length", 1)))
        rec["hash_ids"] = hashes
        normalized.append(rec)
    return normalized
