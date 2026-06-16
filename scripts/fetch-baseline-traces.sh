#!/usr/bin/env bash
# Fetch optional external baseline trace payloads used by Tracerator.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/fetch-baseline-traces.sh cc-weka-subagents

Environment overrides:
  TRACERATOR_CC_WEKA_DIR  Path to cc-traces-weka-with-subagents checkout
                          (default: /Users/ewright/Documents/cc-traces-weka-with-subagents-051826)

This script downloads the Git LFS payload into the local dataset checkout so
Tracerator can mount and use it as a baseline source.
EOF
}

target="${1:-}"
if [[ -z "$target" || "$target" == "-h" || "$target" == "--help" ]]; then
  usage
  exit 0
fi

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

ensure_git_lfs() {
  if ! command_exists git; then
    echo "ERROR: git is required." >&2
    exit 1
  fi
  if ! git lfs version >/dev/null 2>&1; then
    echo "ERROR: git-lfs is required to download this trace payload." >&2
    echo "Install it, then rerun this script:" >&2
    echo "  brew install git-lfs && git lfs install" >&2
    exit 1
  fi
}

is_lfs_pointer() {
  local file="$1"
  [[ -f "$file" ]] && head -n 1 "$file" | grep -q 'version https://git-lfs.github.com/spec/v1'
}

fetch_cc_weka() {
  local dir="${TRACERATOR_CC_WEKA_DIR:-/Users/ewright/Documents/cc-traces-weka-with-subagents-051826}"
  local trace="$dir/traces.jsonl"

  if [[ ! -d "$dir/.git" ]]; then
    echo "ERROR: expected a git checkout at $dir" >&2
    echo "Clone it first:" >&2
    echo "  git clone https://huggingface.co/datasets/DiscoPosse/cc-traces-weka-with-subagents-051826 \"$dir\"" >&2
    exit 1
  fi

  ensure_git_lfs

  echo "==> Fetching CC Weka with subagents trace payload"
  echo "    repo : $dir"
  echo "    file : $trace"

  git -C "$dir" lfs install --local
  git -C "$dir" lfs pull --include="traces.jsonl"

  if is_lfs_pointer "$trace"; then
    echo "ERROR: traces.jsonl is still a Git LFS pointer after pull." >&2
    echo "Check Hugging Face access/auth for the dataset and rerun." >&2
    exit 1
  fi

  echo "OK: traces.jsonl is downloaded."
  ls -lh "$trace"
  echo ""
  echo "Restart or refresh Tracerator:"
  echo "  ./run_trace_ui.sh"
}

case "$target" in
  cc-weka-subagents|cc-weka|weka)
    fetch_cc_weka
    ;;
  *)
    echo "ERROR: unknown target '$target'" >&2
    usage
    exit 1
    ;;
esac
