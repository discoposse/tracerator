#!/usr/bin/env bash
# Deploy an exact git revision of Tracerator over SSH.
#
# Required environment:
#   DEPLOY_HOST   SSH host
#   DEPLOY_DIR    remote checkout directory, for example /opt/tracerator
#   DEPLOY_URL    public URL used for the smoke check
#
# Optional environment:
#   DEPLOY_USER   default: root
#   DEPLOY_BARE   default: ${DEPLOY_DIR}.git
#
# Usage:
#   DEPLOY_HOST=host.example DEPLOY_DIR=/opt/tracerator DEPLOY_URL=https://tracerator.example \
#     ./scripts/deploy-prod.sh              # deploy origin/main
#   ./scripts/deploy-prod.sh HEAD           # deploy current commit
#   ./scripts/deploy-prod.sh abc1234        # deploy a specific SHA
#   ./scripts/deploy-prod.sh --bootstrap    # one-time: bare repo + post-receive hook
#
# Only committed files are shipped (via git archive). Untracked local files
# (.venv, artifacts, .env) never reach the host.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_HOST="${DEPLOY_HOST:-}"
DEPLOY_DIR="${DEPLOY_DIR:-}"
DEPLOY_URL="${DEPLOY_URL:-}"
DEPLOY_BARE="${DEPLOY_BARE:-}"
REMOTE="${DEPLOY_USER}@${DEPLOY_HOST}"

usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
  exit "${1:-0}"
}

require_deploy_env() {
  local missing=()
  [[ -n "$DEPLOY_HOST" ]] || missing+=("DEPLOY_HOST")
  [[ -n "$DEPLOY_DIR" ]] || missing+=("DEPLOY_DIR")
  [[ -n "$DEPLOY_URL" ]] || missing+=("DEPLOY_URL")
  if ((${#missing[@]})); then
    echo "Set ${missing[*]} before deploying. This script has no default host." >&2
    exit 1
  fi
  if [[ -z "$DEPLOY_BARE" ]]; then
    DEPLOY_BARE="${DEPLOY_DIR}.git"
  fi
  REMOTE="${DEPLOY_USER}@${DEPLOY_HOST}"
}

ssh_remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE" "$@"
}

require_tools() {
  local missing=()
  for cmd in git ssh docker; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if ((${#missing[@]})); then
    echo "Missing required tools: ${missing[*]}" >&2
    exit 1
  fi
}

warn_if_dirty() {
  local ref="$1"
  if [[ "$ref" == "HEAD" ]] && [[ -n "$(git status --porcelain)" ]]; then
    echo "Working tree is dirty. Only committed files are deployed." >&2
  fi
}

resolve_ref() {
  git rev-parse --verify "$1^{commit}"
}

bootstrap_git_remote() {
  require_deploy_env
  echo "Bootstrapping git-backed deploy on ${REMOTE}:${DEPLOY_DIR} ..."
  ssh_remote "bash -s" <<EOF
set -euo pipefail
DEPLOY_DIR='${DEPLOY_DIR}'
DEPLOY_BARE='${DEPLOY_BARE}'

mkdir -p "\$(dirname "\$DEPLOY_DIR")"
if [[ -d "\$DEPLOY_DIR" && ! -d "\$DEPLOY_DIR/.git" ]]; then
  stamp="\$(date -u +%Y%m%dT%H%M%SZ)"
  backup="\${DEPLOY_DIR}.pre-git-\${stamp}"
  echo "Moving existing flat copy to \$backup"
  mv "\$DEPLOY_DIR" "\$backup"
fi

if [[ ! -d "\$DEPLOY_BARE" ]]; then
  git init --bare "\$DEPLOY_BARE"
fi

cat > "\$DEPLOY_BARE/hooks/post-receive" <<HOOK
#!/usr/bin/env bash
set -euo pipefail
DEPLOY_DIR="${DEPLOY_DIR}"
DEPLOY_BARE="${DEPLOY_BARE}"
COMPOSE_FILE="docker-compose.prod.yml"
while read -r oldrev newrev refname; do
  branch="\${refname#refs/heads/}"
  [[ "\$branch" == "main" || "\$branch" == "master" ]] || continue
  echo "Deploying \$branch (\$newrev) to \$DEPLOY_DIR"
  mkdir -p "\$DEPLOY_DIR"
  GIT_WORK_TREE="\$DEPLOY_DIR" GIT_DIR="\$DEPLOY_BARE" git checkout -f "\$branch"
  printf '%s\\n' "\$newrev" "\$branch" "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "\$DEPLOY_DIR/.deployed-revision"
  cd "\$DEPLOY_DIR"
  docker compose -f "\$COMPOSE_FILE" up -d --build --force-recreate
  docker exec tracerator python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8008/")'
  echo "Deploy complete."
done
HOOK
chmod +x "\$DEPLOY_BARE/hooks/post-receive"
echo "Bare repo ready at \$DEPLOY_BARE"
EOF

  if git remote get-url prod >/dev/null 2>&1; then
    git remote set-url prod "${REMOTE}:${DEPLOY_BARE}"
  else
    git remote add prod "${REMOTE}:${DEPLOY_BARE}"
  fi
  echo "Local git remote 'prod' → ${REMOTE}:${DEPLOY_BARE}"
  echo "First deploy:  git push -u prod main"
}

deploy_archive() {
  require_deploy_env
  local ref="$1"
  local sha
  sha="$(resolve_ref "$ref")"
  local short
  short="$(git rev-parse --short "$sha")"
  local subject
  subject="$(git log -1 --pretty=%s "$sha")"

  warn_if_dirty "$ref"

  echo "Deploying ${short} — ${subject}"
  echo "  host: ${REMOTE}:${DEPLOY_DIR}"
  echo "  url:  ${DEPLOY_URL}"

  ssh_remote "bash -s" <<EOF
set -euo pipefail
mkdir -p '${DEPLOY_DIR}'
if [[ -e '${DEPLOY_DIR}/.git' ]]; then
  echo "${DEPLOY_DIR} is a git work tree (after --bootstrap)." >&2
  echo "Use: git push prod main" >&2
  exit 1
fi
find '${DEPLOY_DIR}' -mindepth 1 -maxdepth 1 ! -name '.env' -exec rm -rf {} +
EOF

  echo "Streaming git archive ${short} ..."
  git archive --format=tar "$sha" | ssh_remote "tar -xf - -C '${DEPLOY_DIR}'"

  ssh_remote "bash -s" <<EOF
set -euo pipefail
cd '${DEPLOY_DIR}'
printf '%s\n' '${sha}' '${ref}' '$(date -u +%Y-%m-%dT%H:%M:%SZ)' > .deployed-revision
echo "Building image and recreating tracerator ..."
docker compose -f docker-compose.prod.yml up -d --build --force-recreate
docker ps --filter name=tracerator --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker exec tracerator python -c 'import urllib.request; print("in-container", urllib.request.urlopen("http://127.0.0.1:8008/").status)'
EOF

  echo "Checking public site ..."
  local html
  html="$(curl -fsS --max-time 30 "${DEPLOY_URL}")"
  if ! grep -q 'Tracerator' <<<"$html"; then
    echo "Public URL responded but did not look like Tracerator HTML." >&2
    exit 1
  fi
  echo "OK — ${DEPLOY_URL} is serving ${short}"
}

main() {
  require_tools
  local arg="${1:-origin/main}"
  case "$arg" in
    -h|--help) usage 0 ;;
    --bootstrap)
      bootstrap_git_remote
      ;;
    *)
      if [[ "$arg" == "origin/main" ]] && ! git rev-parse --verify origin/main >/dev/null 2>&1; then
        echo "origin/main not found locally; fetching..."
        git fetch origin main
      fi
      deploy_archive "$arg"
      ;;
  esac
}

main "$@"
