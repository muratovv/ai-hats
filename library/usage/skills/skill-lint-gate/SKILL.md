---
name: skill-lint-gate
description: Pre-commit agnix validation of staged library SKILL.md files for skill-authoring roles. Use when composing the maintainer or role-curator role, when a commit touches a library/**/SKILL.md, or when diagnosing why a commit was blocked by the skill-lint hook.
ai_hats:
  # HATS-617 — hook-carrier skill. The assembler installs the script below
  # into `.githooks/pre-commit.d/` at composition time. It lints STAGED
  # `library/**/SKILL.md` (excluding the golang-* pack) with agnix against the
  # repo-root `.agnix.toml` and blocks on agnix errors. Fail-open if
  # agnix/node absent; per-commit override AI_HATS_SKILL_LINT_ACK=1.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-skill-lint.sh
license: MIT
---
# Skill Lint Gate

Pure-infrastructure hook-carrier skill. It contributes one git pre-commit hook
that runs [agnix](https://github.com/avifenesh/agnix) over the SKILL.md files a
commit touches, and blocks the commit when agnix reports an error. There is no
agent-side decision logic here — the value is delivered entirely through
composition.

## What it gates

- **Scope:** only STAGED `library/**/SKILL.md` files, excluding the third-party
  `golang-*` pack (handled separately — HATS-626/627).
- **Policy:** the repo-root `.agnix.toml` (target `claude-code`; rules
  `XML-001` / `XP-SK-001` / `VER-001` disabled as convention false positives).
- **Effect:** agnix non-zero exit on a staged skill blocks the commit, printing
  the diagnostics.

Because the scope is the changed files, the gate only fires on commits that
touch an authored skill — it never retro-blocks the pre-existing backlog.

## Who gets it

Installed via the `skill-engineer` trait, composed only by the `maintainer` and
`role-curator` roles. Other roles do not receive the hook. The server-side
counterpart (CI `lint-skills` job) is HATS-627.

## How to bypass

Fix the flagged issue, or — after confirming the skill is intentionally
non-conforming — skip the gate for a single commit:

```bash
AI_HATS_SKILL_LINT_ACK=1 git commit ...
```

If `agnix` / `node` is not installed the hook is a loud no-op (fail-open): it
prints a SKIPPED notice and allows the commit, so a missing optional dev tool
never wedges work. Inside an ai-hats agent node is present, so the gate is live.

## Overrides

- `AI_HATS_SKILL_LINT_ACK=1` — allow the current commit despite findings.
- `AI_HATS_SKILL_LINT_CMD` — override the agnix invocation (default
  `npx --yes agnix@0.29.0`); used by the test suite to inject a stub.
