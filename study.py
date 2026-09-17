"""Study Summary: pack workspace runs for AI narrative + chat.

API keys are accepted per-request for the browser session and are never persisted.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from inspect_enrich import enrich_run

PROVIDERS = {
    "openai": {
        "id": "openai",
        "label": "OpenAI (ChatGPT)",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
        "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"],
    },
    "xai": {
        "id": "xai",
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-4.5",
        "models": [
            "grok-4.5",
            "grok-build-0.1",
            "grok-4.3",
            "grok-4.20-multi-agent-0309",
            "grok-4.20-0309-reasoning",
            "grok-4.20-0309-non-reasoning",
        ],
    },
}

STUDY_SYSTEM = """You are Tracerator's Study Summary analyst for enterprise KV-cache / prefix-reuse
workload studies (Mooncake-schema traces and other baselines). Write like the INF-Traces poster
and video-trace-shapes case studies: executive highlight first, then measured findings, setup,
workload shape, and honest caveats.

Rules:
- Use only facts present in the provided JSON context. Do not invent numbers.
- Identify each run by its `name` and `generated_from` / `source` fields. Never rename a run's
  baseline (e.g. do not call a CC Weka run a Mooncake Conversation run).
- Prefer `generated_from` and top-level `source` over any nested `narrative` text if they differ.
- Prefer concrete medians/p95, hit ratios, burst, TTFT when replay is present.
- Call out when GPU-local prefix cache would overflow vs when an external shared tier would matter.
- Keep tone precise and technical; avoid marketing fluff.
- When asked to chat, answer from the context pack; say if something is unknown.
"""


def list_providers() -> List[Dict[str, Any]]:
    return [
        {
            "id": cfg["id"],
            "label": cfg["label"],
            "default_model": cfg["default_model"],
            "models": list(cfg["models"]),
        }
        for cfg in PROVIDERS.values()
    ]


def _compact_overview(overview: Dict[str, Any]) -> Dict[str, Any]:
    prefix = overview.get("prefix") or {}
    return {
        "request_count": overview.get("request_count"),
        "rps": overview.get("rps"),
        "max_concurrency": overview.get("max_concurrency"),
        "duration_s": overview.get("duration_s"),
        "isl": overview.get("isl"),
        "osl": overview.get("osl"),
        "prefix_hit_ratio": prefix.get("approx_cache_hit_ratio"),
        "unique_blocks": prefix.get("unique_block_ids"),
        "hot_blocks": (prefix.get("hot_blocks") or [])[:8],
        "integrity_ok": (overview.get("integrity") or {}).get("ok"),
        "summary_line": overview.get("summary_line"),
    }


def _compact_replay(replay: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not replay:
        return None
    summary = replay.get("summary") or {}
    latency = replay.get("latency_ms") or {}
    return {
        "model": (replay.get("run") or {}).get("model"),
        "success_count": summary.get("success_count"),
        "error_count": summary.get("error_count"),
        "request_count": summary.get("request_count"),
        "request_throughput": summary.get("request_throughput"),
        "output_token_throughput": summary.get("output_token_throughput"),
        "ttft_ms": latency.get("time_to_first_token"),
        "request_latency_ms": latency.get("request_latency"),
        "error_types": summary.get("error_types") or replay.get("error_types"),
    }


def build_run_study_slice(
    *,
    run: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a compact, LLM-safe study slice for one workspace run."""
    manifest = run.get("manifest") or {}
    params = run.get("params") or {}
    analysis = run.get("analysis")
    replay = run.get("replay_result")
    enriched = enrich_run(
        records,
        params=params,
        manifest=manifest,
        analysis=analysis,
        replay_result=replay,
    )
    overview = enriched.get("overview") or {}
    prefix = enriched.get("prefix") or {}
    timeline = enriched.get("timeline") or {}
    align = enriched.get("align") or {}
    narrative = enriched.get("narrative") or {}
    workload = enriched.get("workload") or {}

    sessions = prefix.get("sessions") or {}
    base_source = manifest.get("base_source") or {}
    requested_source = params.get("source") or params.get("base")
    generated_id = base_source.get("id")
    generated_label = base_source.get("label") or params.get("source")
    source_mismatch = bool(
        requested_source and generated_id and str(requested_source) != str(generated_id)
    )
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "source": generated_label,
        "source_id": generated_id or requested_source,
        "requested_source": requested_source,
        "generated_from": {
            "id": generated_id,
            "label": generated_label,
            "family": base_source.get("family"),
            "mode": base_source.get("mode"),
        },
        "source_mismatch": source_mismatch,
        "scenario": (manifest.get("trace_scenario") or {}).get("label") or params.get("scenario"),
        "overview": _compact_overview(overview),
        "isl_distribution": workload.get("isl_distribution") or overview.get("isl_distribution"),
        "osl_distribution": workload.get("osl_distribution"),
        "prefix": {
            "hit_ratio": (overview.get("prefix") or {}).get("approx_cache_hit_ratio"),
            "unique_blocks": (overview.get("prefix") or {}).get("unique_block_ids"),
            "hot_blocks": ((overview.get("prefix") or {}).get("hot_blocks") or [])[:10],
            "multi_turn_roots": sessions.get("multi_turn_roots"),
            "distinct_roots": sessions.get("distinct_roots"),
            "top_sessions": (sessions.get("top_sessions") or [])[:8],
        },
        "timeline": {
            "available": timeline.get("available"),
            "max_burst": timeline.get("max_burst"),
            "mean_burst": timeline.get("mean_burst"),
            "points_sample": (timeline.get("points") or [])[:40],
        },
        "replay": _compact_replay(replay),
        "align": {
            "available": align.get("available"),
            "success_count": align.get("success_count"),
            "error_count": align.get("error_count"),
            "isl_mismatch_bins": align.get("isl_mismatch_bins"),
            "ttft_ms": ((align.get("latency_ms") or {}).get("time_to_first_token")),
        },
        # Stats bullets only — omit auto prose so the model cannot latch onto a wrong baseline label.
        "narrative": {
            "bullets": narrative.get("bullets"),
        },
        "viz": {
            "hot_blocks": ((overview.get("prefix") or {}).get("hot_blocks") or [])[:12],
            "top_sessions": (sessions.get("top_sessions") or [])[:12],
            "isl_bins": workload.get("isl_distribution") or {},
        },
    }


def build_study_context(slices: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "study_type": "tracerator_workspace",
        "run_count": len(slices),
        "runs": list(slices),
        "guidance": {
            "audience": "infra / inference platform engineers",
            "style": "INF-Traces poster + video-trace-shapes case study",
            "sections": [
                "highlight",
                "findings",
                "setup",
                "workload_shape",
                "cache_tier_implication",
                "caveats",
            ],
        },
    }


def _chat_completions(
    *,
    provider: str,
    api_key: str,
    model: Optional[str],
    messages: List[Dict[str, str]],
    timeout_sec: float = 90.0,
) -> Dict[str, Any]:
    cfg = PROVIDERS.get((provider or "").strip().lower())
    if not cfg:
        raise ValueError(f"Unknown provider {provider!r}; choose openai or xai")
    key = (api_key or "").strip()
    if not key:
        raise ValueError("API key is required for this session call")
    resolved_model = (model or "").strip() or cfg["default_model"]
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    payload = {
        "model": resolved_model,
        "messages": messages,
        "temperature": 0.35,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"{cfg['label']} API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {cfg['label']} API: {exc.reason}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"{cfg['label']} returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    return {
        "provider": cfg["id"],
        "model": resolved_model,
        "content": content,
        "usage": body.get("usage"),
    }


def summarize_study(
    context: Dict[str, Any],
    *,
    provider: str,
    api_key: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    user_prompt = (
        "Write a Study Summary for the following Tracerator run context.\n"
        "In Setup, name each run using its `name` and `generated_from.label` (or `source`).\n"
        "If `source_mismatch` is true, call that out in Caveats.\n"
        "Return concise Markdown with these headings exactly and no other level-2 headings:\n"
        "## Highlight\n## Findings\n## Setup\n## Workload shape\n"
        "## Cache-tier implication\n## Caveats\n\n"
        "Presentation contract:\n"
        "- Highlight: one decision-oriented paragraph, at most 90 words. Bold only the run name "
        "and the 3-5 numbers that best explain the result.\n"
        "- Findings: 4-7 bullets. Start each with a short bold takeaway label, then the evidence.\n"
        "- Setup: one Markdown table with columns Run, Baseline, Status, Scenario, Replay model. "
        "Follow it with no more than three short bullets for scale, replay alignment, and source identity.\n"
        "- Workload shape: 4-6 bullets covering input, output, reuse, and burstiness. Start each "
        "with a bold label.\n"
        "- Cache-tier implication: 2-4 bullets covering GPU-local cache, shared tier, and prefill/decode "
        "pressure. Start each with a bold label and state the operational implication first.\n"
        "- Caveats: 2-4 short bullets ordered by decision risk.\n"
        "- Use human-friendly values and units. Do not repeat the same fact in every section. "
        "Do not use code fences or HTML.\n\n"
        "Context JSON:\n"
        f"```json\n{json.dumps(context, indent=2)[:120000]}\n```"
    )
    result = _chat_completions(
        provider=provider,
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": STUDY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    )
    return {
        **result,
        "format": "markdown",
    }


def chat_with_study(
    context: Dict[str, Any],
    *,
    provider: str,
    api_key: str,
    model: Optional[str],
    history: Sequence[Dict[str, str]],
    question: str,
) -> Dict[str, Any]:
    q = (question or "").strip()
    if not q:
        raise ValueError("question is required")
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": STUDY_SYSTEM},
        {
            "role": "user",
            "content": (
                "Study context JSON (authoritative facts):\n"
                f"```json\n{json.dumps(context, indent=2)[:100000]}\n```"
            ),
        },
    ]
    for turn in history[-12:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:8000]})
    messages.append({"role": "user", "content": q[:8000]})
    result = _chat_completions(
        provider=provider,
        api_key=api_key,
        model=model,
        messages=messages,
    )
    return result
