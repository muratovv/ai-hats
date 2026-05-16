#!/usr/bin/env bash
# ai-hats bootstrap — convenience wrapper for first-time setup (HATS-333/336).
#
# Pipeline: install-launcher.sh → `ai-hats self update` → `ai-hats self init`.
# Idempotent — safe to re-run; launcher's self update handles existing venv.
#
# Quick start (fresh tmp each run, auto-cleanup):
#   TMP=$(mktemp -d) && git clone --depth 1 git@github.com:muratovv/ai-hats.git "$TMP" && \
#     bash "$TMP/scripts/bootstrap.sh" -r assistant -p claude; rm -rf "$TMP"
#
# From local clone:
#   bash scripts/bootstrap.sh -r go-dev -p claude
set -euo pipefail

# -- Colors --
if [[ -t 1 ]]; then
    BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; CYAN="\033[36m"; RESET="\033[0m"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; RESET=""
fi
ok()   { printf "  ${GREEN}✓${RESET} %-14s %s\n" "$1" "$2"; }
info() { printf "  ${DIM}…${RESET} %-14s %s\n" "$1" "$2"; }
err()  { printf "  ${RED}✗${RESET} %-14s %s\n" "$1" "$2" >&2; }

ROLE=""
PROVIDER=""
REPO_URL=""
LOCAL_ROOT=""

usage() {
    cat <<EOF
${BOLD}Usage:${RESET} bootstrap.sh [OPTIONS]

${BOLD}Options:${RESET}
  -r, --role <name>        Role (assistant, go-dev, sre, architect, ...)
  -p, --provider <name>    Provider (claude or gemini)
  --repo <git-url>         Custom git install URL (overrides default)
  --local <path>           Install from local clone instead of GitHub
  -h, --help               Show this help

${BOLD}Pipeline:${RESET}
  1. install-launcher.sh  → ~/.local/bin/ai-hats (one-time per host).
  2. ai-hats self update  → create venv + install ai-hats.
  3. ai-hats self init -r ... -p ...  (if -r/-p given; else printed as next step).

${BOLD}Notes:${RESET}
  Idempotent — safe to re-run. Existing ai-hats.yaml is preserved by init.

${BOLD}Examples:${RESET}
  ${DIM}# Quick start — fresh tmp each run${RESET}
  TMP=\$(mktemp -d) && git clone --depth 1 git@github.com:muratovv/ai-hats.git "\$TMP" && \\
    bash "\$TMP/scripts/bootstrap.sh" -r assistant -p claude; rm -rf "\$TMP"

  ${DIM}# From local clone${RESET}
  bash scripts/bootstrap.sh -r go-dev -p claude
EOF
    exit 0
}

# -- Parse arguments --
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--role)     ROLE="$2";       shift 2 ;;
        -p|--provider) PROVIDER="$2";   shift 2 ;;
        --repo)        REPO_URL="$2";   shift 2 ;;
        --local)       LOCAL_ROOT="$2"; shift 2 ;;
        -h|--help)     usage ;;
        *)  err "unknown" "$1"; usage ;;
    esac
done

# Auto-detect local clone if invoked from one (and --local not given)
SCRIPT_DIR=""
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
if [[ -n "$SCRIPT_PATH" && "$SCRIPT_PATH" != "bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd || true)"
fi
if [[ -z "$LOCAL_ROOT" && -n "$SCRIPT_DIR" ]]; then
    CANDIDATE="$(cd "$SCRIPT_DIR/.." 2>/dev/null && pwd || true)"
    if [[ -n "$CANDIDATE" && -f "$CANDIDATE/pyproject.toml" ]]; then
        LOCAL_ROOT="$CANDIDATE"
    fi
fi

echo ""
printf "  ${BOLD}ai-hats bootstrap${RESET}\n"
echo ""

# -- 1. Python precondition (launcher will need it for `python -m venv`) --
if ! command -v python3 >/dev/null 2>&1; then
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

# -- 2. Install launcher --
LAUNCHER_DEST="${AI_HATS_LAUNCHER_DEST:-$HOME/.local/bin/ai-hats}"
INSTALLER=""
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/install-launcher.sh" ]]; then
    INSTALLER="$SCRIPT_DIR/install-launcher.sh"
fi
if [[ -n "$INSTALLER" ]]; then
    info "launcher" "running $INSTALLER"
    AI_HATS_LAUNCHER_DEST="$LAUNCHER_DEST" bash "$INSTALLER" >/dev/null
    ok "launcher" "$LAUNCHER_DEST"
else
    err "launcher" "install-launcher.sh not found next to bootstrap.sh"
    err "hint" "clone the ai-hats repo and re-run from scripts/"
    exit 1
fi

if [[ ! -x "$LAUNCHER_DEST" ]]; then
    err "launcher" "expected $LAUNCHER_DEST after install; not found/executable"
    exit 1
fi

# -- 3. Compute AI_HATS_REPO_URL for self update --
if [[ -z "$REPO_URL" && -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/pyproject.toml" ]]; then
    REPO_URL="$LOCAL_ROOT"
    info "source" "local clone: $LOCAL_ROOT"
elif [[ -n "$REPO_URL" ]]; then
    info "source" "custom: $REPO_URL"
fi
# else: leave empty → launcher falls back to its built-in default git URL

# -- 4. ai-hats self update (creates venv + installs) --
info "install" "ai-hats self update (creates venv at <ai_hats_dir>/.venv)"
if [[ -n "$REPO_URL" ]]; then
    AI_HATS_REPO_URL="$REPO_URL" "$LAUNCHER_DEST" self update
else
    "$LAUNCHER_DEST" self update
fi

# -- 5. Detect re-run --
if [[ -f "ai-hats.yaml" || -d ".agent" ]]; then
    info "re-run" "existing project detected — init will preserve customizations"
fi

# -- 6. Initialize project (only if -r/-p given) --
if [[ -n "$ROLE" || -n "$PROVIDER" ]]; then
    # HATS-242: `init` is nested under `self`.
    INIT_ARGS=(self init)
    [[ -n "$ROLE" ]]     && INIT_ARGS+=(--role "$ROLE")
    [[ -n "$PROVIDER" ]] && INIT_ARGS+=(--provider "$PROVIDER")
    info "init" "ai-hats ${INIT_ARGS[*]}"
    "$LAUNCHER_DEST" "${INIT_ARGS[@]}" >/dev/null
    ok "init" "$(pwd)"
    [[ -n "$PROVIDER" ]] && ok "provider" "${PROVIDER}"
    [[ -n "$ROLE" ]]     && ok "role" "${ROLE}"
else
    info "init" "skipped — no -r/-p given"
fi

# -- Done --
echo ""
printf "  ${GREEN}${BOLD}ready${RESET}\n"
echo ""
if [[ -z "$ROLE" && -z "$PROVIDER" ]]; then
    printf "  Next:\n"
    printf "    ${CYAN}ai-hats self init -r <role> -p <provider>${RESET}\n"
else
    printf "    ${CYAN}ai-hats status${RESET}\n"
fi
echo ""
