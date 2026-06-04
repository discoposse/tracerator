# Realistic LLM Serving Traces

> **The best way to model real LLM inference systems is with data that actually came from one.**

Most research and performance modeling in LLM serving uses synthetic workloads that bear little resemblance to production traffic. Poisson arrivals. Independent requests. Uniform or simplistic length distributions. Almost no prefix cache sharing.

Real production traffic is different in ways that matter enormously for KVCache systems, schedulers, disaggregation strategies, and everything in between:

- Extreme burstiness (dozens of requests landing in the exact same millisecond)
- Long-lived, structured prefix sharing (the same system prompts, tool definitions, and conversation histories being reused across thousands of requests)
- Heavy-tailed context and generation lengths that make average-case thinking dangerous

This repository exists to give researchers and engineers access to **real, high-fidelity traces** — and the tools to extend and adapt them while preserving the statistical properties that actually drive system behavior.

## Current Collection: Mooncake Traces

The first (and currently only) collection comes from Moonshot AI's Kimi production service and was originally released as part of the [Mooncake paper (FAST'25)](https://github.com/kvcache-ai/Mooncake).

It includes three one-hour traces:

- **Conversation** — real multi-turn chatbot traffic with long contexts and natural session sharing.
- **Tool & Agent** — extremely high cache reuse from repeated agent scaffolds and tool definitions, combined with very bursty arrivals.
- **Synthetic** — constructed from public long-context datasets with Poisson arrivals (useful as a contrast case with lower sharing).

See the detailed [workload narrative and analysis](Mooncake/WORKLOAD_NARRATIVE.md) for deep statistics on burstiness, prefix cache behavior, length distributions, and why these patterns matter.

The original paper, traces, and system are available at the [Mooncake GitHub repo](https://github.com/kvcache-ai/Mooncake).

## The Generator

Raw traces are useful for replay. Real work usually requires asking "what if" questions:

- What if we had 3× the load?
- What if contexts were 50% longer on average?
- What if the cache was even hotter (more users hitting the same popular prompts)?
- What if we injected more multi-turn sessions?

Naive scaling or duplication destroys the very structure that makes these traces valuable. Our generator lets you scale, stretch, and remix traces while carefully preserving (or realistically extending) the burst patterns and prefix-sharing relationships encoded in the `hash_ids`.

Every generated output includes a **full manifest** recording the exact source, every parameter, the random seed, and resulting aggregate statistics. This makes experiments reproducible and auditable for the perf and modeling teams.

The generator works with the standard trace schema (`timestamp`, `input_length`, `output_length`, `hash_ids`). Future collections from other sources will be supported by the same tooling.

## Get Started — The Beautiful Way

We've built a clean, interactive landing page that explains the concepts, compares the workloads, and lets you play with the generator parameters visually before cloning or running anything.

**→ [Open the landing page](site/index.html)** (for the best experience, serve it locally: `python -m http.server -d site 8000`)

## Launch the Full Interactive UI

```bash
./run_trace_ui.sh
```

This sets up an isolated environment with pinned dependencies and launches a Streamlit app where you can:
- Load any of the included traces (or upload your own matching the schema)
- Adjust scale, length multipliers, cache reuse intensity, new session injection, etc.
- See live estimates
- Generate extended traces + manifests ready for your modeling pipeline

The core generator is also usable programmatically from Python with no UI dependencies.

## Why This Matters

Good modeling and good system design require good data. When your traces don't reflect real burstiness and real prefix cache behavior, your "improvements" may only work in simulation.

These traces (and the ability to controllably extend them) were a key part of the research that produced the Mooncake KVCache-centric architecture. We're making the data and the tooling available so the broader community can do the same kind of grounded work.

## Roadmap & Extensibility

This project is designed from the start to grow beyond a single source:

- Multiple trace collections from different organizations and use cases
- Support for additional output styles / formats
- More sophisticated generation strategies (while always prioritizing fidelity to real patterns over simplicity)
- Better tooling for analyzing and comparing trace characteristics

The schema is simple and documented. The generator is intentionally not hard-coded to any one collection.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). We especially welcome:
- New trace collections (with good documentation of their characteristics)
- Improvements to the generator that better preserve real statistical properties
- Better visualization and analysis tools

## Citation

If you use the Mooncake traces (the current collection), please cite the original paper:

```bibtex
@article{qin2024mooncake,
  title={Mooncake: Trading More Storage for Less Computation -- A {KVCache}-centric Architecture for Serving {LLM} Chatbot},
  author={Qin, Ruoyu and Li, Zheming and He, Weiran and Cui, Jialei and Ren, Feng and Zhang, Mingxing and Wu, Yongwei and Zheng, Weimin and Xu, Xinran},
  journal={arXiv preprint arXiv:2407.00079},
  year={2024}
}
```

## License

Licensed under the Apache License 2.0 (same as the upstream Mooncake release). See [LICENSE](LICENSE).

The trace data is derived from the Mooncake open-sourced dataset. The generator, UI, landing page, and supporting documentation are additional work released under the same license.

---

Real data. Controllable scaling. Reproducible experiments. Let's build better LLM systems.