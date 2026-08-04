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
# HATS-1161: a refusal that colours itself, plus a report of what the hook saw
# in the colour-forcing vars — both vectors of the ANSI leak in one branch.
if [ -f "$AI_HATS_PROJECT_DIR/.drain-ansi" ]; then
  printf '\033[31mdrain: refusing\033[0m — FORCE_COLOR=%s NO_COLOR=%s\n' \
    "${FORCE_COLOR-unset}" "${NO_COLOR-unset}"
  exit 2
fi
# HATS-1269: bundle: dir — a file shipped beside this script is on disk at
# spawn, because the hook runs in place inside its declaring skill.
if [ -f "${BASH_SOURCE[0]%/*}/neighbour.txt" ]; then
  cat "${BASH_SOURCE[0]%/*}/neighbour.txt" >> "$AI_HATS_PROJECT_DIR/.neighbour"
fi
echo "$AI_HATS_EVENT" >> "$AI_HATS_PROJECT_DIR/.drained"
