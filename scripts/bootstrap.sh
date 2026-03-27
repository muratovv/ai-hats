#!/usr/bin/env bash
# ai-hats bootstrap — one-command setup
# Usage: curl -sSL <url>/bootstrap.sh | sh -s -- [--role <name>] [--provider <name>]
set -euo pipefail

ROLE=""
PROVIDER=""
REPO_URL="https://github.com/fedor/ai-hats.git"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --role)    ROLE="$2"; shift 2 ;;
        --provider) PROVIDER="$2"; shift 2 ;;
        --repo)    REPO_URL="$2"; shift 2 ;;
        *)         echo "Unknown option: $1"; exit 1 ;;
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
    source .venv/bin/activate
    echo "  venv: activated ✓"
fi

# 3. Install ai-hats
echo "  Installing ai-hats..."
pip install --quiet --upgrade pip
pip install --quiet "ai-hats @ git+${REPO_URL}" 2>/dev/null || {
    # Fallback: install from local if available
    if [[ -f pyproject.toml ]]; then
        pip install --quiet -e ".[dev]"
    else
        echo "ERROR: Cannot install ai-hats. Clone the repo first or provide --repo URL."
        exit 1
    fi
}
echo "  ai-hats: installed ✓"

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
