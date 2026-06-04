#!/usr/bin/env bash
# One-command launcher for the Mooncake Trace Workload Extender UI.
# Creates an isolated venv, installs deps from requirements.txt (pinned for
# safety + reproducibility), and runs the slider UI.
#
# Usage (from repo root or traces/):
#   bash Mooncake/trace_gen/run_ui.sh
#
# Then open the URL it prints (usually http://localhost:8501).
# Use the UI to load a trace (builtin or your own .jsonl), set sliders,
# generate, and download the extended trace + manifest for the perf team.
#
# See requirements.txt for the frozen package set and how to regenerate it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv_ui"

echo "==> Mooncake Trace Extender UI launcher"
echo "==> Working dir: $(pwd)"
echo "==> Script dir: ${SCRIPT_DIR}"

# Change to the script directory early so that requirements.txt and
# relative paths for the app (and builtin trace loading) are reliable
# no matter where the user invoked the launcher from.
cd "${SCRIPT_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "==> Creating venv at ${VENV_DIR}..."
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# Use a clean python -m pip and disable pip's wheel cache to avoid
# "Cache entry deserialization failed" warnings (common on macOS with
# multiple Python installs or previous partial/corrupt caches).
export PIP_NO_CACHE_DIR=1

echo "==> Upgrading pip + installing UI dependencies from requirements.txt (one time)"
echo "    This can take 1-5 minutes on first run (downloads ~150-300 MB of wheels)."
python -m pip install --no-cache-dir --upgrade pip -q
python -m pip install --no-cache-dir -r requirements.txt -q

echo "==> Launching Streamlit..."
echo "    (First run may take a few seconds to compile the app)"
echo ""
echo "==> Streamlit is starting. Your browser should open automatically to http://localhost:8501"
echo "==> (If not, copy the URL from the output below.)"
echo ""
exec streamlit run streamlit_app.py "$@"
