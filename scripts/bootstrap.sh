#!/usr/bin/env bash
# ai-hats bootstrap — convenience wrapper for first-time setup (HATS-333/336).
#
# Pipeline: install-launcher.sh → `ai-hats self update` → `ai-hats self init`.
# Idempotent — safe to re-run; launcher's self update handles existing venv.
#
# Quick start (piped one-liner — fetches the installer + launcher):
#   curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash -s -- -r assistant -p claude
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
  ${DIM}# Piped one-liner (fresh host)${RESET}
  curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash -s -- -r assistant -p claude

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

# -- 1. uv precondition — the engine; provisions Python. Auto-install (unlike the
#       host-global launcher) so the piped one-liner needs no pre-installed uv. --
if ! command -v uv >/dev/null 2>&1; then
    info "uv" "not found — installing via astral.sh"
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        err "uv" "auto-install failed"
        err "hint" "install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    # astral edits rc files but not THIS shell's PATH — refresh it. Source the env
    # under +u (third-party shell may ref unset vars); the PATH export is the real fix.
    if [[ -f "$HOME/.local/bin/env" ]]; then
        set +u; . "$HOME/.local/bin/env"; set -u
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    err "uv" "still not found after install"
    err "hint" "add ~/.local/bin to PATH and re-run"
    exit 1
fi
ok "uv" "$(uv --version 2>/dev/null || echo present)"

# -- 2. Install launcher (local script if cloned, else fetch — enables piped use) --
LAUNCHER_DEST="${AI_HATS_LAUNCHER_DEST:-$HOME/.local/bin/ai-hats}"
INSTALL_LAUNCHER_URL="${AI_HATS_INSTALL_LAUNCHER_URL:-https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh}"
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/install-launcher.sh" ]]; then
    info "launcher" "running $SCRIPT_DIR/install-launcher.sh"
    AI_HATS_LAUNCHER_DEST="$LAUNCHER_DEST" bash "$SCRIPT_DIR/install-launcher.sh" >/dev/null
else
    # Piped (`curl … bootstrap.sh | bash`): fetch the installer; it self-fetches
    # the launcher binary from the same public repo.
    info "launcher" "fetching install-launcher.sh"
    _installer="$(mktemp)"
    trap 'rm -f "$_installer"' EXIT
    if ! curl -fsSL "$INSTALL_LAUNCHER_URL" -o "$_installer"; then
        err "launcher" "could not fetch $INSTALL_LAUNCHER_URL"
        err "hint" "clone the ai-hats repo and run scripts/bootstrap.sh locally"
        exit 1
    fi
    AI_HATS_LAUNCHER_DEST="$LAUNCHER_DEST" bash "$_installer" >/dev/null
fi
ok "launcher" "$LAUNCHER_DEST"

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
