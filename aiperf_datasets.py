"""AIPerf custom dataset styles and converters for Tracerator traces."""

from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


AIPERF_CUSTOM_DATASET_STYLES: Dict[str, Dict[str, Any]] = OrderedDict(
    [
        (
            "mooncake_trace",
            {
                "id": "mooncake_trace",
                "label": "Mooncake trace replay",
                "version": "trace_replay",
                "best_for": "Full trace timing, ISL/OSL fidelity, and KV prefix-cache replay.",
                "description": (
                    "Tracerator's native format. Every row carries timestamp, input_length, "
                    "output_length, and block hash_ids so AIPerf can analyze the trace and "
                    "replay realistic prefix reuse."
                ),
                "timing_control": True,
                "multi_turn": True,
                "random_sampling": False,
                "supports_analyze_trace": True,
                "profile_args": {"use_server_token_count": True},
            },
        ),
        (
            "single_turn",
            {
                "id": "single_turn",
                "label": "Single turn",
                "version": "custom_dataset_single_turn",
                "best_for": "Deterministic independent prompts in file order.",
                "description": (
                    "Each JSONL line is one independent request with text, optional "
                    "output_length, and optional extra request parameters. Use this for "
                    "debugging, regression checks, and predictable prompt replay."
                ),
                "timing_control": True,
                "multi_turn": False,
                "random_sampling": False,
                "supports_analyze_trace": False,
                "profile_args": {"request_count": True, "use_server_token_count": False},
            },
        ),
        (
            "multi_turn",
            {
                "id": "multi_turn",
                "label": "Multi turn",
                "version": "custom_dataset_multi_turn",
                "best_for": "Conversations where later turns build on earlier context.",
                "description": (
                    "Each JSONL entry represents a session with a turns array. AIPerf runs "
                    "turns sequentially inside each conversation while multiple "
                    "conversations can run concurrently."
                ),
                "timing_control": True,
                "multi_turn": True,
                "random_sampling": False,
                "supports_analyze_trace": False,
                "profile_args": {"request_count": True, "use_server_token_count": False},
            },
        ),
        (
            "random_pool",
            {
                "id": "random_pool",
                "label": "Random pool",
                "version": "custom_dataset_random_pool",
                "best_for": "Load tests with varied prompts sampled with replacement.",
                "description": (
                    "AIPerf samples prompts from one JSONL file or a directory of JSONL "
                    "pools. This intentionally trades exact trace timing for varied, "
                    "production-like request mix during stress tests."
                ),
                "timing_control": False,
                "multi_turn": False,
                "random_sampling": True,
                "supports_analyze_trace": False,
                "profile_args": {"num_conversations": True, "use_server_token_count": False},
            },
        ),
    ]
)


def _public_dataset(
    dataset_id: str,
    label: str,
    family: str,
    modality: str,
    description: str,
    *,
    best_for: str,
    docs_slug: Optional[str] = None,
    hf_dataset: Optional[str] = None,
    hf_subset: Optional[str] = None,
    hf_split: Optional[str] = None,
    requires_hf_token: bool = False,
    streaming: bool = False,
    category: Optional[str] = None,
    endpoint_type: str = "chat",
) -> tuple[str, Dict[str, Any]]:
    docs_url = None
    if docs_slug:
        docs_url = f"https://docs.nvidia.com/aiperf/tutorials/datasets-inputs/{docs_slug}"
    return dataset_id, {
        "id": dataset_id,
        "label": label,
        "family": family,
        "modality": modality,
        "endpoint_type": endpoint_type,
        "best_for": best_for,
        "description": description,
        "docs_url": docs_url,
        "hf_dataset": hf_dataset,
        "hf_subset": hf_subset,
        "hf_split": hf_split,
        "requires_hf_token": requires_hf_token,
        "streaming_dataset": streaming,
        "category": category,
        "profile_args": {
            "public_dataset": dataset_id,
            "request_count": True,
            "fixed_schedule": False,
        },
    }


_SPEED_CATEGORIES = [
    ("coding", "Code generation and programming tasks."),
    ("humanities", "History, philosophy, and liberal arts prompts."),
    ("math", "Mathematical reasoning and computation prompts."),
    ("multilingual", "Tasks across 23 languages."),
    ("qa", "Question-answering prompts."),
    ("rag", "Retrieval-augmented generation prompts."),
    ("reasoning", "Logical and analytical reasoning prompts."),
    ("roleplay", "Creative roleplay and dialogue prompts."),
    ("stem", "Science, technology, and engineering prompts."),
    ("summarization", "Text summarization prompts."),
    ("writing", "Creative and technical writing prompts."),
]
_SPEED_LENGTHS = ["1k", "2k", "8k", "16k", "32k"]
_SPEED_ENTROPY = [
    ("low_entropy", "Code and sorting tasks."),
    ("mixed", "Needle-in-a-haystack and exam-style prompts."),
    ("high_entropy", "Creative writing and dialogue prompts."),
]


AIPERF_PUBLIC_DATASETS: Dict[str, Dict[str, Any]] = OrderedDict(
    [
        _public_dataset(
            "sharegpt",
            "ShareGPT",
            "ShareGPT",
            "text",
            (
                "Conversation-derived Hugging Face dataset. AIPerf extracts the first "
                "prompt/completion pair from each conversation as single-turn requests."
            ),
            best_for="General chat workloads with realistic user prompt shapes.",
            docs_slug="profile-with-sharegpt-dataset",
        ),
        _public_dataset(
            "instruct_coder",
            "InstructCoder",
            "InstructCoder",
            "text",
            "Code instruction dataset from Hugging Face with single-turn code generation prompts.",
            best_for="Code-generation throughput and latency checks.",
            docs_slug="profile-with-instructcoder-dataset",
            hf_dataset="likaixin/InstructCoder",
            hf_split="train",
        ),
        _public_dataset(
            "aimo",
            "AIMO NuminaMath-TIR",
            "AIMO",
            "text",
            "AI-MO NuminaMath-TIR competition math problems loaded as single-turn requests.",
            best_for="Math-reasoning workloads with longer deliberative outputs.",
            docs_slug="profile-with-aimo-dataset",
            hf_dataset="AI-MO/NuminaMath-TIR",
            hf_split="train",
        ),
        _public_dataset(
            "aimo_aime",
            "AIMO AIME validation",
            "AIMO",
            "text",
            "AIME competition math validation problems from AI-MO.",
            best_for="Compact math competition validation runs.",
            docs_slug="profile-with-aimo-dataset",
            hf_dataset="AI-MO/aimo-validation-aime",
            hf_split="train",
        ),
        _public_dataset(
            "aimo_numina_cot",
            "AIMO NuminaMath CoT",
            "AIMO",
            "text",
            "NuminaMath chain-of-thought math reasoning prompts from AI-MO.",
            best_for="Reasoning-heavy math prompts with chain-of-thought style data.",
            docs_slug="profile-with-aimo-dataset",
            hf_dataset="AI-MO/NuminaMath-CoT",
            hf_split="train",
        ),
        _public_dataset(
            "aimo_numina_1_5",
            "AIMO NuminaMath 1.5",
            "AIMO",
            "text",
            "NuminaMath 1.5 math reasoning prompts from AI-MO.",
            best_for="Math-reasoning replay against the newer NuminaMath 1.5 corpus.",
            docs_slug="profile-with-aimo-dataset",
            hf_dataset="AI-MO/NuminaMath-1.5",
            hf_split="train",
        ),
        _public_dataset(
            "mmstar",
            "MMStar",
            "MMStar",
            "image",
            "Multimodal visual QA benchmark with image-backed questions.",
            best_for="Vision QA model profiling with image plus text inputs.",
            docs_slug="profile-with-mmstar-dataset",
            hf_dataset="Lin-Chen/MMStar",
            hf_split="val",
        ),
        _public_dataset(
            "mmvu",
            "MMVU",
            "MMVU",
            "video",
            "Expert-level video understanding benchmark with video URLs and multiple-choice prompts.",
            best_for="Video-language model profiling.",
            docs_slug="profile-with-mmvu-dataset",
            hf_dataset="yale-nlp/MMVU",
            hf_split="validation",
        ),
        _public_dataset(
            "llava_onevision",
            "LLaVA-OneVision",
            "LLaVA-OneVision",
            "image",
            "Multimodal instruction-following data with first user message and image inputs.",
            best_for="Vision instruction-following models.",
            docs_slug="profile-with-llava-onevision-dataset",
            hf_dataset="lmms-lab/LLaVA-OneVision-Data",
            hf_subset="sharegpt4o",
            hf_split="train",
            streaming=True,
        ),
        _public_dataset(
            "vision_arena",
            "VisionArena",
            "VisionArena",
            "image",
            (
                "Real-world vision-language conversations from Chatbot Arena with "
                "user images and questions."
            ),
            best_for="OpenAI-compatible vision model profiling on real image-question traffic.",
            docs_slug="profile-with-vision-arena-dataset",
            hf_dataset="lmarena-ai/VisionArena-Chat",
            hf_split="train",
            requires_hf_token=True,
            streaming=True,
        ),
        _public_dataset(
            "blazedit_5k",
            "Blazedit 5K",
            "Blazedit",
            "text",
            "Edit-distance code benchmark with roughly 5K-character code contexts.",
            best_for="Code-editing workloads with medium contexts.",
            docs_slug="profile-with-blazedit-dataset",
            hf_dataset="vdaita/edit_5k_char",
            hf_split="train",
        ),
        _public_dataset(
            "blazedit_10k",
            "Blazedit 10K",
            "Blazedit",
            "text",
            "Edit-distance code benchmark with roughly 10K-character code contexts.",
            best_for="Code-editing workloads with longer contexts.",
            docs_slug="profile-with-blazedit-dataset",
            hf_dataset="vdaita/edit_10k_char",
            hf_split="train",
        ),
        _public_dataset(
            "spec_bench",
            "SpecBench",
            "SpecBench",
            "text",
            "Speculative decoding benchmark loaded from the Spec-Bench question set.",
            best_for="Speculative decoding comparisons.",
            docs_slug="profile-with-specbench-dataset",
        ),
        _public_dataset(
            "speed_bench_qualitative",
            "SPEED-Bench qualitative",
            "SPEED-Bench",
            "text",
            "SPEED-Bench qualitative split with 880 prompts across 11 semantic domains.",
            best_for="Speculative decoding acceptance-rate profiling across categories.",
            docs_slug="profile-with-speed-bench-dataset",
            hf_dataset="nvidia/SPEED-Bench",
            hf_subset="qualitative",
            hf_split="test",
        ),
    ]
)

for _category, _description in _SPEED_CATEGORIES:
    AIPERF_PUBLIC_DATASETS[f"speed_bench_{_category}"] = _public_dataset(
        f"speed_bench_{_category}",
        f"SPEED-Bench {_category.replace('_', ' ').title()}",
        "SPEED-Bench",
        "text",
        f"SPEED-Bench {_category.replace('_', ' ')} category with 80 prompts. {_description}",
        best_for=f"Focused speculative decoding runs for {_category.replace('_', ' ')} tasks.",
        docs_slug="profile-with-speed-bench-dataset",
        hf_dataset="nvidia/SPEED-Bench",
        hf_subset="qualitative",
        hf_split="test",
        category=_category,
    )[1]

for _length in _SPEED_LENGTHS:
    AIPERF_PUBLIC_DATASETS[f"speed_bench_throughput_{_length}"] = _public_dataset(
        f"speed_bench_throughput_{_length}",
        f"SPEED-Bench throughput {_length.upper()}",
        "SPEED-Bench",
        "text",
        (
            f"SPEED-Bench throughput split at approximately {_length.upper()} input "
            "tokens across low, mixed, and high entropy tiers."
        ),
        best_for=f"Throughput profiling near {_length.upper()} input sequence length.",
        docs_slug="profile-with-speed-bench-dataset",
        hf_dataset="nvidia/SPEED-Bench",
        hf_subset=f"throughput_{_length}",
        hf_split="test",
    )[1]
    for _entropy, _entropy_description in _SPEED_ENTROPY:
        AIPERF_PUBLIC_DATASETS[f"speed_bench_throughput_{_length}_{_entropy}"] = _public_dataset(
            f"speed_bench_throughput_{_length}_{_entropy}",
            f"SPEED-Bench {_length.upper()} {_entropy.replace('_', ' ').title()}",
            "SPEED-Bench",
            "text",
            (
                f"SPEED-Bench throughput split near {_length.upper()} input tokens, "
                f"{_entropy.replace('_', ' ')} tier. {_entropy_description}"
            ),
            best_for=f"Throughput profiling near {_length.upper()} for {_entropy.replace('_', ' ')} prompts.",
            docs_slug="profile-with-speed-bench-dataset",
            hf_dataset="nvidia/SPEED-Bench",
            hf_subset=f"throughput_{_length}",
            hf_split="test",
            category=_entropy,
        )[1]

for _id, _label, _description, _subset, _requires_auth in [
    (
        "librispeech",
        "LibriSpeech",
        "Clean read-speech clips from audiobooks, using the test.clean split.",
        "clean",
        False,
    ),
    (
        "voxpopuli",
        "VoxPopuli",
        "English European Parliament speech data for ASR profiling.",
        "en",
        False,
    ),
    (
        "gigaspeech",
        "GigaSpeech",
        "Large-scale multi-domain speech corpus from audiobooks, podcasts, and YouTube.",
        "xs",
        True,
    ),
    (
        "ami",
        "AMI Meeting Corpus",
        "Meeting recordings with individual headset microphone audio.",
        "ihm",
        False,
    ),
    (
        "spgispeech",
        "SPGISpeech",
        "Financial earnings-call recordings; requires Hugging Face authentication and terms acceptance.",
        "test",
        True,
    ),
]:
    AIPERF_PUBLIC_DATASETS[_id] = _public_dataset(
        _id,
        _label,
        "ASR",
        "audio",
        _description,
        best_for="Speech recognition benchmarking with public audio corpora.",
        hf_dataset={
            "librispeech": "openslr/librispeech_asr",
            "voxpopuli": "facebook/voxpopuli",
            "gigaspeech": "speechcolab/gigaspeech",
            "ami": "edinburghcstr/ami",
            "spgispeech": "kensho/spgispeech",
        }[_id],
        hf_subset=_subset,
        hf_split="test",
        requires_hf_token=_requires_auth,
        streaming=True,
    )[1]

_PUBLIC_DATASET_ALIASES = {
    "speed_bench": "speed_bench_qualitative",
    "speedbench": "speed_bench_qualitative",
    "visionarena": "vision_arena",
    "llava": "llava_onevision",
    "llava_one_vision": "llava_onevision",
    "blazedit": "blazedit_5k",
    "specbench": "spec_bench",
    "instructcoder": "instruct_coder",
    "libri_speech": "librispeech",
    "spgi_speech": "spgispeech",
}


def list_aiperf_dataset_styles() -> List[Dict[str, Any]]:
    return [dict(style) for style in AIPERF_CUSTOM_DATASET_STYLES.values()]


def list_aiperf_public_datasets() -> List[Dict[str, Any]]:
    return [dict(dataset) for dataset in AIPERF_PUBLIC_DATASETS.values()]


def normalize_aiperf_dataset_style(value: Any) -> str:
    text = str(value or "mooncake_trace").strip().lower().replace("-", "_")
    aliases = {
        "mooncake": "mooncake_trace",
        "trace": "mooncake_trace",
        "single": "single_turn",
        "singleturn": "single_turn",
        "multi": "multi_turn",
        "multiturn": "multi_turn",
        "pool": "random_pool",
        "random": "random_pool",
    }
    text = aliases.get(text, text)
    if text not in AIPERF_CUSTOM_DATASET_STYLES:
        return "mooncake_trace"
    return text


def dataset_style_manifest(style: Any) -> Dict[str, Any]:
    key = normalize_aiperf_dataset_style(style)
    return dict(AIPERF_CUSTOM_DATASET_STYLES[key])


def normalize_aiperf_public_dataset(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower().replace("-", "_")
    if not text:
        return None
    text = _PUBLIC_DATASET_ALIASES.get(text, text)
    if text not in AIPERF_PUBLIC_DATASETS:
        return None
    return text


def public_dataset_manifest(dataset: Any) -> Optional[Dict[str, Any]]:
    key = normalize_aiperf_public_dataset(dataset)
    if not key:
        return None
    return dict(AIPERF_PUBLIC_DATASETS[key])


def _compact_text(label: str, input_length: int, index: int) -> str:
    """Create deterministic text for AIPerf custom datasets without giant files."""
    target = max(1, int(input_length or 0))
    approx_words = min(384, max(8, math.ceil(target / 4)))
    seed_words = [
        "enterprise",
        "trace",
        "prefix",
        "cache",
        "latency",
        "context",
        "request",
        "token",
    ]
    body = " ".join(seed_words[i % len(seed_words)] for i in range(approx_words))
    return f"{label} request {index}. Target input tokens approximately {target}. {body}"


def _record_extra(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    extra = dict(row.get("extra") or {}) if isinstance(row.get("extra"), dict) else {}
    extra.setdefault("tracerator_request_index", index)
    extra.setdefault("tracerator_target_input_length", int(row.get("input_length") or 0))
    hashes = row.get("hash_ids")
    if isinstance(hashes, list):
        extra.setdefault("tracerator_hash_block_count", len(hashes))
    return extra


def _single_turn_record(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    out = {
        "text": row.get("text") or _compact_text("Single-turn", int(row.get("input_length") or 0), index),
        "output_length": int(row.get("output_length") or 1),
        "extra": _record_extra(row, index),
    }
    if row.get("timestamp") is not None:
        out["timestamp"] = int(row.get("timestamp") or 0)
    if row.get("delay") is not None:
        out["delay"] = row.get("delay")
    return out


def _turn_record(row: Dict[str, Any], index: int, previous_timestamp: Any = None) -> Dict[str, Any]:
    out = _single_turn_record(row, index)
    timestamp = row.get("timestamp")
    if timestamp is not None:
        current = int(timestamp or 0)
        if previous_timestamp is None:
            out["timestamp"] = current
        else:
            out.pop("timestamp", None)
            out["delay"] = max(0, current - int(previous_timestamp or 0))
    elif previous_timestamp is None:
        out["delay"] = int(row.get("delay") or 0)
    return out


def _session_key(row: Dict[str, Any], index: int) -> str:
    for key in ("session_id", "trace_id", "conversation_id"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    hashes = row.get("hash_ids")
    if isinstance(hashes, list) and hashes:
        prefix = "-".join(str(v) for v in hashes[:3])
        return f"prefix-{prefix}"
    return f"session-{index // 3}"


def convert_mooncake_records(records: Sequence[Dict[str, Any]], style: Any) -> List[Dict[str, Any]]:
    key = normalize_aiperf_dataset_style(style)
    if key == "mooncake_trace":
        return [dict(row) for row in records]
    if key in ("single_turn", "random_pool"):
        return [_single_turn_record(dict(row), idx) for idx, row in enumerate(records)]
    if key == "multi_turn":
        groups: "OrderedDict[str, List[tuple[int, Dict[str, Any]]]]" = OrderedDict()
        for idx, row in enumerate(records):
            groups.setdefault(_session_key(dict(row), idx), []).append((idx, dict(row)))
        conversations: List[Dict[str, Any]] = []
        for session_id, rows in groups.items():
            rows.sort(key=lambda item: (int(item[1].get("timestamp") or 0), item[0]))
            first_timestamp = int(rows[0][1].get("timestamp") or 0) if rows else 0
            turns = []
            previous_timestamp = None
            for idx, row in rows:
                turns.append(_turn_record(row, idx, previous_timestamp))
                if row.get("timestamp") is not None:
                    previous_timestamp = int(row.get("timestamp") or 0)
            conversations.append({
                "session_id": session_id,
                "timestamp": first_timestamp,
                "turns": turns,
            })
        conversations.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("session_id") or "")))
        return conversations
    return [dict(row) for row in records]


def write_aiperf_dataset(
    path: Path,
    records: Sequence[Dict[str, Any]],
    style: Any,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    converted = convert_mooncake_records(records, style)
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in converted) + ("\n" if converted else ""),
        encoding="utf-8",
    )
    return path


def dataset_file_name(style: Any) -> str:
    key = normalize_aiperf_dataset_style(style)
    safe = re.sub(r"[^a-z0-9_]+", "_", key)
    return f"aiperf_{safe}.jsonl"
