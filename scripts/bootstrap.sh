#!/usr/bin/env bash
# ai-hats bootstrap — one-command project setup
#
# Usage (from local clone):
#   git clone git@github.com:muratovv/ai-hats.git /tmp/ai-hats && \
#     bash /tmp/ai-hats/scripts/bootstrap.sh --role go-dev --provider claude
#
# Usage (from within ai-hats repo):
#   bash scripts/bootstrap.sh --role assistant
#
# Usage (if ai-hats already installed):
#   ai-hats init --role go-dev --provider claude
set -euo pipefail

ROLE=""
PROVIDER=""
REPO_URL="git+ssh://git@github.com/muratovv/ai-hats.git"

# Detect repo root relative to this script (works when run from a clone)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --role)     ROLE="$2"; shift 2 ;;
        --provider) PROVIDER="$2"; shift 2 ;;
        --repo)     REPO_URL="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bootstrap.sh [--role <name>] [--provider gemini|claude] [--repo <git-url>]"
            exit 0 ;;
        *)  echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== ai-hats bootstrap ==="

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    echo "ERROR: Python 3.11+ required (found $PY_VERSION)"
    exit 1
fi
echo "  Python: $PY_VERSION ✓"

# 2. Create venv if not in one
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ ! -d .venv ]]; then
        echo "  Creating venv..."
        python3 -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "  venv: activated ✓"
fi

# 3. Install ai-hats
echo "  Installing ai-hats..."
pip install --quiet --upgrade pip

if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
    # Install from local clone (fastest, works offline)
    pip install --quiet "$REPO_ROOT"
    echo "  ai-hats: installed from local clone ✓"
else
    # Install from git URL
    pip install --quiet "ai-hats @ ${REPO_URL}"
    echo "  ai-hats: installed from git ✓"
fi

# 4. Initialize project
INIT_ARGS="init"
if [[ -n "$ROLE" ]]; then
    INIT_ARGS="$INIT_ARGS --role $ROLE"
fi
if [[ -n "$PROVIDER" ]]; then
    INIT_ARGS="$INIT_ARGS --provider $PROVIDER"
fi

ai-hats $INIT_ARGS
echo ""
echo "=== ai-hats ready ==="
echo "  Run: ai-hats status"
if [[ -n "$ROLE" ]]; then
    echo "  Role: $ROLE"
fi
