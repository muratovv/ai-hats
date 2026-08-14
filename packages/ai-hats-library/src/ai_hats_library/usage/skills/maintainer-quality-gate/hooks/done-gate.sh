#!/usr/bin/env bash
# The `->done` gate (ADR-0026 D4), bound to the FSM edge `review--done`.
#
# It asks: is master green after this card. That is the one question no earlier
# gate can ask — two independently green branches make a red master — and the
# supervisor is present at this edge, so a refusal here may need coordination
# between agents (D3).
#
# Two modes:
#
#   --check (DEFAULT — what the `composition.apps` binding runs). A marker
#     lookup, no suite: it runs inside the per-task rack lock.
#   --run (`make done-gate`). Run the composition and mark a clean green tree.
#
# HATS-1604 moved the discipline into ../lib/gate.sh; HATS-1614 moved both whole
# modes there, when `merge-gate.sh` would otherwise have copied them to change
# two strings. This file states a name, a restart command and nothing else — the
# composition is the project dispatcher's to answer (ADR-0026 D7).
#
# A SEPARATE script from pre-push-e2e-master.sh on purpose. That one's default
# mode reads git's pre-push protocol from stdin, and the check runner gives its
# children stdin=DEVNULL — empty stdin hits its "no master target" fast path and
# it exits 0. Reusing it would have produced a gate that is silently always green.
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

GATE_NAME='done-gate'
RUN_CMD='make done-gate'

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate-marker.sh" || ! . "$_self_dir/../lib/gate.sh"; then
    printf 'done-gate: cannot load %s/../lib/ — the gate cannot look up any marker,\n' "$_self_dir"
    printf 'so it cannot let this transition through.\n'
    exit 2
fi

# The check runner spawns this with no argv at all, so --check is the default.
case "${1:---check}" in
    --check) gate_check_task_worktree "$GATE_NAME" "$RUN_CMD" ;;
    --run)
        gate_run_and_stamp_here "$GATE_NAME" \
            "'rack transition <ID> done' on this content now passes instantly."
        ;;
    *)
        echo "usage: done-gate.sh [--check|--run]" >&2
        # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
        # neither a refusal nor a verdict of any kind.
        exit 64
        ;;
esac
