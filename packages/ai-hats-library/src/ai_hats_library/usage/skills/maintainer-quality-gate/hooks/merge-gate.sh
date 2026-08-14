#!/usr/bin/env bash
# HATS-1614 — the `->merge` gate (ADR-0023 D4), bound to `wt:pre-merge`.
#
# It asks: is this branch fit to enter master. At this edge the agent is ALONE,
# so only checks whose refusal points at the agent's own branch and is fixable
# without an arbiter belong here (D3) — a refusal costs nothing, because nothing
# has been merged yet.
#
# A separate FILE from done-gate.sh, not a flag on it, because the checks
# channel has no argv: `run:` is `<skill>/<script>` and the runner spawns it
# bare (`libraries/models.py`, `rack_consumers.py`). Both files are this thin
# precisely because of that — everything they would otherwise share lives in
# ../lib/gate.sh.
#
# Two modes, both the primitive's:
#
#   --check (DEFAULT — what the `composition.apps` binding runs). A marker
#     lookup, no suite: it runs inside the per-task rack lock.
#   --run (`make merge-gate`). Run the composition and mark a clean green tree.
#
# Exit contract (ADR-0020 D2): 0 pass, 2 refuse, 126/127 corrupt, EVERYTHING
# ELSE — including 1 — is "the gate broke". Hence `set -uo pipefail` and no `-e`.

set -uo pipefail

GATE_NAME='merge-gate'
RUN_CMD='make merge-gate'
#: The ->done gate's superset. Its run stamps this one too (absorption, D5), so
#: naming it here is what keeps a typical card at one run instead of two.
FULLER_CMD='make done-gate'

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate-marker.sh" || ! . "$_self_dir/../lib/gate.sh"; then
    printf 'merge-gate: cannot load %s/../lib/ — the gate cannot look up any marker,\n' "$_self_dir"
    printf 'so it cannot let this merge through.\n'
    exit 2
fi

# The check runner spawns this with no argv at all, so --check is the default.
case "${1:---check}" in
    --check) gate_check_task_worktree "$GATE_NAME" "$RUN_CMD" "$FULLER_CMD" ;;
    --run)
        gate_run_and_stamp_here "$GATE_NAME" \
            "'ai-hats wt merge' on this content now passes instantly."
        ;;
    *)
        echo "usage: merge-gate.sh [--check|--run]" >&2
        # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
        # neither a refusal nor a verdict of any kind.
        exit 64
        ;;
esac
