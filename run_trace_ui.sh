#!/usr/bin/env bash
# Copyright 2026 The Mooncake Traces Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Convenience wrapper: run the Realistic LLM Trace Extender UI from the repo root.
#
# This will:
#   - Create an isolated .venv_ui (if needed)
#   - Install pinned versions from requirements.txt (first time only, 1-5 min)
#   - Launch the slider-based web UI for loading traces and generating
#     controllable, high-fidelity extensions (with full manifests)
#
# The project supports multiple trace collections over time. Mooncake traces
# are the first collection.
set -euo pipefail
exec bash Mooncake/trace_gen/run_ui.sh "$@"
