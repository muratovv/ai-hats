#!/usr/bin/env bash
# HATS-1877 — the `->review` gate (ADR-0023 D4), bound to `rack.tasks:->review`.
#
# It asks: is this work fit for a human to spend time on. The two later gates
# ask their questions of a branch and of master; nothing asked anything of the
# HAND-OFF, so a card reached a reviewer on the agent's word that the suite was
# green. This card exists because that word was measured and found unreliable:
# five times in one session the agent read a verdict wrong — a pipeline ate the
# runner's status, a `cd` moved the run into another checkout, a planted fixture
# was never scanned. The point of a marker is that there is no number for the
# agent to read: the composition stops at the first red and no marker is written.
#
# SAME COMPOSITION AS `merge-gate`, deliberately. The distinction between them is
# WHEN they fire, not what they demand, and because the marker is read across
# gates by stage subset (absorption, D5), one run satisfies both. Making this one
# lighter would have been the wrong saving: the whole objection is that tests had
# not necessarily run, so `unit` is the stage that cannot be dropped.
#
# A separate FILE from its two siblings, not a flag on them, because the checks
# channel has no argv: `run:` is `<skill>/<script>` and the runner spawns it bare.
# Everything the three would otherwise share lives in ../lib/gate.sh.
#
# Two modes, both the primitive's:
#
#   --check (DEFAULT — what the `composition.apps` binding runs). A marker
#     lookup, no suite: it runs inside the per-task rack lock.
#   --run (`make review-gate`). Run the composition and mark a clean green tree.
#
# Exit contract (ADR-0020 D2): 0 pass, 2 refuse, 126/127 corrupt, EVERYTHING
# ELSE — including 1 — is "the gate broke". Hence `set -uo pipefail` and no `-e`.

set -uo pipefail

GATE_NAME='review-gate'
RUN_CMD='make review-gate'
#: The ->done gate's superset. Its run stamps this one too (absorption, D5), so
#: a card closed in one sitting still pays for one run rather than three.
FULLER_CMD='make done-gate'

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate-marker.sh" || ! . "$_self_dir/../lib/gate.sh"; then
    printf 'review-gate: cannot load %s/../lib/ — the gate cannot look up any marker,\n' "$_self_dir"
    printf 'so it cannot hand this card to a reviewer.\n'
    exit 2
fi

# The check runner spawns this with no argv at all, so --check is the default.
case "${1:---check}" in
    --check) gate_check_task_worktree "$GATE_NAME" "$RUN_CMD" "$FULLER_CMD" ;;
    --run)
        shift
        gate_run_mode "$GATE_NAME" \
            "handing this card to a reviewer now passes instantly." "$@"
        ;;
    *)
        echo "usage: review-gate.sh [--check|--run [--rev <sha>]]" >&2
        # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
        # neither a refusal nor a verdict of any kind.
        exit 64
        ;;
esac
