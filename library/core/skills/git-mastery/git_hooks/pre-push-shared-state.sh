#!/usr/bin/env bash
# HATS-437 — git pre-push: block `git push --force` / `-f` without ack.
#
# Provider-agnostic safety net. The PreToolUse hook (Claude-only) catches
# `gh pr merge` and `git push --force` before the Bash invocation; this
# pre-push hook is the resilient second layer that fires even in Gemini
# sessions or when the user runs `git push -f` from a normal terminal in
# an agent-driven worktree.
#
# Override (per single push):  AI_HATS_SHARED_STATE_ACK=1 git push --force ...
#
# Pre-push hook receives the push command line in two ways:
#   * argv:  $1=remote $2=URL  (no flags propagated — git strips them)
#   * stdin: lines `<local_ref> <local_sha> <remote_ref> <remote_sha>`
#
# We cannot read the original CLI argv directly. We rely on git itself
# having reported a deletion or a non-fast-forward intention to its
# wire-protocol decision; the cheap proxy is `GIT_PUSH_OPTION_*` / the
# explicit env flag the user-driven hook receives. To avoid false negatives
# we use the canonical pre-push contract: if any (local_sha, remote_sha)
# pair indicates a forced overwrite (non-fast-forward), the hook blocks.

set -uo pipefail

if [[ "${AI_HATS_SHARED_STATE_ACK:-}" == "1" ]]; then
    echo "[shared-state-guard] AI_HATS_SHARED_STATE_ACK=1 — allowing forced push" >&2
    exit 0
fi

# Iterate refs from stdin. Each non-empty line:
#   <local_ref> <local_sha> <remote_ref> <remote_sha>
# A non-fast-forward push is detected when both shas are non-zero and the
# remote commit is NOT an ancestor of the local commit (i.e. force-push
# would rewrite history). Branch deletion (local_sha == 0000...) is NOT
# treated as force here — agent-driven `--delete-branch` is covered by the
# PreToolUse hook, and benign cleanup pushes should not be blocked.
zero='0000000000000000000000000000000000000000'
blocked=0
while read -r local_ref local_sha remote_ref remote_sha; do
    [[ -z "${local_ref:-}" ]] && continue
    # Skip ref deletions (local sha = zero).
    if [[ "$local_sha" == "$zero" ]]; then
        continue
    fi
    # Skip new refs (remote sha = zero; not a force).
    if [[ "$remote_sha" == "$zero" ]]; then
        continue
    fi
    # Non-fast-forward check.
    if ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
        echo "[shared-state-guard] BLOCKED — non-fast-forward push detected:" >&2
        echo "    $local_ref ($local_sha) → $remote_ref ($remote_sha)" >&2
        echo "    remote commit is not an ancestor — this is a force-push." >&2
        blocked=1
    fi
done

if [[ $blocked -eq 1 ]]; then
    cat >&2 <<EOF

This rewrites shared history with no clean undo path (HATS-437). Pause and
ask the user before continuing. Do NOT chain forced pushes with other
git commands.

Override for a single push (only after user has confirmed):
  AI_HATS_SHARED_STATE_ACK=1 git push ...
EOF
    exit 1
fi

exit 0
