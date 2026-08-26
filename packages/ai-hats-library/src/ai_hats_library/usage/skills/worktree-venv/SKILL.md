---
name: worktree-venv
description: Provisions a Python venv inside every freshly created worktree — infrastructure only, no agent-side decision logic
ai_hats:
  # HATS-1291 — hook-carrier skill. The lifecycle bundle spawns this script in
  # place from the skill dir after `git worktree add` (HATS-1269 retired the
  # materialized wt-hooks tree; `migrations.py` sweeps what it left behind).
  worktree:
    wt_in:
      - script: hooks/provision-venv.sh
license: MIT
---

# worktree-venv

`hooks/provision-venv.sh` runs after `git worktree add` and mints a `.venv` in
the new worktree: `uv venv`, then one editable `uv pip install` over the root
project and every `packages/**/pyproject.toml`.

Without it the worktree's `sys.executable` is the main checkout's, whose
editable install points at main — so the e2e tier (which strips `PYTHONPATH` by
design, HATS-685) tests the wrong source and the HATS-1242 guard blocks the
first commit. The paired half is the `git-mastery` smoke hook preferring
`<git-toplevel>/.venv/bin/pytest`; neither half works alone (HATS-1245).

## Conventions

- **Warn-continue** (ADR-0012 D3/D7). Every declining path exits 0: no root
  `pyproject.toml`, `.venv/bin/python` already present (idempotent), no `uv` on
  PATH, or a failed install. Each leaves a worktree without `.venv` — the
  pre-HATS-1291 status quo, never worse.
- **Bounded** by the 45s `wt_in` budget; a timeout is a WARN, not a failed
  create. Cost when it runs: ~1–2s on a warm uv cache.
- **Unpinned by necessity** — the repo gitignores `uv.lock`, so the install
  resolves fresh and a worktree venv may carry newer dev tools than an older
  main venv.

## References

- Rationale, measurements and the rejected designs: `.agent/ai-hats/tracker/backlog/tasks/HATS-1291/plan.md`
- Paired hook: `packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-commit-smoke.sh`
- Hook contract: `docs/how-to-extend.md#worktree-lifecycle-hooks`
