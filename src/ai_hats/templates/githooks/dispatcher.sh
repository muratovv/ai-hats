#!/usr/bin/env bash
# AI-HATS-DISPATCHER-MARKER v1
# Managed by ai-hats. Do not edit manually.
# Add hooks by declaring `git_hooks:` in a skill's metadata.yaml — they
# will be installed into .githooks/<event>.d/ on the next composition.
set -uo pipefail

GITHOOKS_DIR="$(dirname "$0")"
EVENT="$(basename "$0")"
EVENT_D="${GITHOOKS_DIR}/${EVENT}.d"
MANIFEST="${GITHOOKS_DIR}/.ai-hats-manifest"

# --- Gate-integrity backstop (fail-closed, HATS-593) ------------------------
# The manifest lists every ai-hats-MANAGED entry (`<event>.d/<skill>-<name>`
# and the bare `<event>` dispatcher). For THIS event, any managed `.d/` entry
# that the manifest expects but that is now absent or non-executable means the
# gate is corrupt: self-heal failed AND a hook is gone. Skipping it silently
# is the one failure we cannot tolerate (a degraded/bypassed push gate), so we
# fail LOUD and CLOSED. Cheap: read manifest + stat, no composition.
#
# Scoped to MANAGED entries only — an event with no managed entries in the
# manifest (a legitimately empty `.d/`) is NOT blocked.
if [[ -f "$MANIFEST" ]]; then
    while IFS= read -r entry; do
        entry="${entry%$'\r'}"   # tolerate CRLF manifests
        [[ -z "$entry" || "$entry" == \#* ]] && continue
        # Only managed `.d/` entries for THIS event.
        [[ "$entry" == "${EVENT}.d/"* ]] || continue
        expected="${GITHOOKS_DIR}/${entry}"
        if [[ ! -f "$expected" || ! -x "$expected" ]]; then
            echo "ai-hats: git hooks corrupt — expected managed hook '${entry}' is" >&2
            echo "         missing or non-executable. Refusing to run a degraded" >&2
            echo "         '${EVENT}' gate. Run 'ai-hats self init' to repair." >&2
            exit 1
        fi
    done < "$MANIFEST"
fi

if [[ ! -d "$EVENT_D" ]]; then
    exit 0
fi

# Run scripts in lexicographic order. First non-zero exit aborts the chain
# (matches git's expectation that a failed pre-commit blocks the commit).
# Export the resolved event name: a managed `.d/` script's own $0 is its
# renamed path (e.g. <skill>-<basename>), so it cannot recover the git event
# from $0 — AI_HATS_HOOK_EVENT carries it (HATS-593).
export AI_HATS_HOOK_EVENT="$EVENT"

# --- STDIN fan-out (HATS-654) ----------------------------------------------
# Some git events deliver a protocol on STDIN that EVERY .d/ script must see:
#   pre-push / pre-receive / post-receive : <local_ref> <local_sha> <remote_ref> <remote_sha>
#   post-rewrite                          : <old_sha> <new_sha> [extra]
#   proc-receive / reference-transaction  : their own line protocols
# The loop below runs each script sharing ONE stdin, so the first
# stdin-consuming hook drains the protocol and every later hook reads EOF. For
# pre-push that silently no-ops the e2e-master gate (empty stdin → fast-path
# exit 0 → master push never gated). Capture the protocol ONCE and replay a
# fresh copy into each script via `< "$STDIN_FILE"`.
#
# Scoped to events with a documented STDIN protocol by NAME (not a runtime
# `[[ -t 0 ]]` probe): stdin-less events (pre-commit, post-checkout,
# post-merge) must never `cat`, or a tty/open-pipe on fd 0 (e.g. inside an
# agent harness) would block the hook forever. A tmpfile + `< file` preserves
# bytes exactly and hands each script a fresh fd at offset 0.
#
# The list is the full git ref-protocol family on purpose, not just the one
# event with a .d/ chain today (pre-push). It costs nothing: capture runs only
# AFTER the `[[ ! -d "$EVENT_D" ]]` early-out above, so an event with no
# installed `<event>.d/` hooks (every entry here except pre-push, currently)
# never reaches the `cat`/`mktemp`. Listing the family keeps the dispatcher
# correct-by-default the day a skill declares, say, a post-rewrite.d/ chain.
STDIN_FILE=""
case "$EVENT" in
    pre-push|pre-receive|post-receive|post-rewrite|proc-receive|reference-transaction)
        if STDIN_FILE="$(mktemp "${TMPDIR:-/tmp}/ai-hats-${EVENT}-stdin.XXXXXX")"; then
            trap 'rm -f "$STDIN_FILE"' EXIT
            cat > "$STDIN_FILE"
        else
            # mktemp failed — fall back to shared stdin (degraded but no crash).
            STDIN_FILE=""
        fi
        ;;
esac

shopt -s nullglob
for script in "$EVENT_D"/*; do
    [[ -f "$script" && -x "$script" ]] || continue
    if [[ -n "$STDIN_FILE" ]]; then
        "$script" "$@" < "$STDIN_FILE"
    else
        "$script" "$@"
    fi
    rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "ai-hats: hook '$(basename "$script")' failed (exit $rc)" >&2
        exit "$rc"
    fi
done

exit 0
