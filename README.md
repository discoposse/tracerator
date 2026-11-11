# Tracerator

Tracerator turns real LLM serving traces into reproducible, AIPerf-ready variants.

You hire it to get a request tape that still has millisecond bursts and shared KV prefixes. Generic Poisson generators drop both, so cache hit ratio, prefill cost, and queueing come out wrong.

```bash
git clone https://github.com/discoposse/tracerator.git
cd tracerator
docker compose up
```

Open http://localhost:8008. Create a run, generate a Mooncake Conversation slice, download the zip.

Generated traces keep selected source properties. They are planning and replay simulations, not a claim that your production traffic looks exactly like this.

## What you get

- Bundled Mooncake FAST'25 baselines: Conversation, Tool & Agent, Synthetic
- Parameterized variants: scale, ISL profile, reuse bias, extra sessions, seed
- Integrity rule: `len(hash_ids) == ceil(input_length / 512)` on every row
- Trace pages at `/traces`: arrival, prompt length, sessions, and KV size from the whole trace
- Workspace: generate → AIPerf validate/replay → inspect → compare
- Import path for other timestamped traces with prefix hashes

## Docs

| If you want to | Read |
|----------------|------|
| Generate a first zip in the UI | [Generate your first trace](docs/tutorials/generate-your-first-trace.md) |
| Run, rebuild, or use a local venv | [How to run the UI](docs/how-to/run-the-ui.md) |
| Check a trace with AIPerf | [How to validate with AIPerf](docs/how-to/validate-with-aiperf.md) |
| Bring your own workload | [How to import a workload source](docs/how-to/import-a-workload-source.md) |
| Compare manifests | [How to compare traces](docs/how-to/compare-traces.md) |
| Build a Turn-1 / Turn-2 external-KV case | [How to generate an external-KV golden trace](docs/how-to/generate-an-external-kv-golden-trace.md) |
| Publish a host | [How to self-host](docs/how-to/self-host.md) |
| Look up schema, env, or HTTP paths | [Docs map](docs/README.md) |
| Understand why Poisson traces mislead | [Why realistic traces matter](docs/explanation/why-realistic-traces.md) |

The Mooncake source narrative lives in [Mooncake/WORKLOAD_NARRATIVE.md](Mooncake/WORKLOAD_NARRATIVE.md).

## Bundled baselines

| Workload     | Requests | Max burst | Median input / output | Cache personality |
|--------------|----------|-----------|-----------------------|-------------------|
| Conversation | 12,031   | 28        | 6.9k / 350            | Multi-turn sessions, about 40% sharing |
| Tool & Agent | 23,608   | 47        | 6.3k / 30             | High reuse, bursty |
| Synthetic    | 3,993    | 2         | 11.6k / 69            | Long context, lower sharing |

Original traces: [kvcache-ai/Mooncake](https://github.com/kvcache-ai/Mooncake). Paper: [USENIX FAST '25](https://www.usenix.org/conference/fast25/presentation/qin) and [arXiv:2407.00079](https://arxiv.org/abs/2407.00079).

## Schema

Each JSONL line:

```json
{"timestamp": 1234, "input_length": 1024, "output_length": 64, "hash_ids": [101, 102]}
```

- `timestamp` is milliseconds, for AIPerf `--fixed-schedule`
- matching `hash_ids` prefixes are KV cache hits
- the zip also contains `manifest.json` with source, parameters, and aggregate stats

## Hugging Face auth

AIPerf replay may need a token. Copy `.env.example` to `.env` and set `HF_TOKEN`. Compose does not read your shell profile.

Do not commit `.env`.

## License

Apache License 2.0. Bundled Mooncake-derived traces are Apache-2.0 as well.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Citation

If you use the bundled Mooncake-derived traces in academic work, cite:

```bibtex
@article{qin2024mooncake,
  title={Mooncake: Trading More Storage for Less Computation -- A KVCache-centric Architecture for Serving LLM Chatbot},
  author={Qin, Ruoyu and Li, Zheming and He, Weiran and Cui, Jialei and Ren, Feng and Zhang, Mingxing and Wu, Yongwei and Zheng, Weimin and Xu, Xinran},
  journal={arXiv preprint arXiv:2407.00079},
  year={2024}
}
```

## Acknowledgments

The Mooncake team at Moonshot AI and Tsinghua University open-sourced the traces and the system.
