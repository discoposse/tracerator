#!/usr/bin/env bash
# Convenience wrapper: run the Mooncake trace extender UI from anywhere in the traces tree.
#
# This will:
#   - Create an isolated .venv_ui (if needed)
#   - Install pinned versions from requirements.txt (first time only, 1-5 min)
#   - Launch the slider-based web UI for loading/extending real Mooncake traces
#
# Then use the UI to generate extended traces + manifests for the perf team.
set -euo pipefail
exec bash Mooncake/trace_gen/run_ui.sh "$@"
