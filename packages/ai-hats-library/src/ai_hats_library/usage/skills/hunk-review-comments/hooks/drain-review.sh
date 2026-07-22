#!/usr/bin/env bash
# drain-review.sh — wt_out drain hook for hunk review notes (HATS-818, ADR-0012).
#
# Runs the user's DOTS-157 `hunk-notes.sh consume` against the worktree being
# torn down, so `<worktree>/.hunk/notes.json` is backed up (to /tmp/review/<wtid>)
# and cleared BEFORE teardown destroys it — the consume that never ran in the
# incident. The engine invokes this with cwd = the MAIN repo, so the script
# `cd`s into $AI_HATS_WORKTREE_PATH (also reproducing the same backup id as a
# manual drain, which runs hunk-notes.sh from the worktree with no selector).
#
# Consume-only: a clean drain exits 0 and teardown proceeds. It exits non-zero —
# engine is fail-closed, so teardown ABORTS and the worktree is preserved — only
# when the drain FAILED: consume errored, or returned 0 yet left the sidecar
# intact (e.g. `jq` missing makes consume treat it as malformed and leave it).
# It never blocks a *successfully* drained worktree (that would be the rejected
# refuse-non-empty guard; that is the built-in harvest_out's job, HATS-775).
#
# Every run appends one triage line to $DRAIN_REVIEW_LOG for fast diagnosis.
#
# Env (from the engine): AI_HATS_WORKTREE_PATH, AI_HATS_EVENT, AI_HATS_BRANCH_NAME.
# Overridable (tests / relocation): HUNK_NOTES_SH, HUNK_NOTES_BACKUP_DIR,
# DRAIN_REVIEW_LOG.
set -euo pipefail

HUNK_NOTES_SH="${HUNK_NOTES_SH:-hunk-notes.sh}"
BACKUP_ROOT="${HUNK_NOTES_BACKUP_DIR:-/tmp/review}" # must match hunk-notes.sh default
LOG_FILE="${DRAIN_REVIEW_LOG:-$BACKUP_ROOT/drain-review.log}"

wt="${AI_HATS_WORKTREE_PATH:?drain-review: AI_HATS_WORKTREE_PATH not set}"
event="${AI_HATS_EVENT:-?}"
branch="${AI_HATS_BRANCH_NAME:-?}"
sidecar="$wt/.hunk/notes.json"

# Best-effort triage log; a logging failure must never abort the drain.
log() {
	local ts
	ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '?')"
	{
		mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null &&
			printf '%s\t%s\n' "$ts" "$*" >>"$LOG_FILE"
	} 2>/dev/null || true
}

# No (or empty) sidecar → nothing to drain. Idempotent no-op: a retry after a
# clean drain, or a teardown of a never-reviewed worktree, lands here.
if [[ ! -s "$sidecar" ]]; then
	log "NOOP event=$event branch=$branch wt=$wt (no sidecar)"
	exit 0
fi

cd "$wt"

set +e
out="$("$HUNK_NOTES_SH" consume 2>&1)"
rc=$?
set -e

if ((rc != 0)); then
	log "FAIL event=$event branch=$branch rc=$rc consume-error: ${out//$'\n'/ | }"
	printf '%s\n' "$out" >&2
	echo "drain-review: hunk-notes.sh consume failed (rc=$rc) — teardown aborts (fail-closed)." >&2
	exit "$rc"
fi

# Post-condition: consume returns 0 even when it does nothing (missing `jq` →
# treats the sidecar as malformed and leaves it). If the sidecar survived, the
# notes were NOT harvested — fail closed so teardown does not destroy them.
if [[ -s "$sidecar" ]]; then
	log "FAIL event=$event branch=$branch rc=0-but-sidecar-survived (drain no-op; jq missing?)"
	echo "drain-review: consume exited 0 but $sidecar is still non-empty — drain failed, teardown aborts (fail-closed). Check that jq is installed." >&2
	exit 1
fi

n="$(printf '%s' "$out" | grep -c . || true)"
log "OK event=$event branch=$branch drained=$n backup=$BACKUP_ROOT"
if ((n > 0)); then
	log "NOTES event=$event branch=$branch:${out:+ | }${out//$'\n'/ | }"
fi
printf '%s\n' "$out"
exit 0
