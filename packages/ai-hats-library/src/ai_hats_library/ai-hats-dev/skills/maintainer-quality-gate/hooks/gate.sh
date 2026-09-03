#!/usr/bin/env bash
# The one checks-channel gate hook. WHICH gate it is comes from the binding row
# that spawned it — `gate: <name>` beside `run:` in `composition.apps`, which
# the engine hands over as AI_HATS_CARGO_GATE — so one file serves every edge,
# and adding a gate is adding a row, not writing a script.
#
# It asks the project under judgement one question, through that project's own
# `scripts/gates.sh check <gate>`: has the tree this card puts into master
# earned every stage the gate requires. A marker lookup, no suite: it runs
# inside the per-task rack lock. The suite runs out of band —
# `scripts/gates.sh run <gate>`, or `make <gate>` where the project wraps it.
#
# Exit contract (ADR-0020 D2): 0 pass, 2 refuse (the stdout tail becomes the
# reason the agent reads), 126/127 corrupt, EVERYTHING ELSE — including 1 — is
# "the gate broke". Hence `set -uo pipefail` and no `-e`: under `-e` any stray
# non-zero command becomes exit 1, which would report a broken gate where there
# was only a failed `grep`.
#
# The check must never invoke a mutating `rack` / `ai-hats wt` command on its own
# task: it runs inside that task's lock, and a subprocess re-entering it would
# deadlock. AI_HATS_IN_HOOK=1 marks that. It only reads.

set -uo pipefail

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate.sh"; then
    printf 'gate: cannot load %s/../lib/gate.sh — the gate cannot look anything up,\n' "$_self_dir"
    printf 'so it cannot let this transition through.\n'
    exit 2
fi

gate="${AI_HATS_CARGO_GATE:-}"
if [[ -z "$gate" ]]; then
    printf 'gate: the binding row that spawned this hook names no gate.\n\n'
    printf "Add 'gate: <name>' beside 'run:' in that composition.apps row; the names\n"
    printf 'are what `scripts/gates.sh list` prints. A hook that cannot say which gate\n'
    printf 'it is cannot verify anything, so it does not pass.\n'
    exit 2
fi

# The check runner spawns this with no argv at all, so --check is the default.
case "${1:---check}" in
    --check) gate_check_task_worktree "$gate" ;;
    *)
        echo "usage: gate.sh [--check]   (AI_HATS_CARGO_GATE names the gate;" >&2
        echo "       to RUN one: scripts/gates.sh run <gate>)" >&2
        # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
        # neither a refusal nor a verdict of any kind.
        exit 64
        ;;
esac
