# Contributing to Mooncake Traces

Thank you for your interest in contributing to this repository! It contains production-derived request traces from the [Mooncake](https://github.com/kvcache-ai/Mooncake) project (used in the FAST'25 paper) along with tools to analyze, extend, and generate new traces that preserve realistic enterprise LLM serving patterns.

## How to Contribute

### Reporting Issues
- Use the GitHub issue tracker.
- For trace-related questions (format, semantics, KVCache hash_ids, etc.), please reference the original [Mooncake paper](Mooncake/Mooncake-FAST25.pdf) and [WORKLOAD_NARRATIVE.md](Mooncake/WORKLOAD_NARRATIVE.md).
- When reporting bugs in the generator UI or scripts, include:
  - The exact command or steps
  - Python / Streamlit version
  - A small reproducible example if possible (e.g., a minimal trace snippet)

### Pull Requests
1. Fork the repository and create a feature branch.
2. Make your changes.
3. Ensure the generator still works:
   - Run `./run_trace_ui.sh` (or the inner script) and test loading traces + generating extensions.
   - Verify that generated traces follow the original patterns (bursty arrivals, prefix sharing via `hash_ids`, heavy-tailed lengths, etc.).
4. Update documentation (README, narrative, or code comments) as needed.
5. Submit a PR with a clear description of the change and why it is useful.

### Code Style
- Python code should be readable and follow PEP 8 where reasonable.
- The core generator (`Mooncake/trace_gen/generator.py`) has no heavy dependencies and should remain easy to use programmatically.
- The UI (`streamlit_app.py`) uses Streamlit best practices for state management (`st.session_state` + widget `key`s) so that configuration survives reruns.

### Trace Data
- The canonical traces live under `Mooncake/traces/`.
- Do **not** commit very large generated traces to the repo.
- Small demo extensions can live in `Mooncake/trace_gen/examples/`.
- If you create new high-fidelity generators or analysis scripts, prefer adding them under `trace_gen/` with documentation.

### License
By contributing, you agree that your contributions will be licensed under the same [Apache License 2.0](LICENSE) as the rest of the project.

## Questions?
Open a discussion or issue on GitHub. For questions specifically about the original Mooncake system or the semantics of the traces, the upstream [kvcache-ai/Mooncake](https://github.com/kvcache-ai/Mooncake) repository and its issues are the best place.

Thanks again for helping keep these traces useful for realistic LLM serving research and modeling!
