#!/usr/bin/env bash
# ai-hats bootstrap — one-command project setup
#
# Quick start:
#   git clone --depth 1 git@github.com:muratovv/ai-hats.git /tmp/ai-hats && \
#     bash /tmp/ai-hats/scripts/bootstrap.sh -r assistant -p claude && \
#     rm -rf /tmp/ai-hats
#
# From local clone:
#   bash scripts/bootstrap.sh -r go-dev -p claude
#
set -euo pipefail

# -- Colors --

if [[ -t 1 ]]; then
    BOLD="\033[1m"
    DIM="\033[2m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    RED="\033[31m"
    CYAN="\033[36m"
    RESET="\033[0m"
else
    BOLD="" DIM="" GREEN="" YELLOW="" RED="" CYAN="" RESET=""
fi

ok()   { printf "  ${GREEN}✓${RESET} %-14s %s\n" "$1" "$2"; }
info() { printf "  ${DIM}…${RESET} %-14s %s\n" "$1" "$2"; }
err()  { printf "  ${RED}✗${RESET} %-14s %s\n" "$1" "$2" >&2; }

ROLE=""
PROVIDER=""
REPO_URL="git+ssh://git@github.com/muratovv/ai-hats.git"
LOCAL_ROOT=""

usage() {
    cat <<EOF
${BOLD}Usage:${RESET} bootstrap.sh [OPTIONS]

${BOLD}Options:${RESET}
  -r, --role <name>        Role (assistant, go-dev, sre, architect, ...)
  -p, --provider <name>    Provider (claude or gemini)
  --repo <git-url>         Custom git install URL
  --local <path>           Install from local clone instead of GitHub
  -h, --help               Show this help

${BOLD}Examples:${RESET}
  ${DIM}# Quick start (private repo via SSH)${RESET}
  git clone --depth 1 git@github.com:muratovv/ai-hats.git /tmp/ai-hats && \\
    bash /tmp/ai-hats/scripts/bootstrap.sh -r assistant -p claude && \\
    rm -rf /tmp/ai-hats

  ${DIM}# From local clone${RESET}
  bash scripts/bootstrap.sh -r go-dev -p claude
EOF
    exit 0
}

# -- Parse arguments --

while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--role)     ROLE="$2";      shift 2 ;;
        -p|--provider) PROVIDER="$2";  shift 2 ;;
        --repo)        REPO_URL="$2";  shift 2 ;;
        --local)       LOCAL_ROOT="$2"; shift 2 ;;
        -h|--help)     usage ;;
        *)  err "unknown" "$1"; usage ;;
    esac
done

# Auto-detect local clone: if script is run from file (not pipe) and pyproject.toml exists nearby
if [[ -z "$LOCAL_ROOT" && -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
    CANDIDATE="${SCRIPT_DIR}/.."
    if [[ -f "${CANDIDATE}/pyproject.toml" ]]; then
        LOCAL_ROOT="$(cd "$CANDIDATE" && pwd)"
    fi
fi

echo ""
printf "  ${BOLD}ai-hats bootstrap${RESET}\n"
echo ""

# -- 1. Check Python 3.11+ --

if ! command -v python3 &>/dev/null; then
    err "python" "not found — install Python 3.11+"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VERSION%%.*}
PY_MINOR=${PY_VERSION##*.}

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    err "python" "${PY_VERSION} — need 3.11+"
    exit 1
fi
ok "python" "${PY_VERSION}"

# -- 2. Create / activate venv --

if [[ ! -d .venv ]]; then
    info "venv" "creating .venv..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
ok "venv" ".venv activated"

# -- 3. Install ai-hats --

info "install" "python3 -m pip install ai-hats..."
python3 -m pip install --quiet --upgrade pip

if [[ -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/pyproject.toml" ]]; then
    python3 -m pip install --quiet "$LOCAL_ROOT"
    ok "install" "from local clone"
else
    python3 -m pip install --quiet "ai-hats @ ${REPO_URL}"
    ok "install" "from git"
fi

# -- 4. Verify installation --

if ! command -v ai-hats &>/dev/null; then
    err "verify" "ai-hats not found in PATH"
    echo "  Try: source .venv/bin/activate && ai-hats --version"
    exit 1
fi

INSTALLED_VERSION=$(ai-hats --version 2>&1 | head -1)
ok "version" "${INSTALLED_VERSION}"

# -- 5. Initialize project --

info "init" "setting up project..."

INIT_ARGS=(init)
[[ -n "$ROLE" ]]     && INIT_ARGS+=(--role "$ROLE")
[[ -n "$PROVIDER" ]] && INIT_ARGS+=(--provider "$PROVIDER")

ai-hats "${INIT_ARGS[@]}" >/dev/null

ok "init" "$(pwd)"
[[ -n "$PROVIDER" ]] && ok "provider" "${PROVIDER}"
[[ -n "$ROLE" ]]     && ok "role" "${ROLE}"

# -- Done --

echo ""
printf "  ${GREEN}${BOLD}ready${RESET}\n"
echo ""
printf "  ${CYAN}source .venv/bin/activate${RESET}\n"
printf "  ${CYAN}ai-hats status${RESET}\n"
echo ""
