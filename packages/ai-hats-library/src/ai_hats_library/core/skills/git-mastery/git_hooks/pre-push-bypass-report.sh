#!/usr/bin/env bash
# HATS-1407 — surface the bypasses riding along in this push.
#
# A journal nobody reads is the same defect one level up: a sensor with no
# consumer (the HATS-725/726 shape — detected, prioritised, never consumed).
# This is that consumer. It never blocks: the hatch was a deliberate act, and
# the point is that the reviewer can see it, not that the push stops.
#
# STDIN is git's pre-push protocol: <local_ref> <local_sha> <remote_ref> <remote_sha>
set -uo pipefail

journal=""
if git_dir="$(git rev-parse --git-common-dir 2>/dev/null)"; then
    journal="${git_dir}/ai-hats/bypasses.jsonl"
fi
[[ -n "$journal" && -f "$journal" ]] || exit 0

ZERO="0000000000000000000000000000000000000000"
shas=""
while read -r _local_ref local_sha _remote_ref remote_sha; do
    [[ -z "${local_sha:-}" || "$local_sha" == "$ZERO" ]] && continue
    if [[ "${remote_sha:-$ZERO}" == "$ZERO" ]]; then
        range="$local_sha"          # new branch — git decides what is new
        revs="$(git rev-list "$range" --not --remotes 2>/dev/null)" || revs=""
    else
        revs="$(git rev-list "${remote_sha}..${local_sha}" 2>/dev/null)" || revs=""
    fi
    [[ -n "$revs" ]] && shas+="${revs}"$'\n'
done

[[ -n "${shas// /}" ]] || exit 0

hits="$(grep -F -f <(printf '%s' "$shas" | sed '/^$/d') "$journal" 2>/dev/null)" || hits=""
[[ -n "$hits" ]] || exit 0

count="$(printf '%s\n' "$hits" | grep -c . || true)"
{
    echo ""
    echo "[bypass-journal] ${count} gate bypass(es) in the commits being pushed:"
    printf '%s\n' "$hits" | sed 's/^/  /'
    echo ""
    echo "  Full journal: ${journal}"
} >&2

exit 0
