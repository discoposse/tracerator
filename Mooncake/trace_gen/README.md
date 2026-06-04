# Mooncake Real-Workload Trace Generator & Slider UI

This tool lets you load the authentic traces from the Mooncake paper (Kimi production traffic), inspect their enterprise characteristics, then **bulk them up** with precise controls while **exactly preserving** the real patterns:

- Bursty arrivals (many requests at identical timestamps, up to 47 concurrent)
- Heavy-tailed input/output lengths (median << mean, long context tails to 100k+)
- **Structured KV block prefix sharing** (hot shared system/agent prompts, session histories, variable hit depths) — not random hashes
- The exact co-occurrence topology that drives cache hit ratios (40-66% in the paper)

The output is a standard `trace.jsonl` (same schema) + `manifest.json` so the perf / modeling team can consume it reproducibly.

**Never use generic fake traces** (uniform lengths + poisson arrivals + independent hashes) for modeling Mooncake-style systems — the gains come from these real patterns.

## Quick Start (Slider Web UI)

```bash
# From the traces/ directory (or anywhere with Mooncake/ subdir)
bash Mooncake/trace_gen/run_ui.sh
```

- First run: creates `.venv_ui/`, installs pinned deps from `requirements.txt` (1-5 min)
- Opens browser to the UI (http://localhost:8501)
- Choose builtin (conversation / toolagent / synthetic) or upload any .jsonl in the same format
- Move the sliders (scale, input/output mult, reuse bias for cache intensity, etc.)
- Click **Generate**
- Download the `.zip` (contains `trace.jsonl` + `manifest.json` + README) and hand to perf team

The UI also shows before/after histograms and lets you re-analyze the generated trace.

## Reproducibility & requirements.txt

We use a **pinned `requirements.txt`** (instead of loose `pip install streamlit pandas matplotlib`) so that:

- Every run (your machine, CI, a colleague's laptop, the perf team) gets **exactly the same versions**.
- No surprise breakage from a new Streamlit release changing behavior or pulling different transitive deps.
- Full auditability of the UI environment that was used to produce a given trace + manifest.

The file contains the complete transitive closure from a clean macOS venv (as of the generation date in the header).

### Regenerating the pinned set

If you want to upgrade Streamlit (or pandas/matplotlib) and re-freeze:

```bash
rm -rf Mooncake/trace_gen/.venv_ui
bash Mooncake/trace_gen/run_ui.sh          # let it create the venv + install
source Mooncake/trace_gen/.venv_ui/bin/activate
pip freeze > Mooncake/trace_gen/requirements.txt
# Edit the header comment at the top of requirements.txt with the new date + reason
```

Then commit the updated `requirements.txt`.

## Programmatic Use (for scripting / CI)

```python
from Mooncake.trace_gen.generator import load_builtin, generate_extended, save_trace, save_manifest

reqs, analysis = load_builtin("toolagent")
print(analysis.summary())

extended, manifest = generate_extended(
    reqs, analysis,
    scale=3.5,
    input_mult=1.25,      # longer contexts
    output_mult=0.9,
    reuse_bias=0.92,      # crank up cache pressure (more hits on hot agent scaffolds)
    share_hot_prefixes=True,
    seed=1234,
    add_new_sessions=30,  # inject some fresh multi-turn chains
    new_req_fraction=0.05,
)

save_trace(extended, "my_extended_toolagent.jsonl")
save_manifest(manifest, "my_extended_toolagent.manifest.json")
```

See `generator.py` for all parameters and the `TraceAnalysis` structure.

## What the Generator Actually Does (Fidelity)

Primary method: **faithful per-copy cloning with smart remapping**

1. Group the real trace into its exact "bursts" (requests sharing the same timestamp).
2. For each desired copy:
   - Place the burst sequence at `copy_index * base_duration` offset.
   - For every block id in the original:
     - If it is a "hot" id (appears in many popular prefixes / high reuse in the analysis), keep the **original id value** — this makes the hot prompts (system, common RAG, agent defs) shared across all scaled copies exactly like more users hitting the same popular content in production.
     - Otherwise, assign a fresh id, **but consistently within the copy** (a per-copy map ensures that if req A and B shared block X in the real trace, their clones share the *same new* block id).
3. For length multipliers: after remapping the structure, extend or trim the *unique tail* (the part after the kept hot prefix). This lengthens/shortens the per-request novel content without destroying the sharing skeleton.
4. Small optional jitter, modeled "new" requests from the empiricals, and explicit extending session chains can be mixed in.
5. Everything is deterministic given the seed.

Result: the generated trace has (scaled) identical burst sizes, the same distribution of hit depths on the same "hot" content, the same within-"session" sharing groups, etc. The modeling tool will see realistic prefill savings and transfer behavior.

See `WORKLOAD_NARRATIVE.md` (in parent) for detailed characterization of the three workloads and why each pattern matters.

## Manifest Contents

The `manifest.json` always includes:
- `base_trace`, `base_stats`
- Full `params` dict (everything you slid or passed)
- `output_stats` (n, duration, rps, medians, p95, recomputed approx cache hit ratio on the output, unique blocks, max concurrency)
- `generator` version + notes

This is the contract with the perf team: "I started from the real conversation trace, applied 2.3× scale + 1.15× input + reuse=0.85, seed 77. Here are the resulting aggregates."

## File Layout

```
Mooncake/
  traces/
    conversation_trace.jsonl
    toolagent_trace.jsonl
    synthetic_trace.jsonl
  WORKLOAD_NARRATIVE.md
  trace_gen/
    generator.py          # core, no UI deps
    streamlit_app.py
    run_ui.sh             # the one-command launcher
    README.md             # this file
```

## Requirements (for the UI)

The launcher handles it. Manual: `pip install streamlit pandas matplotlib`.

Core generator is pure stdlib + no heavy deps (just std statistics + json).

## Releasing / Handoff

When you give a generated artifact to the perf team, also point them at the narrative and the original paper traces so they understand the provenance.

PRs / changes to the generator should keep the invariant: **any default or "realistic" preset must still look like real Kimi traffic, not lab-generated noise**.

## References

- Mooncake-FAST25.pdf (in parent dir)
- Original open-sourced traces + system: https://github.com/kvcache-ai/Mooncake

## License

See the root [LICENSE](../LICENSE) file (Apache-2.0). The generator code and documentation are released under the same license as the rest of this repository.
