#!/usr/bin/env bash
# Convenience launcher for Tracerator UI (background docker compose).
# Canonical launch/relaunch from the repo root: docker compose up [--build]

set -euo pipefail

echo "==> Tracerator"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

ensure_jq() {
  if command_exists jq; then
    echo "✓ jq available (great for inspecting trace.jsonl)"
    return 0
  fi

  echo "⚠️  jq not found — strongly recommended for working with generated trace.jsonl"
  echo "   (every tracerator-*.zip now includes a README.txt with examples)"

  if [[ "${OSTYPE:-}" == darwin* ]]; then
    if command_exists brew; then
      echo "Installing jq via Homebrew..."
      brew install jq
      return 0
    else
      echo "  (Homebrew not found — get it from https://brew.sh/)"
    fi
  elif command_exists apt-get; then
    echo "Installing jq via apt-get..."
    sudo apt-get update -qq && sudo apt-get install -y jq
    return 0
  fi

  echo "  Continuing without jq (you can install it later)."
}

ensure_jq
echo ""

if ! command_exists docker; then
  echo "ERROR: 'docker' command not found."
  echo "Please install Docker Desktop (macOS/Windows) or Docker Engine (Linux)."
  echo "https://docs.docker.com/get-docker/"
  exit 1
fi

if ! docker info > /dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running or not reachable."
  echo "Start Docker Desktop and try again."
  exit 1
fi

if ! docker compose version > /dev/null 2>&1; then
  echo "⚠️  'docker compose' (Compose V2) not detected."
  echo "   The plugin is usually bundled with recent Docker Desktop."
  echo "   Falling back to legacy 'docker-compose' if present..."
  if ! command_exists docker-compose; then
    echo "ERROR: Neither 'docker compose' nor 'docker-compose' available."
    exit 1
  fi
  echo "Using docker-compose (legacy)..."
  docker-compose up -d --build --force-recreate
else
  docker compose up -d --build --force-recreate
fi

echo "Open http://localhost:8008 for the UI."
echo "Canonical launch/relaunch from the repo root: docker compose up [--build]"
echo "This script runs docker compose up -d --build in the background."
echo ""
echo "AIPerf validation is available in the Trace Generator UI (runs inside the container)."
echo "For CLI validation from the host:"
echo "  ./scripts/validate-with-aiperf.sh --analyze-only"
echo "  ./scripts/validate-with-aiperf.sh --with-replay --subset 30"
echo "See docs/how-to/validate-with-aiperf.md"
echo ""
echo "For a complete local AIPerf + vLLM/Ollama stack (setup scripts, venvs, LMCache):"
echo "  https://github.com/discoposse/aiperf-toolkit"
