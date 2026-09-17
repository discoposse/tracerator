#!/usr/bin/env python3
"""Run AIPerf analyze-trace and profile replay against Tracerator-generated traces."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiperf_results import normalize_aiperf_run
from aiperf_datasets import (
    dataset_file_name,
    dataset_style_manifest,
    normalize_aiperf_public_dataset,
    normalize_aiperf_dataset_style,
    public_dataset_manifest,
    write_aiperf_dataset,
)

DEFAULT_SUBSET_N = 30
REPLAY_TIMEOUT_SEC = 900
# oMLX default install accepts Bearer EMPTY; custom oMLX keys reject it.
LOCAL_INFERENCE_API_KEY_PLACEHOLDER = "EMPTY"

# First-class OpenAI-compatible inference targets for AIPerf replay.
# "engine" in the UI/API is the backend id below.
INFERENCE_BACKENDS: Dict[str, Dict[str, Any]] = {
    "omlx": {
        "id": "omlx",
        "label": "oMLX (Apple Silicon)",
        "description": "Local oMLX OpenAI-compatible server (typically :8000).",
        "port": "8000",
        "default_model": "Qwen2.5-7B-Instruct-4bit",
        "default_tokenizer": "Qwen/Qwen2.5-7B-Instruct",
        "probe_paths": ["/v1/models"],
        "health_path": "/health",
        "auth_env": ("OMLX_API_KEY", "TRACERATOR_INFERENCE_API_KEY", "LLM_API_KEY"),
        "try_placeholder": True,
        "aiperf_endpoint_type": "chat",
    },
    "vllm": {
        "id": "vllm",
        "label": "vLLM",
        "description": "vLLM OpenAI-compatible server (typically :8000).",
        "port": "8000",
        "default_model": "Qwen/Qwen3-0.6B",
        "default_tokenizer": "Qwen/Qwen3-0.6B",
        "probe_paths": ["/v1/models"],
        "health_path": "/health",
        "auth_env": ("TRACERATOR_INFERENCE_API_KEY", "VLLM_API_KEY", "LLM_API_KEY"),
        "try_placeholder": False,
        "aiperf_endpoint_type": "chat",
    },
    "sglang": {
        "id": "sglang",
        "label": "SGLang",
        "description": "SGLang OpenAI-compatible server (typically :30000).",
        "port": "30000",
        "default_model": "Qwen/Qwen3-0.6B",
        "default_tokenizer": "Qwen/Qwen3-0.6B",
        "probe_paths": ["/v1/models"],
        "health_path": "/health",
        "auth_env": ("TRACERATOR_INFERENCE_API_KEY", "SGLANG_API_KEY", "LLM_API_KEY"),
        "try_placeholder": False,
        "aiperf_endpoint_type": "chat",
    },
    "ollama": {
        "id": "ollama",
        "label": "Ollama",
        "description": "Ollama native or OpenAI-compatible API (typically :11434).",
        "port": "11434",
        "default_model": "granite4:350m",
        "default_tokenizer": "ibm-granite/granite-4.0-micro",
        "probe_paths": ["/api/tags", "/v1/models"],
        "health_path": "/api/tags",
        "auth_env": ("TRACERATOR_INFERENCE_API_KEY", "OLLAMA_API_KEY", "LLM_API_KEY"),
        "try_placeholder": False,
        "aiperf_endpoint_type": "chat",
    },
    "openai": {
        "id": "openai",
        "label": "OpenAI-compatible",
        "description": "Generic OpenAI-compatible endpoint (custom URL).",
        "port": "8000",
        "default_model": "Qwen/Qwen3-0.6B",
        "default_tokenizer": "Qwen/Qwen3-0.6B",
        "probe_paths": ["/v1/models"],
        "health_path": "/health",
        "auth_env": ("TRACERATOR_INFERENCE_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"),
        "try_placeholder": False,
        "aiperf_endpoint_type": "chat",
    },
}

# Prefer oMLX when that is the active local Mac target; override with env.
DEFAULT_ENGINE = (os.environ.get("TRACERATOR_INFERENCE_BACKEND") or "omlx").strip().lower()
if DEFAULT_ENGINE not in INFERENCE_BACKENDS:
    DEFAULT_ENGINE = "omlx"


def list_inference_backends() -> List[Dict[str, Any]]:
    host = inference_host()
    return [
        {
            "id": cfg["id"],
            "label": cfg["label"],
            "description": cfg["description"],
            "port": cfg["port"],
            "default_model": cfg["default_model"],
            "default_tokenizer": cfg["default_tokenizer"],
            "default_url": f"http://{host}:{cfg['port']}",
            "auth_env": list(cfg["auth_env"]),
            "try_placeholder": bool(cfg["try_placeholder"]),
        }
        for cfg in INFERENCE_BACKENDS.values()
    ]


def get_inference_backend(engine: Optional[str]) -> Dict[str, Any]:
    key = (engine or DEFAULT_ENGINE or "omlx").strip().lower()
    # Legacy alias: "openai-compatible" / empty → openai
    if key in ("openai-compatible", "openai_compatible"):
        key = "openai"
    if key not in INFERENCE_BACKENDS:
        key = DEFAULT_ENGINE if DEFAULT_ENGINE in INFERENCE_BACKENDS else "omlx"
    return INFERENCE_BACKENDS[key]


def inference_host() -> str:
    """Host reachable from the Tracerator process for inference replay."""
    return os.environ.get("TRACERATOR_INFERENCE_HOST", "localhost").strip() or "localhost"


def running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def huggingface_token_configured() -> bool:
    return bool(
        (os.environ.get("HF_TOKEN") or "").strip()
        or (os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    )


def looks_like_openai_cloud_key(value: Optional[str]) -> bool:
    text = (value or "").strip()
    return text.startswith("sk-") or text.startswith("sk-proj-")


def is_local_inference_host(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        parsed = urlparse((url or "").rstrip("/") if "://" in (url or "") else f"http://{(url or '').rstrip('/')}")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "host.docker.internal",
        "::1",
    }


def inference_api_key(
    explicit: Optional[str] = None,
    *,
    include_openai_env: bool = False,
) -> Optional[str]:
    """Resolve an inference Bearer token.

    Prefer an explicit UI value or TRACERATOR_INFERENCE_API_KEY. OPENAI_API_KEY is
    often a real OpenAI sk-proj key kept in .env for other tools — only use it when
    include_openai_env=True (server already reported auth_required).
    """
    candidates = [
        explicit,
        os.environ.get("TRACERATOR_INFERENCE_API_KEY"),
    ]
    if include_openai_env:
        candidates.append(os.environ.get("OPENAI_API_KEY"))
    for candidate in candidates:
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def resolve_replay_api_key(
    explicit: Optional[str] = None,
    *,
    auth_required: bool = False,
    url: Optional[str] = None,
    allow_local_placeholder: bool = True,
    engine: Optional[str] = None,
) -> Optional[str]:
    """Pick the Bearer token to send to an OpenAI-compatible inference server.

    Never forwards OpenAI cloud sk-/sk-proj keys to local hosts (except the
    generic openai backend, which may intentionally use a cloud key). Backend
    auth_env order is respected (e.g. OMLX_API_KEY first for omlx).
    """
    backend = get_inference_backend(engine)
    local = is_local_inference_host(url or "")
    skip_cloud = local and backend["id"] != "openai"

    candidates: List[Optional[str]] = [explicit]
    for env_name in backend["auth_env"]:
        candidates.append(os.environ.get(env_name))
    # Always allow dedicated Tracerator key as a fallback across backends.
    if "TRACERATOR_INFERENCE_API_KEY" not in backend["auth_env"]:
        candidates.append(os.environ.get("TRACERATOR_INFERENCE_API_KEY"))

    for candidate in candidates:
        text = (candidate or "").strip()
        if not text:
            continue
        if skip_cloud and looks_like_openai_cloud_key(text):
            continue
        return text

    if (
        auth_required
        and allow_local_placeholder
        and backend.get("try_placeholder")
        and local
    ):
        return LOCAL_INFERENCE_API_KEY_PLACEHOLDER

    return None


def inference_api_key_configured(
    explicit: Optional[str] = None,
    *,
    engine: Optional[str] = None,
) -> bool:
    """True when a usable dedicated key exists for this backend (not sk-proj on local)."""
    return resolve_replay_api_key(
        explicit,
        auth_required=False,
        allow_local_placeholder=False,
        engine=engine,
        url="http://host.docker.internal:8000",
    ) is not None


def should_send_inference_api_key(
    *,
    explicit: Optional[str] = None,
    auth_required: bool = False,
    url: Optional[str] = None,
    allow_local_placeholder: bool = True,
    engine: Optional[str] = None,
) -> bool:
    """Only attach auth when the server needs it or a dedicated local key was set."""
    return resolve_replay_api_key(
        explicit,
        auth_required=auth_required,
        url=url,
        allow_local_placeholder=allow_local_placeholder,
        engine=engine,
    ) is not None


def aiperf_subprocess_env(
    *,
    send_api_key: bool,
    api_key: Optional[str] = None,
) -> Dict[str, str]:
    """Build env for AIPerf so a stray OpenAI OPENAI_API_KEY is not auto-sent."""
    env = {key: str(value) for key, value in os.environ.items() if value is not None}
    if send_api_key:
        resolved = (api_key or "").strip() or None
        if resolved:
            env["OPENAI_API_KEY"] = resolved
            env["TRACERATOR_INFERENCE_API_KEY"] = resolved
        else:
            env.pop("OPENAI_API_KEY", None)
            env.pop("TRACERATOR_INFERENCE_API_KEY", None)
        return env
    env.pop("OPENAI_API_KEY", None)
    env.pop("TRACERATOR_INFERENCE_API_KEY", None)
    return env



def artifact_run_root() -> Path:
    root = Path(os.environ.get("TRACERATOR_AIPERF_ARTIFACT_ROOT", "/app/artifacts/ui-aiperf-runs"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize_server_model_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if "/" in text:
        return text
    if "--" in text:
        return text.replace("--", "/", 1)
    return text


# Short / local serving names → Hugging Face tokenizer repos AIPerf can download.
TOKENIZER_ALIASES = {
    "Qwen2.5-7B-Instruct-4bit": "Qwen/Qwen2.5-7B-Instruct",
    "Qwen2.5-7B-Instruct-8bit": "Qwen/Qwen2.5-7B-Instruct",
    "Qwen2.5-7B-Instruct": "Qwen/Qwen2.5-7B-Instruct",
    "Qwen2.5-7B": "Qwen/Qwen2.5-7B",
    "Qwen3-0.6B": "Qwen/Qwen3-0.6B",
    "Qwen3-1.7B": "Qwen/Qwen3-1.7B",
    "Qwen3-4B": "Qwen/Qwen3-4B",
    "Qwen3-8B": "Qwen/Qwen3-8B",
}


def resolve_hf_tokenizer(value: Optional[str], *, fallback: Optional[str] = None) -> str:
    """Map a server/model label to a Hugging Face tokenizer repo id."""
    normalized = normalize_server_model_name(value) or ""
    if not normalized:
        return fallback or default_tokenizer(DEFAULT_ENGINE)

    leaf = normalized.split("/")[-1]
    if leaf in TOKENIZER_ALIASES:
        return TOKENIZER_ALIASES[leaf]
    if normalized in TOKENIZER_ALIASES:
        return TOKENIZER_ALIASES[normalized]

    # mlx-community/Qwen2.5-7B-Instruct-4bit → Qwen/Qwen2.5-7B-Instruct
    stripped = re.sub(r"-(?:[23458]bit|awq|gptq|gguf)$", "", leaf, flags=re.IGNORECASE)
    if stripped in TOKENIZER_ALIASES:
        return TOKENIZER_ALIASES[stripped]
    if stripped.startswith("Qwen") and "/" not in stripped:
        return f"Qwen/{stripped}"

    if "/" in normalized:
        return normalized

    # Bare local tag with no mapping — keep as-is; caller may use --use-server-token-count.
    return normalized


def looks_like_hf_repo_id(value: Optional[str]) -> bool:
    text = (value or "").strip()
    return "/" in text and not text.startswith("/") and " " not in text



def find_aiperf_binary(explicit: Optional[str] = None) -> Optional[str]:
    for candidate in (
        explicit,
        os.environ.get("AIPERF_BIN"),
        "/usr/local/bin/aiperf",
        "/usr/bin/aiperf",
    ):
        if not candidate:
            continue
        path = Path(str(candidate)).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    venv_dir = Path(os.environ.get("VENV_DIR", Path.home() / "venv")).expanduser()
    candidates = [
        venv_dir / "bin" / "aiperf",
        Path.home() / ".venv" / "bin" / "aiperf",
        Path.home() / "venv-aiperf" / "bin" / "aiperf",
        Path.home() / ".local" / "bin" / "aiperf",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    found = shutil.which("aiperf")
    return found


def aiperf_install_hint() -> str:
    if running_in_container():
        return (
            "AIPerf is not installed in the Tracerator container image. "
            "Host installs are not visible inside Docker. Rebuild from the repo root: "
            "docker compose up --build"
        )
    return (
        "Install AIPerf in this Python environment (for example: python3 -m venv ~/venv && "
        "~/venv/bin/pip install aiperf) or set AIPERF_BIN to the binary path."
    )


def get_aiperf_version(aiperf_bin: str) -> str:
    try:
        proc = subprocess.run(
            [aiperf_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        return line[0] if line else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def default_inference_port(engine: str) -> str:
    """Host port for the inference server (not Tracerator's UI port)."""
    explicit = (os.environ.get("TRACERATOR_INFERENCE_PORT") or "").strip()
    if explicit:
        return explicit
    return str(get_inference_backend(engine)["port"])


def default_url(engine: str) -> str:
    host = inference_host()
    port = default_inference_port(engine)
    return f"http://{host}:{port}"


def looks_like_tracerator_port_conflict(url: str) -> bool:
    """True when URL likely points at Tracerator's own Flask UI (port 8008)."""
    base = (url or "").rstrip("/")
    if not base:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base if "://" in base else f"http://{base}")
    except ValueError:
        return False
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    # Tracerator compose publishes 8008:8008.
    if port != 8008:
        return False
    host = (parsed.hostname or "").lower()
    return host in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "host.docker.internal",
        "::1",
    }


def tracerator_port_conflict_hint(url: str) -> str:
    return (
        f"The inference URL {url} points at port 8008, which is Tracerator's own UI. "
        "AIPerf chat requests get Flask 405 Method Not Allowed HTML instead of model responses. "
        "Point Server URL at your inference backend OpenAI-compatible base "
        f"(default http://{inference_host()}:{default_inference_port(DEFAULT_ENGINE)}), not Tracerator."
    )


def default_model(engine: str) -> str:
    return str(get_inference_backend(engine)["default_model"])


def default_tokenizer(engine: str) -> str:
    return str(get_inference_backend(engine)["default_tokenizer"])


def check_inference_server(url: str, engine: str, timeout_sec: float = 4.0) -> bool:
    return bool(probe_inference_server(url, engine, timeout_sec=timeout_sec).get("reachable"))


def probe_inference_server(
    url: str,
    engine: str,
    *,
    api_key: Optional[str] = None,
    timeout_sec: float = 4.0,
) -> Dict[str, Any]:
    backend = get_inference_backend(engine)
    engine = backend["id"]
    base = (url or "").rstrip("/")
    result: Dict[str, Any] = {
        "url": base,
        "engine": engine,
        "backend": {
            "id": backend["id"],
            "label": backend["label"],
            "auth_env": list(backend["auth_env"]),
        },
        "reachable": False,
        "health_ok": False,
        "models_ok": False,
        "auth_required": False,
        "api_key_configured": inference_api_key_configured(api_key, engine=engine),
        "placeholder_key_ok": False,
        "default_model": None,
        "models": [],
        "health": None,
        "hint": None,
    }
    if not base:
        result["hint"] = "Set the inference server URL (for Docker Desktop, usually http://host.docker.internal:<port>)."
        return result

    if looks_like_tracerator_port_conflict(base):
        result["hint"] = tracerator_port_conflict_hint(base)
        result["tracerator_port_conflict"] = True
        return result

    def _open(request_url: str, headers: Optional[Dict[str, str]] = None):
        request = urllib.request.Request(request_url)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        return urllib.request.urlopen(request, timeout=timeout_sec)

    auth_headers = None
    resolved_key = resolve_replay_api_key(
        api_key, auth_required=False, url=base, engine=engine, allow_local_placeholder=False
    )
    if resolved_key:
        auth_headers = {"Authorization": f"Bearer {resolved_key}"}

    health_path = str(backend.get("health_path") or "/health")
    try:
        with _open(f"{base}{health_path}") as resp:
            if 200 <= resp.status < 300:
                result["health_ok"] = True
                result["reachable"] = True
                try:
                    health = json.loads(resp.read().decode("utf-8"))
                except json.JSONDecodeError:
                    health = None
                if isinstance(health, dict):
                    result["health"] = health
                    result["default_model"] = normalize_server_model_name(
                        health.get("default_model") or health.get("model")
                    )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            result["auth_required"] = True
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass

    model_paths = list(backend.get("probe_paths") or ["/v1/models"])

    def _try_models(headers: Optional[Dict[str, str]]) -> bool:
        for path in model_paths:
            try:
                with _open(f"{base}{path}", headers) as resp:
                    if 200 <= resp.status < 300:
                        result["models_ok"] = True
                        result["reachable"] = True
                        try:
                            payload = json.loads(resp.read().decode("utf-8"))
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict):
                            items = payload.get("data") or payload.get("models") or []
                            if isinstance(items, list):
                                names = []
                                for item in items:
                                    if isinstance(item, dict):
                                        name = item.get("id") or item.get("name") or item.get("model")
                                        if name:
                                            names.append(str(name))
                                    elif item:
                                        names.append(str(item))
                                result["models"] = names[:10]
                                if names and not result["default_model"]:
                                    result["default_model"] = normalize_server_model_name(names[0])
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    result["auth_required"] = True
                    if result["health_ok"]:
                        result["reachable"] = True
                continue
            except (urllib.error.URLError, TimeoutError, ValueError):
                continue
        return False

    if not _try_models(auth_headers) and result.get("auth_required") and not resolved_key:
        if backend.get("try_placeholder"):
            placeholder = LOCAL_INFERENCE_API_KEY_PLACEHOLDER
            if _try_models({"Authorization": f"Bearer {placeholder}"}):
                result["placeholder_key_ok"] = True

    has_dedicated_key = inference_api_key_configured(api_key, engine=engine)
    result["api_key_configured"] = has_dedicated_key or bool(result.get("placeholder_key_ok"))

    if result["auth_required"] and engine == "omlx" and not result["api_key_configured"]:
        result["hint"] = (
            "oMLX requires its API key (Settings → API Key). Paste it into the Inference API Key "
            "field, or set OMLX_API_KEY in the host environment / repo .env and run "
            "`docker compose up -d --force-recreate`. Do not use an OpenAI sk-proj key."
        )
    elif result["auth_required"] and not result["api_key_configured"]:
        env_hint = " / ".join(backend["auth_env"][:2])
        result["hint"] = (
            f"{backend['label']} requires an API key. Set {env_hint} "
            "in the repo-root .env and restart with docker compose up -d --force-recreate."
        )
    elif result["reachable"] and result["default_model"]:
        extra = ""
        if result.get("placeholder_key_ok"):
            extra = f" Auth: Bearer {LOCAL_INFERENCE_API_KEY_PLACEHOLDER}."
        elif result["auth_required"] and has_dedicated_key:
            extra = f" Auth: {backend['auth_env'][0]}."
        result["hint"] = (
            f"Detected {backend['label']} model {result['default_model']}.{extra} "
            "Match the Model and Tokenizer fields to this ID."
        )
    elif not result["reachable"]:
        result["hint"] = (
            f"No {backend['label']} server detected at {base}. "
            f"Start it on the host (default {default_url(engine)})."
        )
    return result


def write_trace_bundle(work_dir: Path, reqs: List[Dict[str, Any]], manifest: Dict[str, Any]) -> Tuple[Path, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    trace_path = work_dir / "trace.jsonl"
    manifest_path = work_dir / "manifest.json"
    trace_path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in reqs) + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return trace_path, manifest_path


def build_subset(trace_path: Path, subset_n: int, out_path: Path) -> int:
    lines: List[str] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            lines.append(line.rstrip("\n"))
            if len(lines) >= subset_n:
                break
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def summarize_analysis(analysis: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    manifest_hit = manifest.get("approx_cache_hit_ratio")
    analysis_hit = analysis.get("cache_hit_rate")
    delta = None
    if isinstance(manifest_hit, (int, float)) and isinstance(analysis_hit, (int, float)):
        delta = round(float(analysis_hit) - float(manifest_hit), 6)

    return {
        "total_requests": analysis.get("total_requests"),
        "cache_hit_rate": analysis.get("cache_hit_rate"),
        "manifest_hit_ratio": manifest_hit,
        "hit_ratio_delta": delta,
        "avg_isl": analysis.get("avg_isl"),
        "avg_osl": analysis.get("avg_osl"),
        "prefix_reuse_ratio": analysis.get("prefix_reuse_ratio"),
        "unique_prefixes": analysis.get("unique_prefixes"),
    }


def run_analyze_trace(
    aiperf_bin: str,
    trace_path: Path,
    output_path: Path,
    block_size: int = 512,
) -> Tuple[Dict[str, Any], List[str]]:
    command = [
        aiperf_bin,
        "analyze-trace",
        str(trace_path),
        "--output-file",
        str(output_path),
        "--block-size",
        str(block_size),
    ]
    proc = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "aiperf analyze-trace failed").strip()
        raise RuntimeError(detail)

    if not output_path.exists():
        raise RuntimeError(f"analysis output missing: {output_path}")

    with output_path.open("r", encoding="utf-8") as handle:
        report = json.load(handle)
    if not isinstance(report, dict):
        raise RuntimeError("analysis output is not a JSON object")
    return report, command


def run_profile_replay(
    aiperf_bin: str,
    trace_path: Path,
    artifact_dir: Path,
    *,
    engine: str,
    model: str,
    tokenizer: str,
    url: str,
    fixed_schedule: bool,
    slice_duration: Optional[int] = None,
    api_key: Optional[str] = None,
    auth_required: bool = False,
    allow_local_placeholder: bool = True,
    timeout_sec: int = REPLAY_TIMEOUT_SEC,
    use_server_token_count: bool = True,
    custom_dataset_type: str = "mooncake_trace",
    request_count: Optional[int] = None,
    public_dataset: Optional[str] = None,
    hf_subset: Optional[str] = None,
    endpoint_type: str = "chat",
) -> Tuple[int, List[str], str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    resolved_tokenizer = resolve_hf_tokenizer(tokenizer, fallback=default_tokenizer(engine))
    command = [
        aiperf_bin,
        "profile",
        "--model",
        model,
        "--endpoint-type",
        endpoint_type,
        "--streaming",
        "--url",
        url.rstrip("/"),
        "--tokenizer",
        resolved_tokenizer,
        "--output-artifact-dir",
        str(artifact_dir),
        "--no-auto-plot",
    ]
    if public_dataset:
        command.extend(["--public-dataset", public_dataset])
        if hf_subset:
            command.extend(["--hf-subset", hf_subset])
    else:
        command.extend([
            "--input-file",
            str(trace_path),
            "--custom-dataset-type",
            custom_dataset_type,
        ])
    # Mooncake traces already carry ISL/OSL; skip client-side tokenization when possible.
    if use_server_token_count and custom_dataset_type == "mooncake_trace" and not public_dataset:
        command.append("--use-server-token-count")
    if request_count and public_dataset:
        command.extend(["--request-count", str(request_count)])
    elif request_count and custom_dataset_type == "random_pool":
        command.extend(["--num-conversations", str(request_count)])
    elif request_count and custom_dataset_type != "mooncake_trace":
        command.extend(["--request-count", str(request_count)])
    resolved_key = resolve_replay_api_key(
        api_key,
        auth_required=auth_required,
        url=url,
        allow_local_placeholder=allow_local_placeholder,
        engine=engine,
    )
    if resolved_key:
        command.extend(["--api-key", resolved_key])
    if slice_duration:
        command.extend(["--slice-duration", str(slice_duration)])
    effective_fixed_schedule = fixed_schedule and custom_dataset_type != "random_pool" and not public_dataset
    if effective_fixed_schedule:
        command.append("--fixed-schedule")
    else:
        command.extend(["--no-fixed-schedule", "--concurrency", "4"])

    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=aiperf_subprocess_env(send_api_key=bool(resolved_key), api_key=resolved_key),
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, command, detail


def tail_text_file(path: Path, max_lines: int = 80) -> Optional[str]:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    return "\n".join(lines[-max_lines:])


def summarize_replay_errors(artifact_dir: Path) -> List[Dict[str, Any]]:
    jsonl_path = artifact_dir / "profile_export.jsonl"
    if not jsonl_path.exists():
        return []
    errors: List[Dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            error = row.get("error")
            if not error:
                continue
            message = error.get("message")
            if isinstance(message, str):
                try:
                    nested = json.loads(message)
                    if isinstance(nested, dict) and isinstance(nested.get("error"), dict):
                        message = nested["error"].get("message") or message
                except json.JSONDecodeError:
                    pass
            errors.append({
                "code": error.get("code"),
                "type": error.get("type"),
                "message": message,
            })
    return errors


def replay_error_hint(errors: List[Dict[str, Any]]) -> Optional[str]:
    if not errors:
        return None
    messages = " ".join(str(item.get("message") or "") for item in errors).lower()
    joined = " ".join(
        f"{item.get('type') or ''} {item.get('code') or ''} {item.get('message') or ''}"
        for item in errors
    ).lower()
    if "invalid api key" in messages:
        return (
            "oMLX rejected the API key. Copy the key from oMLX → Settings → API Key into "
            "the Inference API Key field (or TRACERATOR_INFERENCE_API_KEY / OMLX_API_KEY in .env). "
            "Do not use an OpenAI sk-proj key."
        )
    if "api key required" in messages:
        return (
            "oMLX requires an API key. Paste it from oMLX → Settings → API Key into the "
            "Inference API Key field, then retry Replay. Or set TRACERATOR_INFERENCE_API_KEY "
            "in .env and run `docker compose up -d --force-recreate`."
        )
    if (
        "not a valid model identifier" in messages
        or "failed to load tokenizer" in messages
        or "repository not found" in messages
    ):
        return (
            "AIPerf could not load the tokenizer from Hugging Face. Set Tokenizer to a full "
            "HF repo id such as Qwen/Qwen2.5-7B-Instruct (not a short local name like "
            "Qwen2.5-7B-Instruct-4bit). Ensure HF_TOKEN is set if the repo needs auth."
        )
    if (
        "405" in joined
        or "method not allowed" in messages
        or ("<!doctype html>" in messages and "method not allowed" in messages)
    ):
        return (
            "Inference requests returned HTTP 405 Method Not Allowed with an HTML body — "
            "the Server URL is almost certainly Tracerator (port 8008) or another non-OpenAI "
            "endpoint. Point Server URL at your vLLM/Ollama OpenAI-compatible base "
            f"(default http://{inference_host()}:{default_inference_port('vllm')}), not Tracerator's UI port."
        )
    return None


def collect_replay_diagnostics(artifact_dir: Path, stdout_detail: str = "") -> Dict[str, Any]:
    log_path = artifact_dir / "logs" / "aiperf.log"
    errors = summarize_replay_errors(artifact_dir)
    log_tail = tail_text_file(log_path)
    hint = replay_error_hint(errors)
    summary_lines = []
    if hint:
        summary_lines.append(hint)
    if errors:
        first = errors[0]
        summary_lines.append(
            f"{len(errors)} replay request error(s): {first.get('type')} {first.get('code')} — {first.get('message')}"
        )
    if log_path.exists():
        summary_lines.append(f"Log file: {log_path}")
    if stdout_detail:
        summary_lines.append(stdout_detail.strip())
    return {
        "artifact_dir": str(artifact_dir),
        "log_file": str(log_path) if log_path.exists() else None,
        "log_tail": log_tail,
        "request_errors": errors,
        "hint": hint,
        "summary": "\n\n".join(part for part in summary_lines if part),
    }


def aiperf_status(engine: str = DEFAULT_ENGINE, url: Optional[str] = None) -> Dict[str, Any]:
    backend = get_inference_backend(engine)
    engine = backend["id"]
    binary = find_aiperf_binary(os.environ.get("AIPERF_BIN"))
    resolved_url = (url or default_url(engine)).rstrip("/")
    server = probe_inference_server(resolved_url, engine)
    status = {
        "available": bool(binary),
        "binary": binary,
        "version": get_aiperf_version(binary) if binary else None,
        "engine": engine,
        "backends": list_inference_backends(),
        "url": resolved_url,
        "server_reachable": bool(server.get("reachable")),
        "server": server,
        "install_hint": None if binary else aiperf_install_hint(),
        "huggingface_token_configured": huggingface_token_configured(),
        "inference_api_key_configured": inference_api_key_configured(engine=engine),
        "runtime": {
            "in_container": running_in_container(),
            "inference_host": inference_host(),
            "launch": "docker compose up from the repository root",
        },
        "defaults": {
            "engine": engine,
            "subset": DEFAULT_SUBSET_N,
            "model": server.get("default_model") or default_model(engine),
            "tokenizer": resolve_hf_tokenizer(
                server.get("default_model"),
                fallback=default_tokenizer(engine),
            ),
            "url": default_url(engine),
            "fixed_schedule": True,
            "auth_env": list(backend["auth_env"]),
            "api_key_hint": (
                "Paste oMLX Settings → API Key, or rely on OMLX_API_KEY in compose env"
                if engine == "omlx"
                else f"Optional; reads {' / '.join(backend['auth_env'][:2])} when set"
            ),
        },
    }
    return status


def validate_generated_trace(
    reqs: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate-side validation: static analyze and optional replay subset."""
    opts = options or {}
    mode = str(opts.get("mode") or "analyze").lower()
    run_replay = mode in ("replay", "full", "analyze_and_replay", "both")
    run_analyze = mode in ("analyze", "replay", "full", "analyze_and_replay", "both", "analyze_only")

    engine = get_inference_backend(opts.get("engine") or DEFAULT_ENGINE)["id"]
    url = str(opts.get("url") or default_url(engine)).rstrip("/")
    model = str(opts.get("model") or default_model(engine))
    tokenizer = resolve_hf_tokenizer(
        opts.get("tokenizer") or opts.get("model") or default_tokenizer(engine),
        fallback=default_tokenizer(engine),
    )
    fixed_schedule = str(opts.get("fixed_schedule", "true")).lower() not in ("0", "false", "no", "off")
    subset_n = max(1, int(opts.get("subset") or DEFAULT_SUBSET_N))
    slice_duration = opts.get("slice_duration")
    slice_value = int(slice_duration) if slice_duration not in (None, "", 0, "0") else None
    block_size = int(opts.get("block_size") or manifest.get("integrity", {}).get("block_size") or 512)
    api_key = opts.get("api_key")
    use_server_token_count = str(opts.get("use_server_token_count", "true")).lower() not in (
        "0", "false", "no", "off",
    )
    dataset_style = normalize_aiperf_dataset_style(
        opts.get("aiperf_dataset_style")
        or opts.get("custom_dataset_type")
        or (manifest.get("aiperf_dataset") or {}).get("style")
        or (manifest.get("integrity") or {}).get("schema")
    )
    style_meta = dataset_style_manifest(dataset_style)
    requested_public_dataset = opts.get("public_dataset") or opts.get("aiperf_public_dataset")
    public_dataset_key = normalize_aiperf_public_dataset(requested_public_dataset)
    public_dataset_meta = public_dataset_manifest(public_dataset_key)
    replay_dataset_source = "public" if public_dataset_key else "generated"
    invalid_public_dataset = bool(requested_public_dataset and not public_dataset_key)

    binary = find_aiperf_binary(os.environ.get("AIPERF_BIN"))
    result: Dict[str, Any] = {
        "aiperf": {
            "available": bool(binary),
            "binary": binary,
            "version": get_aiperf_version(binary) if binary else None,
        },
        "trace": {
            "n_requests": len(reqs),
            "subset_used": None,
            "aiperf_dataset_style": dataset_style,
            "replay_dataset_source": replay_dataset_source,
            "aiperf_public_dataset": public_dataset_key,
            "analysis_dataset_style": "mooncake_trace",
        },
        "aiperf_dataset": style_meta,
        "aiperf_public_dataset": public_dataset_meta,
        "manifest": manifest,
        "analysis": {"status": "skipped"},
        "replay": {"status": "skipped"},
    }

    if invalid_public_dataset:
        result["replay"] = {
            "status": "error",
            "error": f"unknown AIPerf public dataset: {requested_public_dataset}",
        }
        if not run_analyze:
            result["analysis"] = {"status": "skipped"}
            return result

    if not binary:
        result["analysis"] = {
            "status": "error",
            "error": aiperf_install_hint(),
        }
        if run_replay:
            result["replay"] = {
                "status": "error",
                "error": aiperf_install_hint(),
            }
        return result

    with tempfile.TemporaryDirectory(prefix="tracerator-aiperf-") as tmp:
        work_dir = Path(tmp)
        trace_path, _manifest_path = write_trace_bundle(work_dir, reqs, manifest)
        replay_trace = trace_path
        if run_replay:
            replay_trace = work_dir / dataset_file_name(dataset_style).replace(".jsonl", "_subset.jsonl")
            subset_used = build_subset(trace_path, subset_n, replay_trace)
            if dataset_style != "mooncake_trace" and not public_dataset_key:
                replay_trace = write_aiperf_dataset(replay_trace, reqs[:subset_used], dataset_style)
            result["trace"]["subset_used"] = subset_used
        else:
            result["trace"]["subset_used"] = len(reqs)

        if run_analyze:
            analysis_path = work_dir / "analysis.json"
            try:
                report, command = run_analyze_trace(binary, trace_path, analysis_path, block_size=block_size)
                result["analysis"] = {
                    "status": "ok",
                    "summary": summarize_analysis(report, manifest),
                    "report": report,
                    "command": command,
                    "dataset_style": "mooncake_trace",
                    "replay_dataset_style": dataset_style,
                }
            except (RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                result["analysis"] = {
                    "status": "error",
                    "error": str(exc),
                    "dataset_style": "mooncake_trace",
                    "replay_dataset_style": dataset_style,
                }

        if not run_replay:
            return result
        if invalid_public_dataset:
            return result

        server = probe_inference_server(url, engine, api_key=api_key)
        server_ok = bool(server.get("reachable"))
        if server.get("tracerator_port_conflict"):
            result["replay"] = {
                "status": "error",
                "server_reachable": False,
                "error": server.get("hint") or tracerator_port_conflict_hint(url),
                "server": server,
            }
            return result
        run_label = re.sub(r"[^a-z0-9]+", "-", str(manifest.get("base_source", {}).get("id") or "trace")).strip("-") or "trace"
        work_dir = artifact_run_root() / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{run_label}"
        work_dir.mkdir(parents=True, exist_ok=True)
        trace_path, _manifest_path = write_trace_bundle(work_dir, reqs, manifest)
        replay_trace = work_dir / dataset_file_name(dataset_style).replace(".jsonl", "_subset.jsonl")
        subset_used = build_subset(trace_path, subset_n, replay_trace)
        if dataset_style != "mooncake_trace" and not public_dataset_key:
            replay_trace = write_aiperf_dataset(replay_trace, reqs[:subset_used], dataset_style)
        result["trace"]["subset_used"] = subset_used
        artifact_dir = work_dir / "artifacts"
        hf_subset = str(opts.get("hf_subset") or public_dataset_meta.get("hf_subset") or "") if public_dataset_meta else ""
        endpoint_type = str(
            opts.get("endpoint_type")
            or (public_dataset_meta or {}).get("endpoint_type")
            or "chat"
        )
        try:
            return_code, command, detail = run_profile_replay(
                binary,
                replay_trace,
                artifact_dir,
                engine=engine,
                model=model,
                tokenizer=tokenizer,
                url=url,
                fixed_schedule=fixed_schedule,
                slice_duration=slice_value,
                api_key=api_key,
                auth_required=bool(server.get("auth_required")),
                allow_local_placeholder=bool(server.get("placeholder_key_ok")),
                use_server_token_count=use_server_token_count,
                custom_dataset_type=dataset_style,
                request_count=subset_used,
                public_dataset=public_dataset_key,
                hf_subset=hf_subset or None,
                endpoint_type=endpoint_type,
            )
        except subprocess.TimeoutExpired:
            diagnostics = collect_replay_diagnostics(artifact_dir, stdout_detail="")
            result["replay"] = {
                "status": "error",
                "server_reachable": server_ok,
                "error": f"replay timed out after {REPLAY_TIMEOUT_SEC}s",
                "diagnostics": diagnostics,
                "artifact_dir": str(work_dir),
            }
            return result

        diagnostics = collect_replay_diagnostics(artifact_dir, stdout_detail=detail)
        aggregate = artifact_dir / "profile_export_aiperf.json"
        if return_code == 0 and aggregate.exists():
            replay_result = normalize_aiperf_run(artifact_dir)
            result["replay"] = {
                "status": "ok",
                "server_reachable": server_ok,
                "replay_result": replay_result,
                "command": command,
                "diagnostics": diagnostics,
                "artifact_dir": str(work_dir),
            }
            return result

        error_text = diagnostics.get("summary") or detail or "aiperf profile failed"
        if server.get("auth_required") and not resolve_replay_api_key(
            api_key,
            auth_required=True,
            url=url,
            allow_local_placeholder=bool(server.get("placeholder_key_ok")),
            engine=engine,
        ):
            error_text = (
                "Inference server returned 401 Unauthorized (API key required). "
                "For oMLX, set OMLX_API_KEY (or paste Inference API Key from oMLX Settings). "
                f"Do not use an OpenAI sk-proj key.\n\n{error_text}"
            )
        elif server.get("default_model") and model != server.get("default_model"):
            error_text = (
                f"Configured model {model!r} may not match the server default "
                f"{server.get('default_model')!r}. Update the Model and Tokenizer fields.\n\n{error_text}"
            )

        if not server_ok:
            result["replay"] = {
                "status": "warn",
                "server_reachable": False,
                "command": command,
                "message": server.get("hint") or f"No inference server detected at {url}",
                "error": error_text,
                "diagnostics": diagnostics,
                "artifact_dir": str(work_dir),
            }
            return result

        result["replay"] = {
            "status": "error",
            "server_reachable": server_ok,
            "command": command,
            "error": error_text,
            "diagnostics": diagnostics,
            "artifact_dir": str(work_dir),
        }
        return result
