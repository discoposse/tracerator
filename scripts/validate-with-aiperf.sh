#!/usr/bin/env bash
#
# scripts/validate-with-aiperf.sh
#
# Validate Tracerator-generated Mooncake-style trace.jsonl files using AIPerf.
#
# This ensures the outputs "play out" correctly:
#   - Static structure via `aiperf analyze-trace` (distributions, cache hit estimates from hash_ids)
#   - Full replay via `aiperf profile --custom-dataset-type mooncake_trace --fixed-schedule`
#     (exact timestamps/bursts + hash_id-driven KV cache prefix synthesis)
#
# The script is intentionally styled like the scripts in ../aiperf-toolkit for consistency.
#
# Full step-by-step instruction set (canonical guide): docs/VALIDATING_WITH_AIPERF.md
# (in the repo root). Read that first for context, manual commands, result interpretation,
# and handoff workflows.
#
# Usage (from repo root):
#   ./scripts/validate-with-aiperf.sh
#   ./scripts/validate-with-aiperf.sh --with-replay
#   ./scripts/validate-with-aiperf.sh --with-replay --subset 30
#   TRACE_FILE=Mooncake/traces/conversation_trace.jsonl ./scripts/validate-with-aiperf.sh --analyze-only
#   ENGINE=vllm MODEL="Qwen/Qwen3-0.6B" TOKENIZER="Qwen/Qwen3-0.6B" \
#     ./scripts/validate-with-aiperf.sh --with-replay --subset 20
#
# Requirements:
#   - aiperf installed (script searches common venvs + PATH, matching aiperf-toolkit conventions)
#   - For replay: a running inference server (Ollama on :11434 or vLLM on :8000/v1)
#   - Optional but recommended: jq (for manifest inspection)
#
# Exit codes follow toolkit convention:
#   0 = success (critical checks)
#   1 = failures
#   2 = warnings only
#
set -euo pipefail

# =============================================================================
# CONFIGURATION (override via env or flags)
# =============================================================================
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_TRACE="${REPO_ROOT}/Mooncake/trace_gen/examples/demo_conversation_1.5x.jsonl"
TRACE_FILE="${TRACE_FILE:-$DEFAULT_TRACE}"

SUBSET_N="${SUBSET_N:-50}"          # for replay (keeps runs fast on long-context traces)
RUN_ANALYZE=true
RUN_REPLAY=false
FIXED_SCHEDULE=true                 # --fixed-schedule honors trace timestamps (recommended for "play out")
QUICK_MODE=false
VERBOSE=false
NORMALIZE=false

ENGINE="${ENGINE:-vllm}"            # ollama | vllm  (affects default URL + health checks)
MODEL="${MODEL:-}"                  # will be set based on engine if empty
TOKENIZER="${TOKENIZER:-}"          # HF tokenizer ID; required for many models
URL="${URL:-}"                      # full base URL; script will default based on ENGINE

VENV_DIR="${VENV_DIR:-$HOME/venv}"
VLLM_VENV="${VLLM_VENV:-$HOME/.venv-vllm-metal}"
AIPERF_BIN="${AIPERF_BIN:-}"       # explicit path wins

# Report location (mirrors toolkit)
REPORT_DIR="${HOME}"
REPORT_PREFIX="tracerator-aiperf-validate"

# =============================================================================
# STATE
# =============================================================================
SCRIPT_NAME=$(basename "$0")
START_TIME=$(date +%s)

PASSED=0
WARNED=0
FAILED=0
declare -a RESULTS=()

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# =============================================================================
# HELPERS (adapted from aiperf-toolkit validate scripts for visual consistency)
# =============================================================================
log()   { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()    { echo -e "${GREEN}✓ PASS${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠ WARN${NC} $*"; }
fail()  { echo -e "${RED}✗ FAIL${NC} $*"; }
info()  { echo -e "${CYAN}→${NC} $*"; }

record() {
    local status="$1"; shift
    local msg="$*"
    case "$status" in
        PASS) PASSED=$((PASSED+1)); color=$GREEN; symbol="✓" ;;
        WARN) WARNED=$((WARNED+1)); color=$YELLOW; symbol="⚠" ;;
        FAIL) FAILED=$((FAILED+1)); color=$RED; symbol="✗" ;;
    esac
    RESULTS+=("${status}|${msg}")
    printf "  [${color}%-4s${NC}] %s\n" "$symbol" "$msg"
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

print_header() {
    echo ""
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$*${NC}"
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

print_command() {
    echo -e "${CYAN}$ ${NC}$*"
}

# Find aiperf binary (preference order matches aiperf-toolkit conventions)
find_aiperf() {
    if [[ -n "$AIPERF_BIN" && -x "$AIPERF_BIN" ]]; then
        echo "$AI PERF_BIN"
        return 0
    fi
    if [[ -x "$VENV_DIR/bin/aiperf" ]]; then
        echo "$VENV_DIR/bin/aiperf"
        return 0
    fi
    # Common alt venvs users create
    for cand in "$HOME/.venv/bin/aiperf" "$HOME/venv-aiperf/bin/aiperf" "$HOME/.local/bin/aiperf"; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done
    if command_exists aiperf; then
        echo "aiperf"
        return 0
    fi
    return 1
}

# Try to locate a sibling manifest for the trace (demo_*.jsonl -> demo_*.manifest.json, or manifest.json)
find_manifest() {
    local trace="$1"
    local dir base
    dir=$(dirname "$trace")
    base=$(basename "$trace" .jsonl)

    local candidates=(
        "${dir}/${base}.manifest.json"
        "${dir}/manifest.json"
        "${dir}/$(echo "$base" | sed 's/demo_//; s/_[0-9.]*x//').manifest.json"
    )
    for c in "${candidates[@]}"; do
        if [[ -f "$c" ]]; then
            echo "$c"
            return 0
        fi
    done
    echo ""
}

# Basic server liveness for the chosen engine (non-fatal for analyze-only)
check_server() {
    local url_base="$1"
    local engine="$2"
    local code

    if [[ "$engine" == "vllm" ]]; then
        # vLLM OpenAI compat
        code=$(curl -s --max-time 4 -o /dev/null -w "%{http_code}" "${url_base}/v1/models" || echo "000")
        if [[ "$code" == "200" ]]; then
            return 0
        fi
        # Some vLLM setups expose /health
        code=$(curl -s --max-time 4 -o /dev/null -w "%{http_code}" "${url_base}/health" || echo "000")
        [[ "$code" == "200" ]]
    else
        # Ollama
        code=$(curl -s --max-time 4 -o /dev/null -w "%{http_code}" "${url_base}/api/version" || echo "000")
        [[ "$code" == "200" ]]
    fi
}

# =============================================================================
# ARGUMENT PARSING
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --with-replay|--replay|-r)
            RUN_REPLAY=true
            shift
            ;;
        --analyze-only|--no-replay)
            RUN_REPLAY=false
            shift
            ;;
        --subset|-n)
            SUBSET_N="$2"
            shift 2
            ;;
        --fixed-schedule)
            FIXED_SCHEDULE=true
            shift
            ;;
        --no-fixed-schedule)
            FIXED_SCHEDULE=false
            shift
            ;;
        --quick|-q)
            QUICK_MODE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --engine)
            ENGINE="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --tokenizer)
            TOKENIZER="$2"
            shift 2
            ;;
        --url)
            URL="$2"
            shift 2
            ;;
        --normalize)
            NORMALIZE=true
            shift
            ;;
        -h|--help)
            cat <<EOF
Usage: $SCRIPT_NAME [options]

Validate Tracerator (Mooncake-format) trace JSONL files with AIPerf.

Options:
  --with-replay, -r         Run full trace replay (aiperf profile + mooncake_trace)
  --analyze-only            Only run aiperf analyze-trace (default behavior if no server)
  --subset N, -n N          Use only first N lines for replay (default: 50). Full traces are huge.
  --fixed-schedule          Honor exact timestamps in trace (default). Use for burst/timing fidelity.
  --no-fixed-schedule       Ignore captured timestamps; drive server at max speed for capacity test.
  --quick, -q               Skip heavy steps.
  --verbose, -v             Extra output.
  --engine <ollama|vllm>    Target engine (affects default URL and health checks).
  --model <name>            Model identifier for the server.
  --tokenizer <hf-repo>     Hugging Face tokenizer ID (strongly recommended for accuracy).
  --url <base-url>          Override server base URL (e.g. http://localhost:8000 or http://localhost:11434).
  --normalize               Run the trace through normalize_trace_for_aiperf first (fixes legacy traces that have len(hash_ids) != ceil(input_length/512)).
                            Writes a sibling .normalized.jsonl and uses that for the run.

Environment variables (all overridable):
  TRACE_FILE=...            Path to the .jsonl to validate (default: small demo example)
  SUBSET_N=...              Same as --subset
  ENGINE=... MODEL=... TOKENIZER=... URL=...
  VENV_DIR=...              AIPerf venv (default ~/venv)
  AIPERF_BIN=...            Explicit aiperf path

Examples:
  ./scripts/validate-with-aiperf.sh
  ./scripts/validate-with-aiperf.sh --with-replay --subset 30
  TRACE_FILE=Mooncake/arxiv-trace/mooncake_trace.jsonl SUBSET_N=100 \\
      ./scripts/validate-with-aiperf.sh --with-replay --engine vllm --fixed-schedule

  # Using a generated artifact + your aiperf-toolkit venv
  TRACE_FILE=~/Downloads/extended_conversation_x1.5_s42/trace.jsonl \\
      VENV_DIR=~/venv ./scripts/validate-with-aiperf.sh --with-replay

See also:
  - docs/VALIDATING_WITH_AIPERF.md in this repo (the canonical instruction set)
  - the aiperf-toolkit (https://github.com/discoposse/aiperf-toolkit) for full stack setup.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# =============================================================================
# DEFAULTS PER ENGINE (after parsing)
# =============================================================================
if [[ -z "$URL" ]]; then
    if [[ "$ENGINE" == "vllm" ]]; then
        URL="http://localhost:8000"
    else
        URL="http://localhost:11434"
    fi
fi

# Optional one-shot normalization (great for legacy / third-party traces)
if [[ "${NORMALIZE:-false}" == "true" || " $* " == *" --normalize "* ]]; then
    NORM_OUT="${TRACE_FILE%.jsonl}.normalized.jsonl"
    if python3 -c "
import sys
sys.path.insert(0, '${REPO_ROOT}/Mooncake/trace_gen')
from generator import normalize_trace_for_aiperf, save_trace, load_trace
reqs = load_trace('${TRACE_FILE}')
clean = normalize_trace_for_aiperf(reqs)
save_trace(clean, '${NORM_OUT}')
print('Normalized to ${NORM_OUT}')
" 2>/dev/null; then
        echo "Using normalized trace for this run: $NORM_OUT"
        TRACE_FILE="$NORM_OUT"
    else
        echo "WARNING: Could not auto-normalize (python module not importable). Proceeding with original trace."
    fi
fi


if [[ -z "$MODEL" ]]; then
    if [[ "$ENGINE" == "vllm" ]]; then
        MODEL="Qwen/Qwen3-0.6B"   # small, fast for trace replay smoke tests; long-context traces may need bigger or --use-server-token-count
    else
        MODEL="granite4:350m"
    fi
fi

if [[ -z "$TOKENIZER" ]]; then
    if [[ "$ENGINE" == "vllm" ]]; then
        TOKENIZER="Qwen/Qwen3-0.6B"
    else
        TOKENIZER="ibm-granite/granite-4.0-micro"
    fi
fi

# =============================================================================
# MAIN STEPS
# =============================================================================
main() {
    print_header "Tracerator + AIPerf Validation"
    log "Repo root   : $REPO_ROOT"
    log "Trace file  : $TRACE_FILE"
    log "Engine      : $ENGINE"
    log "URL         : $URL"
    log "Model       : $MODEL"
    log "Tokenizer   : $TOKENIZER"
    log "Subset (replay): $SUBSET_N"
    log "Fixed schedule : $(if $FIXED_SCHEDULE; then echo 'yes (play exact timestamps)'; else echo 'no (max speed)'; fi)"
    log "Analyze       : $RUN_ANALYZE"
    log "Replay        : $RUN_REPLAY"

    if [[ ! -f "$TRACE_FILE" ]]; then
        record "FAIL" "Trace file not found: $TRACE_FILE"
        echo "Hint: TRACE_FILE=... or cd to repo root."
        exit 1
    fi
    record "PASS" "Trace file exists ($(wc -l < "$TRACE_FILE" | tr -d ' ') lines)"

    # aiperf discovery
    local aiperf
    if ! aiperf=$(find_aiperf); then
        record "FAIL" "aiperf not found in PATH or common venvs ($VENV_DIR etc.)"
        info "Install via aiperf-toolkit or: python3 -m venv ~/venv && ~/venv/bin/pip install aiperf"
        info "Then re-run with VENV_DIR=~/venv or AIPERF_BIN=... "
        exit 1
    fi
    record "PASS" "AIPerf found: $aiperf"
    local ver
    ver=$("$aiperf" --version 2>/dev/null | head -1 || echo "unknown")
    record "PASS" "AIPerf version: $ver"

    # jq is nice for manifest + pretty output (run_trace_ui.sh already promotes it)
    local have_jq=false
    if command_exists jq; then
        have_jq=true
        record "PASS" "jq available (great for manifests)"
    else
        record "WARN" "jq not found — manifest cross-checks and pretty JSON will be limited"
    fi

    # Manifest discovery (for provenance + expected stats)
    local manifest
    manifest=$(find_manifest "$TRACE_FILE")
    if [[ -n "$manifest" ]]; then
        record "PASS" "Found manifest: $manifest"
        if $have_jq; then
            local n_reqs median_in approx_hit
            n_reqs=$(jq -r '.output_stats.n_requests // .n_requests // "N/A"' "$manifest" 2>/dev/null || echo "N/A")
            median_in=$(jq -r '.output_stats.median_input // "N/A"' "$manifest" 2>/dev/null || echo "N/A")
            approx_hit=$(jq -r '.output_stats.approx_cache_hit_ratio // "N/A"' "$manifest" 2>/dev/null || echo "N/A")
            info "Manifest summary: n=$n_reqs, median_in=$median_in, hit_ratio≈$approx_hit"
        fi
    else
        record "WARN" "No manifest found next to trace (ok for raw/original traces)"
    fi

    # 1. STATIC ANALYSIS (always, cheap, no server needed)
    print_header "1. STATIC TRACE ANALYSIS (aiperf analyze-trace)"
    local analyze_out
    analyze_out="${REPORT_DIR}/${REPORT_PREFIX}-analysis-$(date +%Y%m%d-%H%M%S).json"
    local analyze_cmd=(
        "$aiperf" analyze-trace
        "$TRACE_FILE"
        --output-file "$analyze_out"
        --block-size 512
    )
    if $VERBOSE; then
        echo "Command: ${analyze_cmd[*]}"
    fi
    if "${analyze_cmd[@]}" 2>&1; then
        record "PASS" "analyze-trace completed → $analyze_out"
        if [[ -f "$analyze_out" && $have_jq ]]; then
            echo "    Top-level keys (jq):"
            jq -r 'keys | .[]' "$analyze_out" 2>/dev/null | sed 's/^/      - /' | head -10 || true
        fi
    else
        record "FAIL" "aiperf analyze-trace failed (see above)"
    fi

    # 2. REPLAY (optional, requires server)
    local replay_subset=""
    local replay_cmd=()
    if $RUN_REPLAY; then
        print_header "2. TRACE REPLAY (aiperf profile + mooncake_trace)"

        # Create a safe subset (critical for 12k–23k line real traces)
        replay_subset="/tmp/tracerator-validate-subset-$$.jsonl"
        head -n "$SUBSET_N" "$TRACE_FILE" > "$replay_subset"
        local subset_lines
        subset_lines=$(wc -l < "$replay_subset" | tr -d ' ')
        record "PASS" "Created replay subset: $subset_lines lines → $replay_subset"

        # Server health (we still attempt the replay so the user sees the real error + the exact command;
        # the post-run handling turns "no server" into a clean WARN instead of FAIL)
        if check_server "$URL" "$ENGINE"; then
            record "PASS" "Inference server responding at $URL"
        else
            info "Server not detected at $URL — will attempt replay anyway (expect a connection error that we treat as non-fatal WARN)"
            info "Quick start examples (in another terminal):"
            if [[ "$ENGINE" == "vllm" ]]; then
                info "  source $VLLM_VENV/bin/activate && vllm serve $MODEL --port 8000"
            else
                info "  ollama serve  # (or launch Ollama.app) && ollama pull $MODEL"
            fi
        fi

        # Build the profile command
        replay_cmd=(
            "$aiperf" profile
            --model "$MODEL"
            --endpoint-type chat
            --streaming
            --url "$URL"
            --input-file "$replay_subset"
            --custom-dataset-type mooncake_trace
            --tokenizer "$TOKENIZER"
        )
        if $FIXED_SCHEDULE; then
            replay_cmd+=(--fixed-schedule)
        else
            replay_cmd+=(--no-fixed-schedule)
            replay_cmd+=(--concurrency 4)   # reasonable default when not using trace timing
        fi

        # Long traces often benefit from this when tokenizer is picky
        # (user can remove if it works)
        # replay_cmd+=(--use-server-token-count)

        if $VERBOSE; then
            echo "Command:"
            print_command "${replay_cmd[*]}"
            echo ""
        fi

        log "Running replay (this will take time proportional to subset size + generation)..."
        echo ""
        set +e
        "${replay_cmd[@]}" 2>&1
        replay_status=$?
        set -e
        if (( replay_status == 0 )); then
            record "PASS" "Trace replay completed successfully"
            info "AIPerf artifacts (if any) written to ./artifacts or the dir you specified."
            info "Run 'aiperf plot' afterwards for visualizations."
        else
            # Distinguish "no server" (common/expected during dev) from real trace problems
            if ! check_server "$URL" "$ENGINE"; then
                record "WARN" "Replay command failed because no server was listening at $URL (this is expected until you start one)"
            else
                record "FAIL" "Trace replay failed (see output above; possible context limit, tokenizer, or trace content issue)"
                info "Common fixes:"
                info "  - Add --use-server-token-count"
                info "  - Use --synthesis-max-isl (if your aiperf supports it) or a smaller model / subset"
                info "  - For capacity (ignore timing): --no-fixed-schedule --concurrency N"
            fi
        fi

        # Always clean the temp subset for non-verbose runs
        if ! $VERBOSE; then
            rm -f "$replay_subset" 2>/dev/null || true
        fi
    else
        info "Replay skipped (pass --with-replay to exercise the full 'play out' path)"
    fi

    # =============================================================================
    # REPORT
    # =============================================================================
    local end_time duration
    end_time=$(date +%s)
    duration=$(( end_time - START_TIME ))

    local report_file="${REPORT_DIR}/${REPORT_PREFIX}-$(date +%Y%m%d-%H%M%S).txt"

    {
        echo "Tracerator + AIPerf Validation Report"
        echo "Generated : $(date)"
        echo "Duration  : ${duration}s"
        echo "Host      : $(hostname)"
        echo "User      : $USER"
        echo ""
        echo "=== CONFIG ==="
        echo "Trace file : $TRACE_FILE"
        echo "Engine     : $ENGINE"
        echo "URL        : $URL"
        echo "Model      : $MODEL"
        echo "Tokenizer  : $TOKENIZER"
        echo "Subset N   : $SUBSET_N"
        echo "Fixed-sched: $FIXED_SCHEDULE"
        echo "AIPerf     : $aiperf ($ver)"
        echo ""
        echo "=== SUMMARY ==="
        echo "Passed : $PASSED"
        echo "Warned : $WARNED"
        echo "Failed : $FAILED"
        echo ""
        echo "=== DETAILED RESULTS ==="
        for line in "${RESULTS[@]}"; do
            IFS='|' read -r status msg <<< "$line"
            printf "%-6s %s\n" "[$status]" "$msg"
        done
        echo ""
        echo "=== COMMANDS USED ==="
        echo "# Static analysis (always safe):"
        echo "${analyze_cmd[*]}"
        echo ""
        if [[ ${#replay_cmd[@]} -gt 0 ]]; then
            echo "# Replay (exact timestamps + hash_id KV simulation):"
            echo "${replay_cmd[*]}"
            echo ""
            echo "# Larger / full run example (edit paths):"
            echo "TRACE_FILE=your_trace.jsonl SUBSET_N=200 ENGINE=vllm \\"
            echo "  ./scripts/validate-with-aiperf.sh --with-replay --fixed-schedule"
        else
            echo "# To replay (recommended for 'play out the traces properly'):"
            echo "TRACE_FILE=\"$TRACE_FILE\" ./scripts/validate-with-aiperf.sh --with-replay --subset 50"
        fi
        echo ""
        echo "=== NEXT STEPS / TIPS ==="
        echo "• Compare aiperf analyze output + manifest.json against your expectations."
        echo "• Use --fixed-schedule (default) to validate bursty timing and arrival patterns."
        echo "• Use --no-fixed-schedule + --concurrency to test the same workload mix at max server capacity."
        echo "• Real production traces (12k–23k lines) need --subset or they will take forever + hit context limits."
        echo "• The hash_ids in the trace drive realistic prefix-cache hit simulation inside AIPerf."
        echo "• After a replay run: aiperf plot   (or aiperf plot --dashboard)"
        echo "• See AIPerf docs: trace-replay-with-mooncake-traces"
        echo "• For the full instruction set: docs/VALIDATING_WITH_AIPERF.md (in this repo)"
        echo "• For full local stack (Ollama/vLLM + aiperf): https://github.com/discoposse/aiperf-toolkit"
        echo ""
        echo "=== END OF REPORT ==="
    } | tee "$report_file"

    echo ""
    echo -e "${BOLD}Report saved to:${NC} $report_file"
    if [[ -f "$analyze_out" ]]; then
        echo -e "Analysis JSON : $analyze_out"
    fi

    echo ""
    print_header "VALIDATION SUMMARY"
    echo -e "  ${GREEN}Passed${NC}: $PASSED    ${YELLOW}Warned${NC}: $WARNED    ${RED}Failed${NC}: $FAILED"

    if (( FAILED > 0 )); then
        echo -e "${RED}Validation completed with failures.${NC}"
        exit 1
    elif (( WARNED > 0 )); then
        echo -e "${YELLOW}Validation completed with warnings.${NC}"
        exit 2
    else
        echo -e "${GREEN}All critical checks passed. Trace is ready for AIPerf replay.${NC}"
        exit 0
    fi
}

main "$@"
