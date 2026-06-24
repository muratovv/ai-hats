#!/usr/bin/env bash
# E2E fixture wt_out hook (HATS-823). Records the event, then fails on purpose
# when a control file exists so the test can exercise fail-closed teardown.
set -e
echo "$AI_HATS_EVENT" >> "$AI_HATS_PROJECT_DIR/.drain.log"
if [ -f "$AI_HATS_PROJECT_DIR/.drain-fail" ]; then
  echo "drain: failing on purpose for $AI_HATS_EVENT" >&2
  exit 1
fi
echo "$AI_HATS_EVENT" >> "$AI_HATS_PROJECT_DIR/.drained"
