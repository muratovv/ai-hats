---
name: git-mastery
description: Advanced git operations covering branches, conventional commits, worktrees, and rebasing. Use for any git operation beyond basic add/commit/push, for branch management, rebasing, and conflict resolution, or when setting up commit conventions for a project.
---
# Git Mastery

Advanced git operations for development workflow.

## When to Use
- Any git operation beyond basic add/commit/push
- Branch management, rebasing, conflict resolution
- Setting up commit conventions for a project

## Capabilities
- Create feature branches with naming conventions
- Stage and commit with conventional commit format
- Resolve merge conflicts intelligently
- Manage worktrees for parallel development
- Interactive rebase for clean history
- Cherry-pick and backport changes

## Conventions
- Branch naming: `type/description` (e.g., `feat/add-auth`, `fix/login-bug`)
- Conventional commits: `type(scope): description`
- Types: feat, fix, refactor, docs, test, chore, ci
- Write meaningful commit messages explaining WHY, not WHAT
- Commit frequently with atomic changes
- Always check status before operations
- Never force push without explicit approval

## Pre-Commit Checklist
Before every `git add` / `git commit`:
1. Run `git status` and `git diff --stat` to review what will be committed
2. For each file, verify:
   - Is this project code? (not agent config like ai-hats.yaml, .agent/)
   - Does this file have meaningful content? (not empty placeholders)
   - Should this file be in the repo? (not .env, credentials, temp files)
3. Do NOT commit:
   - Empty or placeholder files (empty CLAUDE.md, stub configs)
   - Agent framework configs (ai-hats.yaml, .agent/, profile.json)
   - Files that belong in .gitignore

## Pre-Commit Privacy Review (HATS-083)

This skill ships an automated pre-commit hook (`pre-commit-privacy.sh`)
that the framework installs into `.githooks/pre-commit.d/` whenever a role
composing `git-mastery` is applied. It scans staged additions and **blocks
the commit** when it finds:

| Pattern | Examples |
|---|---|
| Absolute home paths | `/Users/foo/...`, `/home/bar/...` |
| API key prefixes | `sk-...`, `ghp_...`, `AKIA...`, `xox[bp]-...`, `AIza...`, `glpat-...` |
| Bearer tokens | `Authorization: Bearer ...` |
| Env-style secrets | `*_KEY=`, `*_TOKEN=`, `*_SECRET=`, `*_PASSWORD=`, `*_API=` |
| Email addresses | any RFC-shaped address |

Soft warnings (printed but non-blocking):
- New file in `tests/fixtures/` larger than 10 KB

### When the hook fires

1. **Show the user** the full report from the hook output before doing anything else.
2. **Do not blindly override.** Ask whether the hit is a true positive.
3. If the hit is a **true leak** → fix the file, re-stage, retry the commit.
4. If the hit is a **false positive** that will recur → add a glob to
   `.privacy-allowlist` (project root or `.githooks/`) and document the
   reason in the same commit.
5. If the hit is a **one-off false positive** → override for that single
   commit only:
   ```bash
   AI_HATS_PRIVACY_ACK=1 git commit ...
   ```

### Anti-pattern: silent override
Setting `AI_HATS_PRIVACY_ACK=1` without showing the findings to the user
defeats the entire purpose of the hook. The hook exists because the agent
already proved it could not catch leaks unaided (HATS-001 retro). Treat
its findings as a peer review, not as an obstacle.

## Pre-Commit Smoke Gate (HATS-081)

A second pre-commit hook (`pre-commit-smoke.sh`) runs `pytest -m smoke`
whenever the **active ai-hats task** carries the `integration` tag.

### How it works
1. Hook reads `<ai_hats_dir>/tracker/backlog/tasks/*/task.yaml` for any task in `execute` state.
2. If such a task has `- integration` in its `tags:`, the hook runs
   `pytest -m smoke -q --tb=line --no-header`.
3. If all smoke tests pass → commit proceeds.
4. If any test fails → commit is blocked with a short report.
5. If no task is active, or the active task has no `integration` tag →
   hook is a silent no-op (exit 0).
6. If pytest is not installed or no tests are marked `@pytest.mark.smoke` →
   silent pass.

### Setting the `integration` tag
The decision is made by the agent during **brainstorm** or **plan** (see
backlog-manager). Heuristic: tag as `integration` when the task touches
integration with an external tool, process, network call, sub-agent
invocation, or filesystem writes outside `.agent/`.

```bash
# Harness bash lacks an activated venv — resolve the binary first:
AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
"$AH" task update <ID> --add-tag integration
```

### Override
```bash
AI_HATS_SMOKE_SKIP=1 git commit ...
```

## Anti-Patterns
- Force push without approval — can destroy team members' work
- Giant commits mixing multiple concerns — keep commits atomic
- Commit messages describing what ("changed X") instead of why
- Committing agent config files to project repos — these are local agent state
- Committing empty/placeholder files — wait until they have real content
- Bypassing the privacy hook (`AI_HATS_PRIVACY_ACK=1`) without showing the user what was flagged
- Declaring "done" on integration work without running the real-path smoke test at least once
