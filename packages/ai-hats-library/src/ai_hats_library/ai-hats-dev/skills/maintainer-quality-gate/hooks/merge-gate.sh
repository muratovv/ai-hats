#!/usr/bin/env bash
# ->merge asks: is this branch fit to enter master. The same set as ->review on
# purpose: the two differ in WHEN they fire, not in what they demand.
set -uo pipefail
STAGES='e2e-catalog lint shellcheck dependency-floor silent-fallback test-isolation prose-refs ticket-ids env-reference gate-table wheel-contents unit'
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2
gate_main merge-gate "$STAGES" "$@"
