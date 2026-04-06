#!/usr/bin/env bash
# ai-hats bootstrap — one-command project setup
#
# Remote (curl | bash):
#   curl -fsSL https://raw.githubusercontent.com/muratovv/ai-hats/master/scripts/bootstrap.sh | bash -s -- -r assistant -p claude
#
# Local (from ai-hats clone):
#   bash scripts/bootstrap.sh -r go-dev -p claude
#
set -euo pipefail

ROLE=""
PROVIDER=""
REPO_URL="git+ssh://git@github.com/muratovv/ai-hats.git"
LOCAL_ROOT=""

usage() {
    cat <<'EOF'
Usage: bootstrap.sh [OPTIONS]

Options:
  -r, --role <name>        Role to apply (assistant, go-dev, sre, architect, ...)
  -p, --provider <name>    Provider (claude or gemini)
  --repo <git-url>         Custom git install URL
  --local <path>           Install from local clone instead of GitHub
  -h, --help               Show this help

Examples:
  # Remote install with curl
  curl -fsSL https://raw.githubusercontent.com/muratovv/ai-hats/master/scripts/bootstrap.sh \
    | bash -s -- -r assistant -p claude

  # Local clone install
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
        *)  echo "ERROR: Unknown option: $1"; usage ;;
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

echo "=== ai-hats bootstrap ==="

# -- 1. Check Python 3.11+ --

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VERSION%%.*}
PY_MINOR=${PY_VERSION##*.}

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    echo "ERROR: Python 3.11+ required (found ${PY_VERSION})"
    exit 1
fi
echo "  Python: ${PY_VERSION} ✓"

# -- 2. Create / activate venv --

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ ! -d .venv ]]; then
        echo "  Creating .venv..."
        python3 -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "  venv: activated ✓"
fi

# -- 3. Install ai-hats --

echo "  Installing ai-hats..."
pip install --quiet --upgrade pip

if [[ -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/pyproject.toml" ]]; then
    pip install --quiet "$LOCAL_ROOT"
    echo "  ai-hats: installed from local clone ✓"
else
    pip install --quiet "ai-hats @ ${REPO_URL}"
    echo "  ai-hats: installed from ${REPO_URL} ✓"
fi

# -- 4. Verify installation --

if ! command -v ai-hats &>/dev/null; then
    echo "ERROR: ai-hats not found in PATH after install."
    echo "  Try: source .venv/bin/activate && ai-hats --version"
    exit 1
fi

INSTALLED_VERSION=$(ai-hats --version 2>&1 | head -1)
echo "  ${INSTALLED_VERSION} ✓"

# -- 5. Initialize project --

INIT_ARGS=(init)
[[ -n "$ROLE" ]]     && INIT_ARGS+=(--role "$ROLE")
[[ -n "$PROVIDER" ]] && INIT_ARGS+=(--provider "$PROVIDER")

ai-hats "${INIT_ARGS[@]}"

# -- Done --

echo ""
echo "=== ai-hats ready ==="
echo ""
echo "  Activate venv:  source .venv/bin/activate"
echo "  Check status:   ai-hats status"
[[ -n "$ROLE" ]]     && echo "  Role:            ${ROLE}"
[[ -n "$PROVIDER" ]] && echo "  Provider:        ${PROVIDER}"
