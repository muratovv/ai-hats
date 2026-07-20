---
name: git-mastery
description: Advanced git operations covering branches, conventional commits, worktrees, and rebasing. Use for any git operation beyond basic add/commit/push, for branch management, rebasing, and conflict resolution, or when setting up commit conventions for a project.
ai_hats:
  # Skill-contributed git hooks (HATS-088 framework). The assembler installs
  # these into the project's .githooks/<event>.d/ at composition time.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-privacy.sh
      - git_hooks/pre-commit-smoke.sh
      # HATS-444: enforce docs/INDEX.md freshness — block commits that
      # add/delete/rename docs/*.md without staging INDEX.md alongside.
      - git_hooks/pre-commit-docs-index.sh
      # HATS-470: forbid raw path.unlink / shutil.rmtree / .rmdir under
      # src/ai_hats/ outside safe_delete.py without an inline
      # `# safe-delete: ok <reason>` marker. No-op on non-ai-hats projects.
      - git_hooks/pre-commit-no-raw-destructive.sh
    # HATS-437: provider-agnostic safety net for `git push --force`. Claude
    # gets a stronger PreToolUse-level block via ClaudeProvider auto-wiring;
    # this pre-push hook protects Gemini sessions and direct-terminal pushes.
    pre-push:
      - git_hooks/pre-push-shared-state.sh
    # No post-merge/post-checkout self-heal: .githooks/ drift is re-healed by the
    # session-start managed-hook net (Assembler.sync_hooks), not a git-event hook.
license: MIT
---

# Git Mastery

## When to Use

Basic add/commit/push needs no skill — reach here only for harder operations:
rebases, conflict resolution, branch strategy, conventional-commit setup,
cherry-pick/backport. The git-*worktree* lifecycle (create/merge/discard a task
worktree) is owned by **worktree-isolation** — use that for isolation, this for
history and branch manipulation.

## Capabilities

- Create feature branches with naming conventions
- Stage and commit with conventional commit format
- Resolve merge conflicts
- Manage worktrees for parallel development
- Interactive rebase for clean history
- Cherry-pick and backport changes

## Conventions

- Branch naming: `type/description` (e.g., `feat/add-auth`, `fix/login-bug`)
- Conventional commits: `type(scope): description`
- Types: feat, fix, refactor, docs, test, chore, ci
- Commit messages explain WHY, not WHAT
- Commit frequently with atomic changes
- Check status before operations
- Never force push without explicit approval

## Pre-Commit Checklist

Before every `git add` / `git commit`:

1. Run `git status` and `git diff --stat` to review what will be committed.
2. For each file verify:
   - Project code? (not agent config like ai-hats.yaml, .agent/)
   - Meaningful content? (not empty placeholders)
   - Belongs in the repo? (not .env, credentials, temp files)

"Don't commit" failure modes (agent configs, empty placeholders, `.gitignore`
material) are listed under **Anti-Patterns**.

## Pre-Commit Privacy Review

The `pre-commit-privacy.sh` hook (installed into `.githooks/pre-commit.d/`
whenever a role composing `git-mastery` is applied) scans staged additions and
**blocks the commit** on:

| Pattern                | Examples                                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Absolute home paths    | `/Users/foo/...`, `/home/bar/...`                                                                                        |
| API key prefixes       | `sk-...`, `ghp_...`, `AKIA...`, `xox[bp]-...`, `AIza...`, `glpat-...`                                                    |
| Bearer tokens          | `Authorization: Bearer ...`                                                                                              |
| Env-style secrets      | `*_KEY=`, `*_TOKEN=`, `*_SECRET=`, `*_PASSWORD=`, `*_API=`                                                               |
| Email addresses        | any RFC-shaped address                                                                                                   |
| Private keys           | `-----BEGIN ... PRIVATE KEY-----` (PEM/OpenSSH headers)                                                                  |
| DB URIs with creds     | `postgres://user:pass@…`, `mysql://…`, `mongodb://…`, `redis://…`                                                        |
| Cloud / SaaS tokens    | GitHub `gho_/ghs_/ghu_/ghr_/github_pat_`, AWS secret keys, Slack webhooks, Stripe `sk_live_`, SendGrid, npm tokens, JWTs |
| Claude session content | JSONL markers — `"sessionId"`/`"requestId"`, `"cwd": "/…"`, `"parentUuid"`/`"toolUseResult"`                             |

Soft warning (printed, non-blocking): new file in `tests/fixtures/` larger than 5 KB.

### When the hook fires

1. **Show the user** the full hook report first.
2. **Don't blindly override** — ask whether the hit is a true positive.
3. **True leak** → fix the file, re-stage, retry.
4. **False positive on a single line** → append the inline allow-marker; only that line is skipped:
   ```
   <the flagged line>   # ai-hats: allow-secret
   ```
5. **False positive across a whole file** that will recur → add a glob to
   `.privacy-allowlist` (project root or `.githooks/`) and document the reason in the same commit.
6. **Last resort — whole commit** → override once, after showing the user what was flagged:
   ```bash
   AI_HATS_PRIVACY_ACK=1 git commit ...
   ```

## Pre-Commit Smoke Gate

The `pre-commit-smoke.sh` hook runs `pytest -m smoke` whenever the **active
backlog task** carries the `integration` tag.

### How it works

1. Hook reads `<ai_hats_dir>/tracker/backlog/tasks/*/task.yaml` for any task in `execute` state.
2. If such a task has `- integration` in its `tags:`, it runs `pytest -m smoke -q --tb=line --no-header`.
3. All smoke tests pass → commit proceeds.
4. Any test fails → commit blocked with a short report.
5. No active task, or active task lacks the `integration` tag → silent no-op (exit 0).
6. pytest not installed or no tests marked `@pytest.mark.smoke` → silent pass.

### Setting the `integration` tag

The agent decides during **brainstorm** or **plan** (see **hatrack**).
Heuristic: tag `integration` when the task touches an external tool, process,
network call, sub-agent invocation, or filesystem writes outside `.agent/`.

```bash
# Harness bash lacks an activated venv — resolve a runner first (HATS-790: no
# bin/ai-hats console script, so the fallback runs the venv interpreter's module):
ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
ah task update <ID> --add-tag integration
```

### Override

```bash
AI_HATS_SMOKE_SKIP=1 git commit ...
```

## Pre-Push Force-Push Guard

The `pre-push-shared-state.sh` hook blocks a **non-fast-forward (force) push** —
an irreversible history rewrite — unless acknowledged. Provider-agnostic: it nets
Gemini sessions and direct-terminal pushes that Claude's stronger PreToolUse block
doesn't cover. On a block it prints recovery guidance and the override:

```bash
AI_HATS_SHARED_STATE_ACK=1 git push --force ...
```

As with the privacy override, only acknowledge after the user has confirmed — see
`rule_pause_before_shared_state_write`.

### Other infra hooks (silent by design)

Two further pre-commit hooks ship here and stay out of the way unless tripped:

- `pre-commit-docs-index.sh` (HATS-444) — blocks adding/deleting/renaming a
  `docs/*.md` without staging `docs/INDEX.md` alongside.
- `pre-commit-no-raw-destructive.sh` (HATS-470) — blocks raw `path.unlink` /
  `shutil.rmtree` / `.rmdir` under `src/ai_hats/` outside `safe_delete.py` unless
  the line carries `# safe-delete: ok <reason>`. No-op on non-ai-hats projects.

## Hook Self-Heal (session start)

`.githooks/` is generated by composition and **not** tracked in git, so it drifts
after `git merge` / `git pull` / `git checkout`. It is re-healed by the
**session-start managed-hook drift net** (`WrapRunner._resync_managed_hooks` →
`Assembler.sync_hooks`): on every ai-hats launch it re-materializes any drifted
managed hook surface — git, runtime (`.claude/settings.json` + the
`library/hooks/` scripts), and worktree hooks — and prints a one-line note of
what it healed. Drift-gated and fail-open: a clean start is silent, a heal
failure never blocks the launch.

- There is **no** `ai-hats self sync-hooks` command and **no** post-merge /
  post-checkout git hook anymore (HATS-833 consolidated all healing to session
  start). A `git pull` + `git commit` with no ai-hats launch between can run stale
  git hooks until the next launch heals them — launch ai-hats (or `ai-hats self
  init`) to refresh sooner.
- **Bootstrap:** a fresh clone still needs one initial `ai-hats self init` to
  install hooks at all; they self-maintain on each launch after.
- A "hooks corrupt" message on push means the gate's fail-closed dispatcher caught
  a **missing** managed hook: run `ai-hats self init` to repair.

## Anti-Patterns

- Force push without approval — can destroy team members' work
- Giant commits mixing multiple concerns — keep commits atomic
- Commit messages describing what ("changed X") instead of why
- Committing agent config files to project repos — local agent state
- Committing empty/placeholder files — wait for real content
- Bypassing the privacy hook (`AI_HATS_PRIVACY_ACK=1`) without showing the user what was flagged — it is a peer review, not an obstacle (it exists because agents proved they could not catch leaks unaided, HATS-001 retro)
- Declaring "done" on integration work without running the real-path smoke test at least once
