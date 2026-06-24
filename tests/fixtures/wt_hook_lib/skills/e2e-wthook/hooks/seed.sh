#!/usr/bin/env bash
# E2E fixture wt_in hook (HATS-823). Proves wt_in runs AFTER `git worktree add`
# (the worktree exists) by recording the worktree path it was handed.
set -e
echo "$AI_HATS_WORKTREE_PATH" > "$AI_HATS_PROJECT_DIR/.seeded"
