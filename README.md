# Traces from Mooncake (FAST'25)

This directory contains the open-sourced production-derived request traces used in the Mooncake paper, plus tooling to understand them and generate scaled, parameter-controlled extensions that **faithfully reproduce real enterprise LLM workload patterns**.

## Contents

- `Mooncake/traces/{conversation_trace,toolagent_trace,synthetic_trace}.jsonl` — the three traces from the paper (and `arxiv-trace/mooncake_trace.jsonl` variant).
- `Mooncake/Mooncake-FAST25.pdf` — the paper.
- `Mooncake/WORKLOAD_NARRATIVE.md` — detailed characterization of the three workloads, their statistical properties, why the patterns (especially burstiness + structured KV prefix sharing) matter for any modeling/simulation, and guidance for extensions.
- `Mooncake/trace_gen/` — the generator + slider UI (see its README).

## The Problem This Solves

You need more trace volume or "what-if" variants (longer contexts, higher cache intensity, different mixes, longer duration) for the perf modeling tool, **but** the traces must behave like real Kimi production traffic:

- Highly bursty arrivals (dozens of requests at the exact same millisecond)
- Heavy-tailed prompt & generation lengths
- **Authentic KVCache reuse patterns**: small number of extremely hot block prefixes (shared system prompts, agent scaffolds, popular RAG contexts) reused across thousands of requests; variable hit depths; session-like extensions that share long prefixes then branch on new user input.

Generic generators (independent requests, uniform or simple normal lengths, Poisson arrivals, random or independent hash_ids) will produce completely wrong prefill/decode costs, cache hit ratios, queuing, and transfer behavior. Mooncake's published gains (up to 5× effective capacity) come from exploiting exactly these real patterns.

## The Tool

See [Mooncake/trace_gen/README.md](Mooncake/trace_gen/README.md) (including the pinned `requirements.txt` for reproducible UI environments) and run:

```bash
./run_trace_ui.sh
# or
bash Mooncake/trace_gen/run_ui.sh
```

- Load a base (builtin or your own matching .jsonl)
- Tune with sliders: scale, input/output multipliers, reuse bias (how aggressively to hit hot shared prefixes), injection of new modeled sessions, seed, etc.
- Generate
- Download `.zip` containing `trace.jsonl` + `manifest.json`

The manifest records the exact base, all parameters, and output aggregate stats so the modeling run is fully traceable and reproducible.

The generator core (`generator.py`) can also be used directly from Python with no UI deps.

## Schema Reminder

Each line in a trace:

```json
{"timestamp": <ms>, "input_length": <prompt tokens>, "output_length": <gen tokens>, "hash_ids": [block1, block2, ...]}
```

`hash_ids` are remapped `PrefixHash(prompt_tokens, block_size)`. Matching leading segments across requests = prefix cache hits for the Mooncake Store / Conductor.

## Next Steps / Handoff to Perf

1. Use the UI to produce the desired variant(s) + manifest(s).
2. Tar/zip the artifacts + the manifest + a note referencing `WORKLOAD_NARRATIVE.md`.
3. The receiving team can replay with the original Mooncake simulator or their modeling tool, knowing the workload characteristics and how they were derived from real traffic.

## References

- Paper + original traces: https://github.com/kvcache-ai/Mooncake
- The three traces correspond to the "Conversation", "Tool&Agent", and "Synthetic" workloads in §5.2.1 and Appendix A of the paper.

Questions on the semantics of the traces or how to interpret reuse should start from the narrative and the paper (especially the scheduling algorithm and the definition of effective request capacity under TTFT/TBT SLOs).
