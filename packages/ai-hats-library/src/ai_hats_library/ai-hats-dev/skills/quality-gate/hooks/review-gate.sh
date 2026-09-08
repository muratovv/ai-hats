#!/usr/bin/env bash
# ->review asks: is this work fit for a human to spend time on.
set -uo pipefail
STAGES='e2e-catalog lint shellcheck dependency-floor silent-fallback test-isolation prose-refs ticket-ids env-reference gate-table wheel-contents unit'
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2
gate_main review-gate "$STAGES" "$@"
