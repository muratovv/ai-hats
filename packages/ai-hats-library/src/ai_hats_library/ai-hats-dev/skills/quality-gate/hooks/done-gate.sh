#!/usr/bin/env bash
# ->done asks: is master green after this card. ->merge's set plus what only
# this edge can ask — the merge result, and the base it lands on. `master-ci`
# goes LAST: markers are per stage, so a red base still lets every local stage
# earn its stamp, and the refusal names only the one thing that is not ours.
set -uo pipefail
# The zones this change touches are demanded at `->merge`, where the agent fixes
# alone what its own diff was expected to break. This edge asks the other
# question — what the merge itself broke — and asking for them again would run
# them a second time, on a second tree, for a refusal nobody here is for.
# shellcheck disable=SC2034 # read by lib/gate.sh, sourced two lines down
DEMAND_ZONES=none
STAGES='e2e-catalog lint shellcheck dependency-floor silent-fallback test-isolation prose-refs ticket-ids consumer-refs env-reference gate-table wheel-contents unit integration merge-smoke e2e-default master-ci'
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2
gate_main done-gate "$STAGES" "$@"
