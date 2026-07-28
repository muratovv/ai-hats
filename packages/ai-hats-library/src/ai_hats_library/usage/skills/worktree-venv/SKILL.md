---
name: worktree-venv
description: Provisions a Python venv inside every freshly created worktree — infrastructure only, no agent-side decision logic
ai_hats:
  # HATS-1291 — hook-carrier skill. The assembler materializes this script
  # into `library/wt-hooks/`; the lifecycle bundle runs it after
  # `git worktree add`. warn-continue: a failure never blocks worktree creation.
  worktree:
    wt_in:
      - script: hooks/provision-venv.sh
license: MIT
---

# worktree-venv

Mints a `.venv` inside each new worktree, delivered as infrastructure (a `wt_in`
lifecycle hook) rather than agent-side decision logic.

## What it ships

`hooks/provision-venv.sh`, declared as a `wt_in` hook. The assembler
materializes it into `library/wt-hooks/` at composition; the lifecycle bundle
(`ai_hats.wt_lifecycle.HookRunningLifecycle.on_created`) runs it after
`git worktree add`, with `AI_HATS_WORKTREE_PATH` naming the new worktree and
cwd set to the MAIN checkout.

The script runs `uv venv .venv`, then one editable `uv pip install` covering the
root project (`.[dev]`) and every `packages/**/pyproject.toml` it discovers.

## Why it is needed

A worktree without its own venv is not merely inconvenient — its first
`git commit` is impossible without hand-provisioning.

`sys.executable` inside a worktree is MAIN's interpreter, and MAIN's editable
install points at MAIN's source. The **in-process** test tier survives this:
`[tool.pytest.ini_options] pythonpath` redirects imports to the worktree. The
**e2e** tier does not, and cannot — it spawns `sys.executable -m ai_hats` under
`clean_env`, which strips `PYTHONPATH` *deliberately* (HATS-685: e2e must
exercise the installed artefact, not a source-tree shadow). So e2e resolves
`ai_hats` through the interpreter's own editable install → MAIN → the HATS-1242
wrong-checkout guard fires → the pre-commit smoke gate blocks the commit.

This also rules out the cheaper-looking designs: anything that works by setting
`PYTHONPATH` (including `ai-hats wt exec`) is discarded by the very tier that
fails. Measured on the HATS-1291 repro, a bare `pytest -m smoke` and one run
with `PYTHONPATH` set to the worktree workspace gave byte-identical results —
37 passed, 17 e2e errors — while a provisioned `.venv/bin/pytest` gave 54
passed, rc=0.

## The paired half

Provisioning alone changes nothing: git hooks resolve `pytest` through PATH,
which is still MAIN's. The `git-mastery` smoke hook therefore prefers
`<git-toplevel>/.venv/bin/pytest` when present. **Both halves are required**
(HATS-1245); either one alone leaves the commit blocked.

## Failure policy

`wt_in` is warn-continue (ADR-0012 D3/D7) — a create-time hook failure is
friction, not data loss. Every declining path exits 0 with a note:

| condition                                | behaviour                                       |
| ---------------------------------------- | ----------------------------------------------- |
| no `pyproject.toml` at the worktree root | skip (not a Python project)                     |
| `.venv/bin/python` already present       | skip (idempotent — safe to re-run)              |
| `uv` not on PATH                         | skip, print the manual recipe                   |
| `uv venv` / editable install fails       | skip, print the command to finish the job       |
| exceeds the 45s `wt_in` budget           | runner times out → WARN, worktree still created |

Each leaves a worktree without `.venv` — the pre-HATS-1291 status quo, never
worse. Cost when it does run is small: measured at ~1–2s on a warm uv cache.

## Known limitation — dev-tool version drift

The install resolves fresh, so a worktree venv can carry *newer* dev tools than
a long-lived main venv. Observed during HATS-1291: main had `ruff 0.15.22`, the
new worktree got `0.16.0`, and `ruff format --check` disagreed across hundreds
of files purely from the version gap. `uv sync --frozen` would pin this, but
this repo gitignores `uv.lock` by policy ("local dev tool artifact") and
`pyproject.toml` leaves dev tools unpinned (`ruff>=0.4`), so there is no lock to
respect. Run repo-wide format/lint checks with the main checkout's tool when the
two disagree, or pin the tool in `pyproject.toml`.

## Why a separate skill

Per `rule_core_vs_usage_split`, `uv` and editable installs are Python-specific,
so this cannot live in the worktree engine (`wt_effects.py`) — that binding runs
for every consuming project, including non-Python ones. It is attached to the
`maintainer` role, matching the `maintainer-quality-gate` precedent. Promotion
to the `dev::python` trait waits for a second confirmed consumer.

## References

- Plan: `.agent/ai-hats/tracker/backlog/tasks/HATS-1291/plan.md`
- Paired hook: `library/core/skills/git-mastery/git_hooks/pre-commit-smoke.sh`
- Hook contract: `docs/how-to-extend.md#worktree-lifecycle-hooks`
- Lifecycle policy: `docs/adr/0012-worktree-data-transfer.md` (D3/D7)
- Sibling hook-carrier skill: `library/usage/skills/maintainer-quality-gate/SKILL.md`
