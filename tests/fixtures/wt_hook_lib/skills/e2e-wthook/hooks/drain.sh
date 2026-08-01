#!/usr/bin/env bash
# E2E fixture wt_out hook (HATS-823). Records the event, then fails on purpose
# when a control file exists so the test can exercise fail-closed teardown.
set -e
echo "$AI_HATS_EVENT" >> "$AI_HATS_PROJECT_DIR/.drain.log"
if [ -f "$AI_HATS_PROJECT_DIR/.drain-fail" ]; then
  echo "drain: failing on purpose for $AI_HATS_EVENT" >&2
  exit 1
fi
# HATS-1151: a considered verdict (exit 2) explaining itself on stdout — the
# reason channel must carry these words out to the operator.
if [ -f "$AI_HATS_PROJECT_DIR/.drain-refuse" ]; then
  echo "drain: 3 unresolved review notes in .hunk/notes.json"
  echo "drain: run 'bash hunk-notes.sh consume', then retry"
  exit 2
fi
echo "$AI_HATS_EVENT" >> "$AI_HATS_PROJECT_DIR/.drained"
