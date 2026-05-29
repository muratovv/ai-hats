#!/usr/bin/env bash
# HATS-593 — self-heal ai-hats-managed git hooks after drift.
#
# Installed as both post-merge AND post-checkout (the assembler copies one
# script per declared event). Merge / pull / branch-checkout rewrite tracked
# files but leave the untracked, generated .githooks/ stale until someone
# runs `ai-hats self init`. This hook re-materializes ONLY the git-hook
# surface via `ai-hats self sync-hooks` at the moment drift is introduced.
#
# CROSS-CUTTING POLICY (failure modes, see plan.md):
#   * ALWAYS exit 0 — fail-open. A self-heal failure must never abort the
#     git operation that triggered it (merge / checkout).
#   * Loud, actionable warning on any failure — never degrade silently.
#   * #1 ai-hats not on PATH (no venv in the hook env) → warn, exit 0.
#   * #7 post-checkout fires on FILE checkouts too; git passes
#        $1=prev_head $2=new_head $3=branch_flag. Act only when $3 == 1
#        (a branch/HEAD checkout); skip per-file checkouts (flag 0).
#   * #8 linked worktree: `.git` is a FILE pointing at the real gitdir, and
#        core.hooksPath / .githooks live in the working checkout. We run
#        sync-hooks from the worktree top-level so it resolves correctly.

set -uo pipefail

# The dispatcher exports AI_HATS_HOOK_EVENT (this script's $0 is its renamed
# .d/ path, e.g. <skill>-self-heal-hooks.sh, so $0 cannot name the event).
# Fall back to $0's basename for direct invocation in tests / manual runs.
EVENT="${AI_HATS_HOOK_EVENT:-$(basename "$0")}"

# --- #7: post-checkout file-checkout guard ----------------------------------
# Only the post-checkout hook receives the branch-flag as $3. A file checkout
# (`git checkout -- path`) passes flag 0 and does NOT introduce hook drift.
if [[ "$EVENT" == "post-checkout" ]]; then
    flag="${3:-0}"
    if [[ "$flag" != "1" ]]; then
        exit 0
    fi
fi

# --- #8: run from the working-tree top-level --------------------------------
# In a linked worktree `.git` is a file; `--show-toplevel` resolves to the
# correct working checkout where .githooks/ lives, independent of CWD.
top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$top" ]]; then
    cd "$top" || exit 0
fi

# --- #1: ai-hats must be reachable ------------------------------------------
AH="$(command -v ai-hats 2>/dev/null || true)"
if [[ -z "$AH" ]]; then
    echo "ai-hats: '$EVENT' wanted to refresh git hooks but 'ai-hats' is not on PATH —" >&2
    echo "         hooks may be stale. Run 'ai-hats self init' from an env with ai-hats." >&2
    exit 0
fi

# sync-hooks is itself fail-open (atomic write, loud warning on failure) and
# returns 0 even when it only warns. Guard the call so a non-zero never
# escapes and aborts the triggering git op.
if ! "$AH" self sync-hooks; then
    echo "ai-hats: '$EVENT' hook self-heal reported a problem — hooks may be stale." >&2
    echo "         Run 'ai-hats self init' to re-materialize." >&2
fi

exit 0
