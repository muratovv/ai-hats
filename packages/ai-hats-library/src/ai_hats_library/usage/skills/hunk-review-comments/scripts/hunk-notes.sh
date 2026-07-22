#!/usr/bin/env bash
# Wrapper for hunk-notes.sh (DOTS-157).
# Automatically resolved via PATH injection when the hunk-review-comments skill is composed.
set -euo pipefail

HUNK_NOTES_BIN="${HUNK_NOTES_BIN:-$HOME/.config/hunk/scripts/hunk-notes.sh}"

if [[ -x "$HUNK_NOTES_BIN" ]]; then
    exec "$HUNK_NOTES_BIN" "$@"
else
    echo "hunk-notes: $HUNK_NOTES_BIN not found or not executable" >&2
    exit 1
fi
