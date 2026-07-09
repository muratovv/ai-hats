---
name: drift-detection
description: Compare implementation diff against task acceptance criteria and ADRs to surface drift. Use when a task is in review state before transitioning to done, when integrating a worktree spanning two or more sessions, during a pre-merge audit when the diff exceeds the task description, or after a sub-agent reports completion.
license: MIT
---
# Drift Detection

Verify that what was implemented matches what was asked. Surfaces silent scope creep, missing acceptance criteria, and architectural drift before a PR is merged or a long-running worktree is integrated.

## When to Use
Compares **what was asked vs what was built** — the implementation diff against
the task's acceptance criteria and ADRs — to catch silent scope creep at
`review`, on long-lived worktrees, or after a sub-agent reports done. Distinct
from **audit-reviewer**, which judges the quality of the code *as written*;
drift-detection asks whether it's the *right scope at all*. (This is task-vs-impl
drift, not IaC config drift.)

## Procedure

1. **Extract requirements.** Acceptance Criteria from `task.yaml`, linked ADRs in `<ai_hats_dir>/tracker/decisions/`, GitHub Issue body, and any updates logged via `ai-hats task log`.
2. **Extract changes.** `git diff --stat <base>...HEAD` — categorize files (source / test / config / doc) and note added vs modified vs deleted.
3. **Semantic matching.** For each AC, locate the implementing artifact (function, file, test, doc paragraph) and cite it by `path:line`. "The diff has it somewhere" is not acceptance.
4. **Drift report.** Classify findings: **missing** (AC without artifact), **extra** (artifact without AC), **mismatch** (partial / differing semantics), **out-of-scope** (unrelated module touched).
5. **Decision.** For each finding: (a) update AC with rationale; (b) split into follow-up task; (c) revert the diff segment; (d) accept with explicit waiver in the PR description.

## Completion
- Every AC has either a matching artifact (`path:line`) or an explicit decision
- Drift report attached to PR description or task work-log
- No silent extras — every "extra" is either accepted, deferred, or removed before merge

## Example
Task AC: "add pagination to /users endpoint". Diff also modifies `/orders` and adds a Redis client. Drift report: pagination on /users — `users.go:42` ✓ matches; /orders change — *out-of-scope*, split to follow-up; Redis client — *extra*, no AC, revert (cache feature was a different ticket).

## Anti-Patterns
- Treating green CI as drift-free — tests pass on extras too
- Updating AC after the fact to retroactively justify the diff — log the change with reason, do not silently rewrite
- Reviewing only added lines — deletions and renamed symbols cause the most drift
- Skipping drift check on "small" PRs — small PRs that touch unexpected files are exactly where drift hides
