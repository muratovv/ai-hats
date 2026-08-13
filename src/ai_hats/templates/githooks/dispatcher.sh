#!/usr/bin/env bash
# AI-HATS-DISPATCHER-MARKER
# Managed by ai-hats. Do not edit manually.
#
# A STUB, deliberately: it finds an ai-hats interpreter and hands the whole event
# over. Which gates run, in what order, how stdin is replayed, what happens to a
# script you dropped in yourself — none of that is here, because all of it will
# change and this file must not. It is re-copied on `self init` / `self update` /
# `set_role`, but NEVER at commit time: between two such runs these exact bytes
# are what execute, so every line here is a contract with the projects composed
# in that window — which spans years.
#
# Two things it must do alone, because nothing else can:
#   1. bootstrap an interpreter (the thing being delegated to cannot find itself);
#   2. fail OPEN when there is none — a human `git commit` is never wedged by a
#      missing, half-updated or broken ai-hats.
set -uo pipefail

EVENT="$(basename "$0")"
GITHOOKS_DIR="$(cd "$(dirname "$0")" && pwd -P)"
# `core.hooksPath` is absolute, so this is the MAIN checkout even when the commit
# happens inside a linked worktree — which is what gates every worktree.
PROJECT_DIR="$(dirname "$GITHOOKS_DIR")"

# ADR-0025 D3. The line above is this stub's OWN answer to "which project", so a
# pin naming a different one belongs to somebody else's session: drop the keys
# travelling with it rather than resolve an interpreter under a foreign checkout
# (HATS-897, HATS-1525). `unset`, not a local blank — `githooks_run` hands this
# environment to every gate, drop-in and chained hook.
if [[ -n "${AI_HATS_PROJECT_DIR:-}" ]]; then
    ah_pin="${AI_HATS_PROJECT_DIR/#\~/${HOME:-}}"
    ah_pin="$(cd "$ah_pin" 2>/dev/null && pwd -P || echo "$ah_pin")"
    if [[ "$ah_pin" != "$PROJECT_DIR" ]]; then
        echo "ai-hats: session pinned to $ah_pin — foreign to $PROJECT_DIR; ignoring its AI_HATS_VENV/AI_HATS_DIR." >&2
        unset AI_HATS_VENV AI_HATS_DIR
    fi
    unset ah_pin
fi

# Mirrors the launcher's venv precedence, read-only. NOT a call into the launcher:
# that one heals and reinstalls, which is minutes of work inside somebody's commit.
resolve_python() {
    local ah_dir versions sha val venv=""

    if [[ -n "${AI_HATS_VENV:-}" && -x "${AI_HATS_VENV}/bin/python" ]]; then
        echo "${AI_HATS_VENV}/bin/python"; return 0
    fi

    # The guard above already dropped a pin belonging to another session. This
    # keeps the older, narrower test as well — deliberately, not by omission: it
    # answers a different question ("is that path even inside the tree I gate"),
    # and this is the one file that cannot be fixed at commit time, so the two
    # checks are worth more here than the consistency of dropping one.
    ah_dir="$PROJECT_DIR/.agent/ai-hats"
    if [[ -n "${AI_HATS_DIR:-}" && "${AI_HATS_DIR}" == "$PROJECT_DIR"/* ]]; then
        ah_dir="$AI_HATS_DIR"
    fi
    versions="$ah_dir/versions"

    if [[ -f "$PROJECT_DIR/ai-hats.yaml" ]]; then
        val=$(grep -E '^venv_path:[[:space:]]' "$PROJECT_DIR/ai-hats.yaml" 2>/dev/null \
            | head -1 \
            | sed -E 's/^venv_path:[[:space:]]+//; s/[[:space:]]+#.*//; s/^"//; s/"$//; s/^'\''//; s/'\''$//' \
            || true)
        if [[ -n "$val" ]]; then
            val="${val/#\~/${HOME:-}}"
            if [[ "${val:0:1}" == "/" ]]; then venv="$val"; else venv="$PROJECT_DIR/$val"; fi
        fi
    fi

    if [[ -z "$venv" && -f "$versions/current" ]]; then
        sha="$(head -1 "$versions/current" 2>/dev/null | tr -d '[:space:]' || true)"
        if [[ -n "$sha" && "$sha" != *"/"* && "$sha" != "." && "$sha" != ".." \
              && -x "$versions/$sha/bin/python" ]]; then
            venv="$versions/$sha"
        fi
    fi

    [[ -z "$venv" ]] && venv="$ah_dir/.venv"
    [[ -x "$venv/bin/python" ]] || return 1
    echo "$venv/bin/python"
}

if PY="$(resolve_python)"; then
    # Imported here rather than run with `-m`: `-m` on an ai-hats too old to
    # carry the module exits 1, and under `exec` that 1 becomes the hook's
    # verdict — version skew would BLOCK the commit, the exact failure mode this
    # stub exists to prevent. Catching the import keeps it fail-open.
    # `--` so a hook argument starting with `-` is never read as our own flag.
    exec "$PY" -c '
import sys
try:
    from ai_hats.cli.githooks_hook import main
except Exception as exc:
    sys.stderr.write("ai-hats: hook entry unavailable (%s) — hooks SKIPPED (fail-open)\n" % exc)
    sys.exit(0)
sys.exit(main(sys.argv[1:]))
' "$EVENT" --project-dir "$PROJECT_DIR" --githooks-dir "$GITHOOKS_DIR" -- "$@"
fi

echo "ai-hats: no usable install — '$EVENT' hooks are SKIPPED (fail-open)" >&2
exit 0
