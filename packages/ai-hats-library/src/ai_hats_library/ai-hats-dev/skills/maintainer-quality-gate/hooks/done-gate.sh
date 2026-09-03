#!/usr/bin/env bash
# ->done asks: is master green after this card. ->merge's set plus what only
# this edge can ask — the merge result, and the base it lands on.
set -uo pipefail
STAGES='e2e-catalog lint shellcheck dependency-floor silent-fallback test-isolation prose-refs ticket-ids env-reference gate-table wheel-contents master-ci unit integration merge-smoke'
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2
gate_main done-gate "$STAGES" "$@"
