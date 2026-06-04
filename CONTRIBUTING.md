# Contributing to Realistic LLM Traces

Thank you for your interest in contributing! This repository hosts production-derived LLM serving traces (starting with the Mooncake / Kimi collection) along with tools to analyze them and generate controllable extensions that preserve the statistical properties that matter for real systems research and performance modeling.

## How to Contribute

### Reporting Issues
- Use the GitHub issue tracker.
- For questions about a specific collection, refer to its documentation (e.g., the Mooncake [narrative](Mooncake/WORKLOAD_NARRATIVE.md) and paper).
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
- Current traces live under `Mooncake/traces/` (first collection).
- Do **not** commit very large generated traces to the repo.
- Small demo extensions can live under the relevant collection's generator examples folder.
- New collections should live at the top level alongside `Mooncake/`, with their own documentation.

### License
By contributing, you agree that your contributions will be licensed under the same [Apache License 2.0](LICENSE) as the rest of the project.

## Questions?
Open a discussion or issue on GitHub. For questions about a specific collection (e.g. Mooncake traces), the upstream source and its documentation are the best starting point. For the generator, UI, or general extensibility, this repo's issues are perfect.

Thanks for helping make realistic, high-fidelity workload data available to the community!
