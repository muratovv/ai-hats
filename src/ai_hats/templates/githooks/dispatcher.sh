#!/usr/bin/env bash
# AI-HATS-DISPATCHER-MARKER v2
# Managed by ai-hats. Do not edit manually.
#
# The one durable artifact of the git channel (ADR-0020 D3). It carries no role
# logic and no path into a versioned venv, so it survives `self update` by
# content: the gate SET is resolved at spawn time from the composition, never
# read from copies on disk.
#
# Degradation is FAIL-OPEN, always: a human `git commit` is never wedged because
# ai-hats is missing, broken or mid-update. A gate that cannot be resolved is a
# gate that does not run, said out loud on stderr — never a commit that cannot
# happen. Hooks the project owns (`<event>.d/` drop-ins and the pre-takeover
# hook chain) do not depend on ai-hats and run either way.
set -uo pipefail

GITHOOKS_DIR="$(cd "$(dirname "$0")" && pwd -P)"
EVENT="$(basename "$0")"
EVENT_D="${GITHOOKS_DIR}/${EVENT}.d"
# `core.hooksPath` is absolute (HATS-1337), so this is the MAIN checkout even
# when the commit happens inside a linked worktree — which is what makes every
# worktree run the same gate set.
PROJECT_DIR="$(dirname "$GITHOOKS_DIR")"

warn() { echo "ai-hats: $*" >&2; }

# --- Interpreter resolution -------------------------------------------------
# Read-only mirror of `scripts/ai-hats-launcher`'s precedence. Deliberately NOT
# a call into the launcher: that one heals and reinstalls a venv, which is
# minutes of work and network access in the middle of somebody's commit.
resolve_python() {
    local ah_dir versions sha val venv=""

    # 1. Pinned at spawn by the launcher — a commit inside an ai-hats session
    #    must run that session's exact venv.
    if [[ -n "${AI_HATS_VENV:-}" && -x "${AI_HATS_VENV}/bin/python" ]]; then
        echo "${AI_HATS_VENV}/bin/python"
        return 0
    fi

    # `AI_HATS_DIR` is honoured only when it belongs to THIS project: a session
    # in another project exports it, and a leaked pin would send us hunting for
    # versions/ under a foreign checkout (the HATS-897 leak class, guarded the
    # same way on the python side).
    ah_dir="$PROJECT_DIR/.agent/ai-hats"
    if [[ -n "${AI_HATS_DIR:-}" && "${AI_HATS_DIR}" == "$PROJECT_DIR"/* ]]; then
        ah_dir="$AI_HATS_DIR"
    fi
    versions="$ah_dir/versions"

    # 2. `venv_path:` in ai-hats.yaml (single-line scalar, as the launcher reads it).
    if [[ -f "$PROJECT_DIR/ai-hats.yaml" ]]; then
        val=$(grep -E '^venv_path:[[:space:]]' "$PROJECT_DIR/ai-hats.yaml" 2>/dev/null \
            | head -1 \
            | sed -E 's/^venv_path:[[:space:]]+//; s/[[:space:]]+#.*//; s/^"//; s/"$//; s/^'\''//; s/'\''$//' \
            || true)
        if [[ -n "$val" ]]; then
            val="${val/#\~/$HOME}"
            if [[ "${val:0:1}" == "/" ]]; then venv="$val"; else venv="$PROJECT_DIR/$val"; fi
        fi
    fi

    # 3. `versions/current` — the blue-green pointer. Usability keys on a runnable
    #    bin/python, so a half-built or host-python-broken version falls through.
    if [[ -z "$venv" && -f "$versions/current" ]]; then
        sha="$(head -1 "$versions/current" 2>/dev/null | tr -d '[:space:]' || true)"
        if [[ -n "$sha" && "$sha" != *"/"* && "$sha" != "." && "$sha" != ".." \
              && -x "$versions/$sha/bin/python" ]]; then
            venv="$versions/$sha"
        fi
    fi

    # 4. Legacy single venv (pre-versioning installs, and every ai-hats dev checkout).
    [[ -z "$venv" ]] && venv="$ah_dir/.venv"

    [[ -x "$venv/bin/python" ]] || return 1
    echo "$venv/bin/python"
}

# --- Resolve the gate set ---------------------------------------------------
GATES=()
JOURNAL=""
if PY="$(resolve_python)"; then
    # A MODULE, never the `ai-hats githooks resolve` subcommand. The CLI's top
    # group treats an unregistered subcommand as a bare prompt and launches a
    # provider session (_PassthroughGroup, HATS-1202) — so against an ai-hats too
    # old to know that subcommand, a plain `git commit` would start an agent
    # session that re-materializes the project and overwrites THIS running file.
    # A missing module just fails to import, which is the fail-open path.
    # stderr is NOT swallowed: a refused gate must say why.
    if resolved="$("$PY" -m ai_hats.cli.githooks_main "$EVENT" --project-dir "$PROJECT_DIR")"; then
        while IFS=$'\t' read -r kind path; do
            case "$kind" in
                gate) [[ -n "$path" ]] && GATES+=("$path") ;;
                journal) JOURNAL="$path" ;;
            esac
        done <<< "$resolved"
    else
        warn "could not resolve '$EVENT' gates — they are SKIPPED (fail-open)"
    fi
else
    warn "no usable ai-hats install — '$EVENT' gates are SKIPPED (fail-open)"
fi

# --- The project's own drop-ins (read, never written by ai-hats) -------------
DROPINS=()
if [[ -d "$EVENT_D" ]]; then
    shopt -s nullglob
    for script in "$EVENT_D"/*; do
        [[ -f "$script" && -x "$script" ]] && DROPINS+=("$script")
    done
    shopt -u nullglob
fi

# --- Previous-hooks chaining (HATS-999) -------------------------------------
# ai-hats owns `core.hooksPath`, but the repo's own hook manager
# (simple-git-hooks / husky / ...) still materializes at the PREVIOUS location —
# recorded at takeover, git's default otherwise. Chain to it LIVE (no snapshot:
# those managers regenerate their hooks) so neither stack silently shadows the other.
PREV_DIR="$(git config --get ai-hats.previousHooksPath 2>/dev/null || true)"
PREV_DIR="${PREV_DIR:-.git/hooks}"
CHAIN="${PREV_DIR}/${EVENT}"
if [[ -f "$CHAIN" && -x "$CHAIN" ]]; then
    chain_dir="$(cd "$(dirname "$CHAIN")" 2>/dev/null && pwd -P || true)"
    # Recursion guard: never chain back into our own hooks dir.
    [[ -z "$chain_dir" || "$chain_dir" == "$GITHOOKS_DIR" ]] && CHAIN=""
else
    CHAIN=""
fi

if [[ ${#GATES[@]} -eq 0 && ${#DROPINS[@]} -eq 0 && -z "$CHAIN" ]]; then
    exit 0
fi

# A resolved gate's own $0 is its library path, so it cannot recover the git
# event from $0 — this carries it (HATS-593).
export AI_HATS_HOOK_EVENT="$EVENT"
# Gates source the bypass journal through this. Their relative fallback is only
# correct inside the builtin library, so the resolved path wins (HATS-1337).
[[ -n "$JOURNAL" ]] && export AI_HATS_BYPASS_JOURNAL="$JOURNAL"

# --- STDIN fan-out (HATS-654) ----------------------------------------------
# Some git events deliver a protocol on STDIN that EVERY script must see. One
# shared stdin means the first consumer drains it and every later script reads
# EOF — for pre-push that silently no-ops the e2e-master gate. Capture once and
# replay a fresh copy into each script.
#
# Scoped to events with a documented STDIN protocol BY NAME, never a runtime
# `[[ -t 0 ]]` probe: a stdin-less event (pre-commit, post-checkout) must never
# `cat`, or an open pipe on fd 0 inside an agent harness blocks forever.
STDIN_FILE=""
case "$EVENT" in
    pre-push|pre-receive|post-receive|post-rewrite|proc-receive|reference-transaction)
        if STDIN_FILE="$(mktemp "${TMPDIR:-/tmp}/ai-hats-${EVENT}-stdin.XXXXXX")"; then
            trap 'rm -f "$STDIN_FILE"' EXIT
            cat > "$STDIN_FILE"
        else
            STDIN_FILE=""   # degraded but no crash: fall back to shared stdin
        fi
        ;;
esac

# --- Run: resolved gates, then the project's drop-ins, then the chain --------
# First non-zero exit aborts the event, matching git's contract for a failed hook.
# `${arr[@]+"${arr[@]}"}` — an empty array under `set -u` is an unbound
# reference on bash 3.2 (macOS system bash).
for script in ${GATES[@]+"${GATES[@]}"} ${DROPINS[@]+"${DROPINS[@]}"}; do
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

if [[ -n "$CHAIN" ]]; then
    if [[ -n "$STDIN_FILE" ]]; then
        "$CHAIN" "$@" < "$STDIN_FILE"
    else
        "$CHAIN" "$@"
    fi
    rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "ai-hats: chained project hook '${CHAIN}' failed (exit $rc)" >&2
        exit "$rc"
    fi
fi

exit 0
