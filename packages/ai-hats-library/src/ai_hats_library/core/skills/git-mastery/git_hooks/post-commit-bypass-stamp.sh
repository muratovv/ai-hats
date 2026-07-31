#!/usr/bin/env bash
# HATS-1407 — stamp the new commit's SHA onto the bypass rows it came from.
#
# pre-commit runs before the commit object exists, so a bypass recorded there
# carries `sha:""` and the parent in `head_before`. This closes the join, which
# is what turns the journal from "a bypass happened around then" into "THIS
# commit went around the gate".
set -uo pipefail

# shellcheck source=../../../../hooks/bypass_journal.sh
if ! . "$(dirname "$0")/../bypass_journal.sh" 2>/dev/null; then
    echo "[bypass-journal] sha NOT STAMPED — bypass_journal.sh missing" >&2
    exit 0
fi

ai_hats_journal_stamp_sha
exit 0
