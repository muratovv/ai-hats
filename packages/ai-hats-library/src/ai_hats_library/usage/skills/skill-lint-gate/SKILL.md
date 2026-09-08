---
name: skill-lint-gate
description: Pre-commit gate over staged library `SKILL.md`. Use when composing a role that carries the `skill-engineer` trait, or when diagnosing why the skill-lint hook blocked a commit.
ai_hats:
  # hook-carrier skill. The assembler installs the script below
  # into `.githooks/pre-commit.d/` at composition time. Over STAGED
  # `{ai_hats_library,library,libraries}/**/SKILL.md` it runs two checks: (1) license/provenance
  # regression-guard (always-on, pure bash, covers golang-*); (2) agnix
  # spec-lint against the repo-root `.agnix.toml` (excludes golang-*, fail-open
  # if agnix/node absent). Per-commit override AI_HATS_SKILL_LINT_ACK=1.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-skill-lint.sh
license: MIT
---

# Skill Lint Gate

Pure-infrastructure hook-carrier skill. It contributes one git pre-commit hook
that runs two checks over the SKILL.md files a commit touches and blocks on
either. There is no agent-side decision logic here — the value is delivered
entirely through composition.

## What it gates

Over STAGED `{ai_hats_library,library,libraries}/**/SKILL.md` (changed-files scope, so the gate never
retro-blocks the pre-existing backlog), the hook runs, in order:

1. **License/provenance regression-guard** (pure bash, always-on, NOT
   fail-open). Blocks when a skill has no non-empty `license:` frontmatter, or is
   declared-derived (sibling `metadata.yaml` has an `upstream:` block) but is
   missing its co-located `LICENSE` file. Its scope deliberately **includes** the
   `golang-*` pack — that pack is exactly the third-party derived content this
   guard protects. It exists to keep the licensing backfill from
   silently regressing.
2. **agnix spec-lint** ([agnix](https://github.com/avifenesh/agnix)
   against the repo-root `.agnix.toml`; target `claude-code`; rules `XML-001` /
   `XP-SK-001` / `VER-001` disabled as convention false positives). **Excludes**
   the `golang-*` pack (drift handled separately). Fail-open if
   agnix/node is absent. agnix non-zero exit blocks the commit.

## Who gets it

Whatever composes this skill, and nothing else: the hook is written into
`.githooks/pre-commit.d/` at composition time, so a role that does not carry
it never sees the gate. The server-side counterpart is the CI `lint-skills` job.

## How to bypass

Fix the flagged issue, or — after confirming the skill is intentionally
non-conforming — skip the gate for a single commit:

```bash
AI_HATS_SKILL_LINT_ACK=1 git commit ...
```

If `agnix` / `node` is not installed, only the **agnix** check is a loud no-op
(fail-open): it prints a SKIPPED notice and allows the commit, so a missing
optional dev tool never wedges work. The license/provenance guard is pure bash
and always runs. Inside an ai-hats agent node is present, so both checks are live.

## Overrides

- `AI_HATS_SKILL_LINT_ACK=1` — allow the current commit despite findings.
- `AI_HATS_SKILL_LINT_CMD` — override the agnix invocation (default
  `npx --yes agnix@0.29.0`); used by the test suite to inject a stub.
