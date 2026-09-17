# Contributing to Tracerator

Thanks for helping. This repository is a generator, UI, and import path around realistic LLM serving traces.

## Issues

Use GitHub issues. Include:

- Command or UI steps
- Docker / Python versions
- A small trace snippet when the bug is in generation or import

Questions about a bundled collection belong with that collection's docs, starting at [Mooncake/WORKLOAD_NARRATIVE.md](Mooncake/WORKLOAD_NARRATIVE.md).

## Pull requests

1. Fork and branch from `main`.
2. Keep changes scoped.
3. From the repository root:

```bash
docker compose up --build
```

4. Open http://localhost:8008 and generate a Conversation slice. Confirm integrity on Overview.
5. Run the tests that match your change:

```bash
python -m unittest tests.test_lightinferra_ui tests.test_lightinferra_golden
```

6. Update the matching Diátaxis page under `docs/` when behavior changes.
7. Open a PR that states what broke or what job the change finishes.

Do not commit `.env`, `artifacts/`, virtualenvs, or `node_modules/`.

## Code

- Python: readable, PEP 8 where it helps.
- `Mooncake/trace_gen/generator.py` stays free of heavy dependencies.
- The UI is Flask plus static files in `site/`. There is no Streamlit app in the supported path.

## Trace data

- Canonical small baselines live under `Mooncake/traces/`.
- Do not commit large generated traces.
- New collections belong at the top level next to `Mooncake/`, with their own docs.

## License

Contributions are Apache License 2.0.
