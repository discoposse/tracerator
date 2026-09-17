# Product Marketing Context

**Document version:** v1
**Last updated:** 2026-09-17

## Product Overview
**One-liner:** Tracerator turns real LLM serving traces into reproducible, AIPerf-ready workload variants so you can replay cache behavior instead of guessing it.
**What it does:** You pick a baseline (bundled Mooncake traces, an imported source, or a deterministic Turn-1/Turn-2 golden case), shape scale, ISL, and prefix reuse, then download a manifest plus `trace.jsonl`. The browser workspace validates with AIPerf, inspects arrivals and prefix hits, and compares runs.
**Product category:** LLM serving workload traces / KV-cache replay datasets
**Product type:** Open-source tool (Flask UI + generator + CLI scripts)
**Business model:** Apache-2.0. No paid SKU. Companion stack: [aiperf-toolkit](https://github.com/discoposse/aiperf-toolkit).

## Target Audience
**Target companies:** Teams that serve LLMs and care about TTFT, prefix cache, and KV offload. Labs, inference vendors, GPU/storage partners, and academic systems groups.
**Decision-makers:** Inference/perf engineers. Staff ML infra. Directors of serving when traces become the shared contract between research and capacity planning.
**Primary use case:** Produce a replayable Mooncake/AIPerf trace that keeps burstiness and prefix-hash structure, then hand it to a serving stack.
**Jobs to be done:**
- Hire Tracerator to get a what-if trace without waiting for a production dump.
- Hire Tracerator to prove a cache or storage change against the same request tape.
- Hire Tracerator to compare two workload shapes in one report.
**Use cases:**
- Scale a one-hour Mooncake conversation trace down for a laptop smoke test.
- Raise reuse bias to stress KV cache hit ratio.
- Generate a Turn-1 warm / Turn-2 resume pair for external KV.
- Import RAGPulse or SageServe and emit AIPerf-ready JSONL.

## Personas
| Persona | Cares about | Challenge | Value we promise |
|---------|-------------|-----------|------------------|
| Perf / serving engineer | Replay that matches arrivals and prefix hits | Poisson synthetics lie about cache and queueing | AIPerf-ready JSONL with `hash_ids` and a manifest |
| Champion (staff ML infra) | One shared workload contract | Every team invents its own generator | Zip + manifest + inspect UI |
| Decision maker | Capacity and SLO risk | Wrong traces → wrong GPU/storage buy | Comparable runs, not a sales deck |
| Financial buyer | Time-to-evidence | Custom trace pipelines are expensive | Docker Compose in one command |
| Technical influencer | Schema integrity | Broken `hash_ids` fail replay | `len(hash_ids) == ceil(input_length / 512)` |

## Problems & Pain Points
**Core problem:** Generic generators emit independent requests, smooth arrivals, and random prefixes. Prefill cost, cache hit ratio, and transfer behavior then look nothing like production.
**Why alternatives fall short:**
- Uniform/Poisson synthetics ignore burstiness and hot prefixes.
- Raw production dumps are scarce, huge, and hard to reshape.
- Vendor demos are not a shared, replayable tape.
**What it costs them:** Wrong TTFT, wasted GPU hours, and KV-offload tests that never touch storage.
**Emotional tension:** "If the trace is fake, the benchmark is theater."

## Competitive Landscape
**Direct:** Other Mooncake/AIPerf trace mungers — usually scripts without a run workspace or manifests.
**Secondary:** NVIDIA AIPerf public datasets and `aiperf synthesize` — useful volume, weaker production prefix structure unless you import a real source.
**Indirect:** Spreadsheet capacity models and vendor calculators — no request tape to replay.
**How each falls short:** They skip provenance, burst timestamps, and ordered `hash_ids`.

## Differentiation
**Key differentiators:**
- Production-derived Mooncake FAST'25 baselines in-repo.
- Explicit prefix `hash_ids` with an integrity rule.
- Run-centric UI: generate → validate → replay → inspect → narrative.
- Source import path (Mooncake, component hashes, SageServe).
- Deterministic LightInferra-style Turn-1/Turn-2 golden case for external KV.
**How we do it differently:** Shape real traces instead of sampling independent requests.
**Why that's better:** Replay sees the same hot prefixes and millisecond bursts the source had.
**Why customers choose us:** One zip that AIPerf can `analyze-trace` and `profile --custom-dataset-type mooncake_trace --fixed-schedule`.

## Objections
| Objection | Response |
|-----------|----------|
| "These are not production traces." | Correct. They preserve selected source properties. Treat them as planning/replay simulations. The bundled Mooncake files are the production-derived baselines. |
| "I can write a JSONL generator." | You can. You still need burst timestamps, prefix overlap, manifests, and AIPerf integrity. That is the product. |
| "Docker is heavy." | `docker compose up` is the supported path because the image includes `aiperf`. Bare Flask is for UI-only development. |

**Anti-persona:** Teams that only need a random chat prompt list and do not replay serving or KV cache.

## Switching Dynamics
**Push:** Synthetics keep missing cache hits. Production dumps never arrive in time.
**Pull:** One Compose command, bundled baselines, AIPerf-native schema.
**Habit:** Existing `aiperf synthesize` scripts and private notebooks.
**Anxiety:** "Will this lock me to one vendor or one schema?" Apache-2.0, Mooncake schema, no account.

## Customer Language
**How they describe the problem:**
- "The synthetic workload doesn't hit KV cache the way prod does."
- "I need a Mooncake trace I can actually replay."
- "Poisson arrivals make the queue look fine and production does not."
**How they describe us:**
- "Trace generator with a comparison UI."
- "Mooncake variants with a manifest."
**Words to use:** trace, replay, prefix, `hash_ids`, burst, ISL, OSL, manifest, AIPerf, Mooncake, KV cache
**Words to avoid:** magic, seamless, enterprise-grade, DDN, Infinia, internal hostnames
**Glossary:**
| Term | Meaning |
|------|---------|
| ISL | Input sequence length in tokens |
| OSL | Output sequence length in tokens |
| `hash_ids` | Ordered 512-token prefix block ids. Shared prefixes are KV hits. |
| Manifest | JSON provenance for a generated trace |
| Golden case | Deterministic Turn-1 warm / Turn-2 resume pair for external KV |

## Brand Voice
**Tone:** Direct, technical, honest about limits
**Style:** Short commands. Real paths and flags. No hype.
**Personality:** Precise, skeptical of fake workloads, generous with baselines

## Proof Points
**Metrics:** Bundled Conversation 12,031 reqs, Tool & Agent 23,608 reqs, Synthetic 3,993 reqs. Integrity rule on every row.
**Customers:** Public GitHub project (`discoposse/tracerator`). No logo wall claimed.
**Testimonials:** None captured yet.
**Value themes:**
| Theme | Proof |
|-------|-------|
| Realistic cache structure | `hash_ids` + Mooncake FAST'25 baselines |
| Reproducible handoff | `manifest.json` in every zip |
| Replay, not slides | AIPerf `mooncake_trace` + `--fixed-schedule` |

## Goals
**Business goal:** Make `discoposse/tracerator` the community default for Mooncake-style serving traces.
**Conversion action:** `docker compose up`, then generate and download a zip.
**Current metrics:** Not instrumented in this repo.

## Changelog
*Newest first. One line per revision: what changed and why.*
- v1 (2026-09-17) — Initial context for the public community edition.
