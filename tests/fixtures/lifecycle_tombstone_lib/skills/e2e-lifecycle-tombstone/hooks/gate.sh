#!/usr/bin/env bash
# HATS-1147 fixture: a healthy script, so the e2e proves the DECLARATION is
# refused — not that a broken script was caught by the old health-check.
set -euo pipefail
echo "e2e-lifecycle-tombstone gate ran — it must not have"
exit 1
