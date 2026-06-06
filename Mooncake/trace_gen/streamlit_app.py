#!/usr/bin/env python3
# Copyright 2026 The Mooncake Traces Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Realistic LLM Trace Extender - Slider UI

Streamlit app for:
- Loading production-derived traces (currently Mooncake collection, or upload your own)
- Inspecting workload characteristics (burstiness, prefix cache sharing, length distributions)
- Using sliders to control scale, input/output multipliers, reuse bias, new sessions, etc.
- Previewing estimates and generating extended traces + full manifests
- Preserving the statistical structure of real enterprise traffic instead of falling back to generic synthetic data

The generator works with the standard trace schema and is designed to support multiple future trace sources.

Run:
  ./run_trace_ui.sh
  # or manually after pip install -r requirements.txt
"""

import json
import os
import random
import tempfile
import zipfile
from io import BytesIO
from typing import Optional, Tuple, List, Dict, Any

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

# Make the package importable
import sys
sys.path.insert(0, os.path.dirname(__file__))
from generator import (
    load_trace, analyze_trace, generate_extended, TraceAnalysis,
    BUILTIN_TRACES, load_builtin, save_trace, save_manifest,
    validate_hash_block_consistency,
)

st.set_page_config(page_title="Realistic LLM Trace Extender", layout="wide", page_icon="📈")

st.title("Realistic LLM Trace Extender")
st.caption("Scale real Kimi enterprise traces with controllable parameters while preserving burstiness, heavy tails, and *authentic* KVCache prefix sharing patterns. Output is modeling-tool ready with full manifest.")

# ---------- Sidebar: load / select base ----------
st.sidebar.header("1. Base Trace")

base_choice = st.sidebar.selectbox(
    "Choose base workload (or upload your own)",
    ["conversation (real chat)", "toolagent (real agent/tool)", "synthetic (public ds, poisson)", "Upload custom .jsonl"],
    index=0,
    key="base_choice"
)

# Persist the loaded trace data across reruns (so moving sliders doesn't reset everything)
if "loaded_base" not in st.session_state:
    st.session_state.loaded_base = {"choice": None, "reqs": None, "analysis": None, "label": ""}

loaded = st.session_state.loaded_base
uploaded_file = None
base_reqs: Optional[List[Dict[str, Any]]] = loaded.get("reqs")
analysis: Optional[TraceAnalysis] = loaded.get("analysis")
base_label = loaded.get("label", "")

needs_reload = (loaded.get("choice") != base_choice) or (base_reqs is None)

if base_choice == "Upload custom .jsonl":
    uploaded_file = st.sidebar.file_uploader(
        "Upload a trace (.jsonl)", type=["jsonl", "json", "txt"], key="trace_upload"
    )
    if uploaded_file is not None:
        # Reparse only if choice changed or we have no data, or the uploaded filename is different
        current_label = loaded.get("label", "")
        if needs_reload or current_label != uploaded_file.name:
            try:
                content = uploaded_file.read().decode("utf-8")
                lines = [l for l in content.splitlines() if l.strip()]
                base_reqs = [json.loads(l) for l in lines]
                base_label = uploaded_file.name
                analysis = analyze_trace(base_reqs, name=base_label)
                st.session_state.loaded_base = {
                    "choice": base_choice,
                    "reqs": base_reqs,
                    "analysis": analysis,
                    "label": base_label,
                }
                st.sidebar.success(f"Loaded {len(base_reqs)} reqs from upload")
            except Exception as e:
                st.sidebar.error(f"Failed to parse: {e}")
                base_reqs = None
                analysis = None
elif needs_reload:
    # Auto-load builtin when selected (no button needed; survives slider reruns)
    key = base_choice.split()[0]
    try:
        with st.spinner(f"Loading {key} trace..."):
            base_reqs, analysis = load_builtin(key)
            base_label = key
            st.session_state.loaded_base = {
                "choice": base_choice,
                "reqs": base_reqs,
                "analysis": analysis,
                "label": base_label,
            }
            st.sidebar.success(f"Loaded {base_label}: {analysis.n_reqs} reqs")
    except Exception as e:
        st.sidebar.error(f"Failed to load builtin: {e}")
        base_reqs = None
        analysis = None

if base_reqs is None or analysis is None:
    st.info("Select a trace from the current collection (auto-loads) or upload your own .jsonl following the standard schema.")
    st.stop()

assert analysis is not None

# Show quick narrative context
with st.sidebar.expander("What is this workload? (from narrative)"):
    if "conversation" in base_label:
        st.write("Real Kimi chatbot traffic: long contexts (med~7k), longer outputs (med~350), ~38% cache hit, very bursty (up to 28 concurrent at same ms).")
    elif "toolagent" in base_label:
        st.write("Real Kimi tool/agent traffic: high prefix cache (~60%+), short outputs (med 30), extremely bursty (47+), heavy shared agent scaffolds.")
    else:
        st.write("Constructed from public long-context datasets + Poisson. Lower burst, more dispersed content (good for stress-testing cache under low locality).")

st.sidebar.metric("Base requests", f"{analysis.n_reqs:,}")
st.sidebar.metric("Duration", f"{analysis.duration_ms/1000:.0f}s")
st.sidebar.metric("Avg RPS / Max burst", f"{analysis.avg_rps:.1f} / {max(analysis.burst_sizes)}")

# ---------- Main: parameters ----------
st.header("2. Extension Parameters (Sliders)")

col1, col2, col3 = st.columns(3)

with col1:
    scale = st.slider("Scale factor (copies + frac)", 0.5, 8.0, 2.0, 0.1, key="scale",
                      help="1.0 = identical to base. 2.5 = two full copies + 50% more. Multiplies load while preserving patterns.")
    input_mult = st.slider("Input length multiplier", 0.5, 4.0, 1.0, 0.05, key="input_mult",
                           help="Scale prompt sizes. >1.0 = longer contexts, more prefill cost. <1 trims unique tails after shared prefixes.")
    output_mult = st.slider("Output length multiplier", 0.5, 3.0, 1.0, 0.05, key="output_mult",
                            help="Affects decode time modeling.")

with col2:
    reuse_bias = st.slider("Cache reuse bias (hit intensity)", 0.0, 1.0, 0.78, 0.01, key="reuse_bias",
                           help="0 = more cold starts / unique content. 1.0 = aggressively use hottest shared prefixes (higher cache hit ratio, more like popular agent scaffolds or viral prompts). Stronger & more monotonic than before.")
    reuse_temperature = st.slider("Reuse temperature (sharpness)", 0.1, 2.0, 0.7, 0.05, key="reuse_temperature",
                                  help="Lower values (<1.0) make selection among hot prefixes much more peaked on the very top ones when reuse_bias is high. 0.3-0.6 gives near-deterministic popular prefixes. 1.0 = previous softer behavior.")
    burst_mult = st.slider("Burst size multiplier (experimental)", 0.5, 2.0, 1.0, 0.05, key="burst_mult",
                           help="Scales how large the concurrent groups at each timestamp are (when injecting new). Cloned bursts keep original sizes exactly.")
    time_jitter = st.slider("Timestamp jitter (ms)", 0, 50, 0, 1, key="time_jitter",
                            help="Small random ± jitter on cloned arrivals. 0 = exact timestamp copies (recommended for fidelity).")

with col3:
    share_hot = st.checkbox("Share hot prefixes across copies", value=True, key="share_hot",
                            help="If true (recommended), popular system prompts / common histories keep the *same* block ids across scaled copies → realistic increase in cache pressure as load grows.")
    seed = st.number_input("Random seed (reproducibility)", 0, 999999, 42, 1, key="seed")
    target_dur = st.number_input("Target duration (ms, 0=keep scaled)", 0, 3600*1000*4, 0, 10000, key="target_dur",
                                 help="Optional: linearly stretch/compress the final timeline to this duration.")

st.subheader("Advanced injection (for more variety beyond pure clones)")
c4, c5 = st.columns(2)
with c4:
    new_sessions = st.slider("Add explicit multi-turn session chains", 0, 200, 15, 5, key="new_sessions",
                             help="Generates new short conversation/agent chains with extending hash lists (simulates user follow-ups). Uses modeled hot prefixes + fresh tails.")
with c5:
    new_frac = st.slider("Mix in modeled new requests (frac of output)", 0.0, 0.25, 0.03, 0.01, key="new_frac",
                         help="Adds extra requests generated from empirical distributions + hot prefix sampler. Useful to increase unique block pressure or fill gaps.")

# Preset buttons - directly update widget state via keys so the sliders visibly change
st.markdown("**Quick presets** (click to set sliders above, then Generate)")
p1, p2, p3, p4 = st.columns(4)
if p1.button("2× chat, slightly longer ctx"):
    st.session_state["scale"] = 2.0
    st.session_state["input_mult"] = 1.15
    st.rerun()
if p2.button("3× toolagent, high cache pressure"):
    st.session_state["scale"] = 3.0
    st.session_state["reuse_bias"] = 0.92
    st.session_state["reuse_temperature"] = 0.45  # very sharp
    st.rerun()
if p3.button("Scale + 10% new sessions (variety)"):
    st.session_state["new_sessions"] = 40
    st.session_state["new_frac"] = 0.08
    st.rerun()
if p4.button("Low cache, 1.5× rate (stress prefill)"):
    st.session_state["reuse_bias"] = 0.25
    st.session_state["reuse_temperature"] = 1.2
    st.session_state["scale"] = 1.5
    st.rerun()

# ---------- Preview current base stats ----------
st.header("3. Base vs Expected Output Preview")

def plot_len_hists(ins: List[int], outs: List[int], title_prefix: str):
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2))
    for ax, vals, lab in zip(axes, (ins, outs), ("input tokens", "output tokens")):
        # log bins for heavy tail
        ax.hist([v for v in vals if v > 0], bins=40, log=True)
        ax.set_title(f"{title_prefix} {lab} (log y)")
        ax.set_xlabel(lab)
    st.pyplot(fig, clear_figure=True)

bcol1, bcol2 = st.columns(2)
with bcol1:
    st.subheader("Base trace")
    st.write(f"**{base_label}** — {analysis.summary()}")
    st.write(f"Median in/out: {analysis.median_input} / {analysis.median_output}")
    st.write(f"Approx block cache hit ratio: {analysis.approx_cache_hit_ratio*100:.1f}%")
    plot_len_hists(analysis.input_lens, analysis.output_lens, "Base")

with bcol2:
    st.subheader("After generation (estimated)")
    est_n = int(analysis.n_reqs * scale * (1 + new_frac))
    est_dur = int(analysis.duration_ms * scale) if not target_dur else target_dur
    est_rps = est_n / max(1, est_dur / 1000)
    st.metric("Est. requests / duration / RPS", f"{est_n:,} / {est_dur/1000:.0f}s / {est_rps:.1f}")
    st.write("Cache hit ratio (manifest + recomputed causal) will be similar or **much higher** with high reuse_bias + low temperature (the bias knob is now far more effective on injected/new content).")
    st.write("Bursts and per-burst concurrency structure are cloned exactly (jitter may slightly affect).")
    st.caption("Actual numbers + full causal hit recomputed after generation. Use reuse_temperature < 1.0 for near-deterministic popular prefixes.")

# ---------- Generate ----------
st.header("4. Generate")

if st.button("🚀 Generate Extended Trace + Manifest", type="primary", use_container_width=True):
    with st.spinner(f"Generating ~{int(analysis.n_reqs*scale)} requests (this clones bursts + remaps tails faithfully)..."):
        try:
            ext_reqs, manifest = generate_extended(
                base_reqs,
                analysis,
                scale=scale,
                input_mult=input_mult,
                output_mult=output_mult,
                reuse_bias=reuse_bias,
                reuse_temperature=reuse_temperature,
                burst_mult=burst_mult,
                time_jitter_ms=time_jitter,
                share_hot_prefixes=share_hot,
                seed=int(seed),
                target_duration_ms=target_dur or None,
                add_new_sessions=int(new_sessions),
                new_req_fraction=new_frac,
            )
            st.session_state["last_ext"] = ext_reqs
            st.session_state["last_manifest"] = manifest
            st.session_state["last_label"] = base_label
            st.success(f"Generated {manifest['output_stats']['n_requests']:,} requests in {manifest['output_stats']['duration_ms']/1000:.0f}s")
            # Quick confirmation for AIPerf users (guaranteed by generator)
            if not validate_hash_block_consistency(ext_reqs):
                st.caption("✓ All records are AIPerf `mooncake_trace` compatible (len(hash_ids) == ceil(input_length/512))")
        except Exception as e:
            st.error(f"Generation failed: {e}")
            st.exception(e)

# ---------- Results ----------
if "last_ext" in st.session_state and "last_manifest" in st.session_state:
    ext_reqs = st.session_state["last_ext"]
    manifest = st.session_state["last_manifest"]
    display_label = st.session_state.get("last_label", base_label)
    gen_params = manifest.get("params", {})
    gen_scale = gen_params.get("scale", scale)
    gen_seed = gen_params.get("seed", seed)

    st.header("5. Results & Downloads")

    mcol1, mcol2 = st.columns([1, 1])
    with mcol1:
        st.subheader("Output stats")
        ost = manifest["output_stats"]
        st.json(ost)
        st.write("**Params used**")
        st.json(manifest["params"])

    with mcol2:
        st.subheader("Manifest (for perf team)")
        st.caption("Save this alongside the .jsonl. It records exact provenance and parameters so runs are reproducible and traceable.")
        st.json(manifest)

    # Small preview table
    st.subheader("Sample of generated trace (first 8 rows)")
    preview_df = pd.DataFrame([
        {
            "ts": r["timestamp"],
            "in": r["input_length"],
            "out": r["output_length"],
            "blocks": len(r["hash_ids"]),
            "hash_head": str(r["hash_ids"][:3]),
        }
        for r in ext_reqs[:8]
    ])
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    # Download section
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        # jsonl as bytes
        buf = BytesIO()
        for r in ext_reqs:
            buf.write((json.dumps(r, separators=(",", ":")) + "\n").encode("utf-8"))
        buf.seek(0)
        st.download_button(
            "⬇️ Download extended_trace.jsonl",
            data=buf,
            file_name=f"extended_{display_label.replace(' ', '_')}_scale{gen_scale}_seed{gen_seed}.jsonl",
            mime="application/jsonl",
            use_container_width=True
        )
    with dcol2:
        man_buf = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        st.download_button(
            "⬇️ Download manifest.json",
            data=man_buf,
            file_name=f"manifest_{display_label.replace(' ', '_')}_scale{gen_scale}_seed{gen_seed}.json",
            mime="application/json",
            use_container_width=True
        )

    # Also offer a zip of both
    zbuf = BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trace.jsonl", buf.getvalue())
        zf.writestr("manifest.json", man_buf)
        zf.writestr("README.txt", f"""Generated from {display_label} using trace-gen.
See manifest for full params and stats.

VALIDATING WITH AIPERF
----------------------
These traces are Mooncake-format and work directly with NVIDIA AIPerf:

  aiperf analyze-trace trace.jsonl --output-file analysis.json --block-size 512

  aiperf profile \\
    --model <your-model> --endpoint-type chat --streaming \\
    --url http://localhost:8000 \\
    --input-file trace.jsonl \\
    --custom-dataset-type mooncake_trace \\
    --fixed-schedule \\
    --tokenizer <hf-tokenizer>

--fixed-schedule replays exact timestamps (bursts + timing fidelity).
The hash_ids drive realistic KV-cache prefix hit simulation inside AIPerf.

For convenient local validation (with reports, subsets, manifest checks):
  ./scripts/validate-with-aiperf.sh --with-replay --subset 50
  TRACE_FILE=your_trace.jsonl ./scripts/validate-with-aiperf.sh --with-replay

**Full canonical instruction set** (strongly recommended):
docs/VALIDATING_WITH_AIPERF.md in the repo root.

**Best way to get a complete local AIPerf + vLLM/Ollama + LMCache stack** (including setup scripts for macOS/Linux):
https://github.com/discoposse/aiperf-toolkit

See also the repo root README and Mooncake/trace_gen/README.md.
""")
    zbuf.seek(0)
    st.download_button(
        "⬇️ Download both as .zip (recommended for handoff)",
        data=zbuf,
        file_name=f"extended_{display_label.replace(' ', '_')}_x{gen_scale}_s{gen_seed}.zip",
        mime="application/zip",
        use_container_width=True
    )

    st.info("Hand the .zip (or the two files) to the perf team. The manifest + AIPerf validation (analyze-trace + mooncake_trace replay) confirm the trace plays out correctly with real burst + prefix-cache behavior. See the README.txt inside the zip for exact aiperf commands.")

    # Optional: re-analyze the output quickly for validation
    if st.checkbox("Re-analyze generated (compute full hit ratio etc, may be slow on very large)"):
        with st.spinner("Analyzing output trace..."):
            out_analysis = analyze_trace(ext_reqs, name="generated")
            st.write(out_analysis.summary())
            st.write("Compare to base:", analysis.summary())

st.divider()
st.caption("This tool preserves real statistical structure (bursts, prefix sharing, length distributions) instead of falling back to generic synthetic data.")