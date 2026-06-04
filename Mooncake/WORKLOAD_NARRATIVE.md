# Mooncake Trace Workloads: Narrative Description of Real Enterprise LLM Serving Patterns

This document describes the three production-derived traces released with the Mooncake paper (FAST'25). These are **not synthetic benchmarks** — they are sampled from Moonshot AI's Kimi production chatbot service, preserving real user behaviors, session structures, prefix cache sharing, and bursty arrival patterns typical of large-scale enterprise LLM deployments.

**Source**: `traces/{conversation_trace,toolagent_trace,synthetic_trace}.jsonl` (and the arxiv variant). Each line: `{"timestamp": <ms since start>, "input_length": <prompt tokens>, "output_length": <generated tokens>, "hash_ids": [<block hash ids for KVCache paged prefix> ... ]}`

The `hash_ids` are remapped opaque identifiers from `PrefixHash(prompt_tokens, block_size)`. Matching prefixes across requests = KVCache hits (critical for the Mooncake disaggregated KVCache pool and scheduling efficacy). The released traces are the first open dataset enabling realistic KVCache reuse studies.

---

## 1. Conversation Workload (conversation_trace.jsonl)

**Real user chat traffic** from Kimi, 1 hour sample, prioritizing same-user multi-turn sessions.

- **Volume & Rate**: 12,031 requests, ~3.4 avg RPS over 3537s. **Highly bursty**: mean ~10 concurrent reqs per unique timestamp, bursts up to 28 simultaneous arrivals. Real traffic shows "spiky" behavior from user activity waves, not smooth poisson.
- **Lengths**:
  - Input (prompt): median 6.9k, mean 12k, p95~39.5k, max~126k tokens. Heavy-tailed (log-sd ~1.2); many long-context multi-turn histories + RAG.
  - Output: median 350, mean 343, p95~700, max 2k. Longer, more "chatty" generations than tool calls.
- **KVCache / Prefix Sharing (the critical enterprise pattern)**: ~38-40% average prefix cache block hit ratio. 
  - A very small number of "hot" blocks (e.g. system prompts, popular tools/docs) are reused across thousands of requests (one block hit 12k×).
  - Many requests share long common prefixes (first 5-10+ blocks identical), corresponding to shared instructions + conversation history within sessions or popular retrieval contexts.
  - This is what makes prefix caching + global pool in Mooncake so effective: 40% cache ratio directly translates to huge compute savings on prefill.
- **Other**: Low input/output length correlation. Hash block lifetimes span minutes to the full hour (temporal locality for cache policies). 842+ distinct multi-turn "session roots" (3-block prefixes with reuse).

**Why this matters for modeling**: Tests long-context prefill savings + moderate cache hit rates. Bursts stress queuing/scheduling. Matches "chatbot" use case in paper.

---

## 2. Tool & Agent Workload (toolagent_trace.jsonl)

**Production tool-calling and agent traffic**, same 1h window from different cluster. Higher volume, more cache-friendly.

- **Volume & Rate**: 23,608 requests, ~6.7 avg RPS. **Extremely bursty**: avg ~20 reqs per timestamp, max burst 47 concurrent. Even spikier than chat.
- **Lengths**:
  - Input: med 6.3k, mean 8.6k, p95 26k, max 126k. Still long but slightly shorter on avg than pure chat.
  - Output: **median only 30 tokens**, mean 182, p95 600. Classic for tool use: many "call tool X with args" + short confirms, occasional long reasoning traces or final answers.
- **KVCache / Prefix Sharing**: **Highest reuse ~59-63%** block hit ratio in paper.
  - Dominant shared prefixes: e.g. one 3-block root `(46,47,48,...)` shared by thousands of requests (~40%+ of traffic in samples). Indicates heavy use of **identical system prompts, tool definitions, few-shot examples, or agent scaffolds** across many parallel agent invocations.
  - High cache hit because agents often start from same "persona + tools" context; then diverge on per-task user query + retrieved state.
  - Perfect for exercising Mooncake's global cache pool + transfer engine (high hit => more transfer opportunities to balance load).
- **Other patterns**: Slightly higher I/O length corr than chat (0.3). Hot blocks even hotter (one ~11k×). Shorter block lists on median (13 vs 14-24).

**Why this matters**: Stresses the system on high cache ratios + short-decode + burst prefill avoidance. In paper, this workload favored prefix-caching baselines less than Mooncake's disagg+global store.

---

## 3. Synthetic Workload (synthetic_trace.jsonl)

Constructed from public long-context datasets (ShareGPT conversations, Leval, LooGLE) + Poisson arrivals. **Lower fidelity to production burst/cache** but useful for controlled long-context dispersion.

- **Volume & Rate**: 3,993 reqs over ~17min (1022s), ~4 RPS. **Smooth**: max burst size 2, mean conc=1.0. Poisson process, no enterprise burst spikes.
- **Lengths**:
  - Input: longest on avg — med 11.6k, mean 15.3k, p95 49k, max 191k. (Public datasets emphasize long docs.)
  - Output: med 69, mean 149, p95 520. Shorter.
- **KVCache**: ~66% reported in paper, but our causal block-hit ~42% (method diff; dispersed hotspots by design). Max reuse per block only ~24× (vs 10k+ in real). Much less "hot prefix" concentration — more unique per-request content.
  - Useful to test "worst case" cache utilization and long-context prefill cost when sharing is low.
- **Arrivals**: Predictable, independent requests. Good for isolating other variables.

**Note**: The paper uses this to show Mooncake still wins +40% effective capacity even with poorer cache locality.

---

## Key Cross-Cutting Real-Workload Patterns (Must Preserve in Any Extension/Generator)

1. **Bursty Arrivals, not Poisson**: Identical timestamps with 10-40+ reqs is common in real. Any generated trace for perf modeling **must** reproduce burst concurrency to stress schedulers, batching, queuing correctly. Cloning or burst-sampling from empirical is required.

2. **Heavy-tailed Lengths**: Both input and output follow roughly log-ish distributions with fat tails. Median << mean. Do not use uniform or simple normal; sample from empirical or fitted (log)normal per workload class. Long requests dominate prefill cost.

3. **Structured Prefix Sharing (not random or uniform reuse)**: 
   - Small set of extremely hot initial blocks (system prompts, common RAG).
   - Variable hit depths: some reqs hit 0-2 blocks (cold), many hit 5-15+, a few very long.
   - "Session-like" groups share long prefixes then branch (new user question or tool arg).
   - **Critical**: naive random hash_ids or independent per-req will produce unrealistically low cache hit rates and wrong prefill time distributions. The modeling will be invalid for real enterprise.

4. **Hash ID Semantics**: The ids are **not** tokens. They are block-granularity (e.g. 16- tokens/block depending on model). Number of hash_ids ≈ input_length / tokens_per_block. When extending prompts in sim, append new ids; common prefix = shared KV pages.

5. **Temporal Locality**: Popular blocks live for 100s of seconds. Good for SSD/DRAM tiering tests in Mooncake Store.

6. **Workload-specific "flavor"**: 
   - Chat = longer outputs, moderate-high sharing, bursty.
   - Agent = short outputs, **very high** sharing of scaffolds, burstier.
   - Avoid blending them into one "generic" trace unless explicitly mixed with controls.

---

## Using These Traces + Extensions for Perf Modeling

The intent of this tooling (the generator UI) is to let you:
- Start from a real base (to inherit authentic patterns).
- Scale volume (more users), duration, or intensity via parameters.
- Tune "what if" : longer contexts (input mult), higher cache pressure (more sharing of hot prefixes), different burstiness, mixed workloads.
- Always output **same JSONL format + manifest** so the downstream modeling tool sees provenance and can reproduce exactly what "workload mix v3 with reuse=high, input=1.5x" meant.

**Never** replace real traces with simplistic generators (e.g. every req independent 4k in / 200 out, uniform arrivals). The whole point of Mooncake's gains (up to 498% effective capacity) comes from exploiting exactly these real patterns.

See the paper for how these drove the design of KVCache-centric Conductor, chunked prefill, global pool, and RDMA transfer engine.

---

## References
- Mooncake FAST25 paper (in this dir).
- Original traces + code: https://github.com/kvcache-ai/Mooncake
- Traces released under Appendix A of the paper.
