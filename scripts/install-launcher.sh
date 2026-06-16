#!/usr/bin/env bash
# Install ai-hats launcher into ~/.local/bin/ai-hats (HATS-339).
#
# Idempotent: safe to re-run. Target path overridable via
# AI_HATS_LAUNCHER_DEST env var.
#
# Usage (from a local clone):
#   bash scripts/install-launcher.sh
#
# Usage (public one-liner — no credentials needed):
#   curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
#   (piped: fetches the launcher from the repo; override AI_HATS_LAUNCHER_URL for a fork.)
set -euo pipefail

DEST="${AI_HATS_LAUNCHER_DEST:-$HOME/.local/bin/ai-hats}"
LAUNCHER_URL="${AI_HATS_LAUNCHER_URL:-https://github.com/muratovv/ai-hats/raw/master/scripts/ai-hats-launcher}"

if [[ -t 1 ]]; then
    BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
ok()   { printf "  ${GREEN}✓${RESET} %-14s %s\n" "$1" "$2"; }
info() { printf "  ${DIM}…${RESET} %-14s %s\n" "$1" "$2"; }
warn() { printf "  ${YELLOW}!${RESET} %-14s %s\n" "$1" "$2" >&2; }
err()  { printf "  ${RED}✗${RESET} %-14s %s\n" "$1" "$2" >&2; }

echo ""
printf "  ${BOLD}ai-hats launcher install${RESET}\n"
echo ""

# --- Locate launcher source ---
SRC=""
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
if [[ -n "$SCRIPT_PATH" && "$SCRIPT_PATH" != "bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd || true)"
    if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/ai-hats-launcher" ]]; then
        SRC="$SCRIPT_DIR/ai-hats-launcher"
        info "source" "from clone: $SRC"
    fi
fi

mkdir -p "$(dirname "$DEST")"

if [[ -n "$SRC" ]]; then
    if [[ -f "$DEST" ]] && cmp -s "$SRC" "$DEST"; then
        ok "skip" "$DEST already current"
    else
        cp "$SRC" "$DEST"
        chmod +x "$DEST"
        ok "install" "$DEST"
    fi
else
    # Piped mode — fetch from network
    if ! command -v curl >/dev/null 2>&1; then
        err "curl" "not found — clone the repo and re-run from scripts/"
        exit 1
    fi
    info "source" "fetching $LAUNCHER_URL"
    TMP="$(mktemp)"
    trap 'rm -f "$TMP"' EXIT
    # curl -f rejects 4xx/5xx (e.g. a wrong-branch 404), so no HTML body leaks
    # into $DEST — the old private-repo HTML guard (HATS-339) is no longer needed.
    if ! curl -fsSL "$LAUNCHER_URL" -o "$TMP"; then
        err "fetch" "failed to download launcher from $LAUNCHER_URL"
        err "hint"  "check the URL/branch, or clone the repo and run scripts/install-launcher.sh locally"
        exit 1
    fi
    if [[ -f "$DEST" ]] && cmp -s "$TMP" "$DEST"; then
        ok "skip" "$DEST already current"
    else
        cp "$TMP" "$DEST"
        chmod +x "$DEST"
        ok "install" "$DEST"
    fi
fi

# --- PATH hint ---
case ":$PATH:" in
    *":$(dirname "$DEST"):"*) ok "path" "$(dirname "$DEST") is in PATH" ;;
    *)
        warn "path" "$(dirname "$DEST") is NOT in PATH"
        warn "hint" "add to shell rc:  export PATH=\"$(dirname "$DEST"):\$PATH\""
        ;;
esac

echo ""
printf "  ${GREEN}${BOLD}ready${RESET}\n"
echo ""
printf "  Next, in a project:\n"
printf "    ${BOLD}ai-hats self init -r assistant -p claude${RESET}    # creates venv + configures project\n"
printf "  ${DIM}(later, to upgrade ai-hats itself:  ai-hats self update)${RESET}\n"
echo ""
