#!/usr/bin/env bash
# Fetch optional external baseline trace payloads used by Tracerator.
#
# Works on macOS and Ubuntu/Debian. Defaults to a sibling checkout next to
# this repo (for example ../cc-traces-weka-with-subagents-051826).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT="$(cd "${ROOT}/.." && pwd)"
INSTALL_DEPS=0
AUTO_CLONE=0

usage() {
  cat <<EOF
Usage:
  scripts/fetch-baseline-traces.sh [--install-deps] [--clone] cc-weka-subagents

Options:
  --install-deps   Install git-lfs via apt (Ubuntu/Debian) or brew (macOS) if missing
  --clone          Clone the dataset repo if the checkout directory is missing

Environment overrides:
  TRACERATOR_CC_WEKA_DIR  Path to cc-traces-weka-with-subagents checkout
                          (default: ${PARENT}/cc-traces-weka-with-subagents-051826)

After a successful fetch, set TRACERATOR_CC_WEKA_DIR to that checkout and
restart Compose so the container can see the payload.
EOF
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

detect_pkg_hint() {
  if command_exists apt-get; then
    echo "  sudo apt-get update && sudo apt-get install -y git-lfs"
    echo "  git lfs install"
  elif command_exists brew; then
    echo "  brew install git-lfs && git lfs install"
  else
    echo "  Install git-lfs for your OS, then: git lfs install"
  fi
}

ensure_git_lfs() {
  if ! command_exists git; then
    echo "ERROR: git is required." >&2
    exit 1
  fi
  if git lfs version >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    echo "==> Installing git-lfs"
    if command_exists apt-get; then
      if [[ "$(id -u)" -ne 0 ]] && command_exists sudo; then
        sudo apt-get update
        sudo apt-get install -y git-lfs
      else
        apt-get update
        apt-get install -y git-lfs
      fi
      git lfs install
    elif command_exists brew; then
      brew install git-lfs
      git lfs install
    else
      echo "ERROR: --install-deps requires apt-get or brew." >&2
      exit 1
    fi
  fi

  if ! git lfs version >/dev/null 2>&1; then
    echo "ERROR: git-lfs is required to download this trace payload." >&2
    echo "Install it, then rerun this script:" >&2
    detect_pkg_hint >&2
    echo "" >&2
    echo "Or rerun with --install-deps (needs root/sudo on Ubuntu):" >&2
    echo "  scripts/fetch-baseline-traces.sh --install-deps --clone cc-weka-subagents" >&2
    exit 1
  fi
}

is_lfs_pointer() {
  local file="$1"
  [[ -f "$file" ]] && head -n 1 "$file" | grep -q 'version https://git-lfs.github.com/spec/v1'
}

default_cc_weka_dir() {
  if [[ -n "${TRACERATOR_CC_WEKA_DIR:-}" ]]; then
    printf '%s\n' "$TRACERATOR_CC_WEKA_DIR"
    return
  fi
  # Sibling of the Tracerator checkout (Documents/... on laptop, /opt/... on VPS).
  printf '%s\n' "${PARENT}/cc-traces-weka-with-subagents-051826"
}

warn_macos_path_on_linux() {
  local dir="$1"
  if [[ "$(uname -s)" == "Linux" && "$dir" == /Users/* ]]; then
    echo "WARNING: checkout path looks like a macOS home path on Linux: $dir" >&2
    echo "Prefer the repo-sibling default instead:" >&2
    echo "  ${PARENT}/cc-traces-weka-with-subagents-051826" >&2
  fi
}

fetch_cc_weka() {
  local dir
  dir="$(default_cc_weka_dir)"
  local repo_url="https://huggingface.co/datasets/DiscoPosse/cc-traces-weka-with-subagents-051826"
  local trace="$dir/traces.jsonl"

  warn_macos_path_on_linux "$dir"

  if [[ ! -d "$dir/.git" ]]; then
    if [[ -d "$dir" && ! -d "$dir/.git" ]]; then
      echo "ERROR: $dir exists but is not a git checkout." >&2
      exit 1
    fi
    if [[ "$AUTO_CLONE" -ne 1 ]]; then
      echo "ERROR: expected a git checkout at $dir" >&2
      echo "Clone it first, or pass --clone:" >&2
      echo "  git clone \"$repo_url\" \"$dir\"" >&2
      echo "  scripts/fetch-baseline-traces.sh --install-deps --clone cc-weka-subagents" >&2
      exit 1
    fi
    echo "==> Cloning dataset into $dir"
    mkdir -p "$(dirname "$dir")"
    git clone "$repo_url" "$dir"
  fi

  ensure_git_lfs

  echo "==> Fetching CC Weka with subagents trace payload"
  echo "    repo : $dir"
  echo "    file : $trace"

  git -C "$dir" lfs install --local
  git -C "$dir" lfs pull --include="traces.jsonl"

  if [[ ! -f "$trace" ]]; then
    echo "ERROR: expected $trace after LFS pull." >&2
    exit 1
  fi

  if is_lfs_pointer "$trace"; then
    echo "ERROR: traces.jsonl is still a Git LFS pointer after pull." >&2
    echo "Check Hugging Face access/auth for the dataset and rerun." >&2
    exit 1
  fi

  echo "OK: traces.jsonl is downloaded."
  ls -lh "$trace"
  echo ""
  if [[ -f "$ROOT/docker-compose.prod.yml" ]] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'tracerator'; then
    echo "Prod container detected. Remount/rebuild so Tracerator can see the payload:"
    echo "  cd $ROOT && docker compose -f docker-compose.prod.yml up -d --force-recreate"
  else
    echo "Restart or refresh Tracerator so it remounts the baseline directory:"
    echo "  docker compose up --build"
  fi
  echo ""
  echo "If the UI still marks the source unavailable, confirm TRACERATOR_CC_WEKA_DIR"
  echo "points at: $dir"
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --clone)
      AUTO_CLONE=1
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

if ((${#ARGS[@]} == 0)); then
  usage
  exit 0
fi

target="${ARGS[0]}"
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
