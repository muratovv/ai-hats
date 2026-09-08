#!/usr/bin/env bash
# ->done asks: is master green after this card. ->merge's set plus what only
# this edge can ask — the merge result, and the base it lands on. `master-ci`
# goes LAST: markers are per stage, so a red base still lets every local stage
# earn its stamp, and the refusal names only the one thing that is not ours.
set -uo pipefail
STAGES='e2e-catalog lint shellcheck dependency-floor silent-fallback test-isolation prose-refs ticket-ids consumer-refs env-reference gate-table wheel-contents unit integration merge-smoke master-ci'
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2
gate_main done-gate "$STAGES" "$@"
