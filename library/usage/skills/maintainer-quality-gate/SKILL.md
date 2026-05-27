# maintainer-quality-gate

Maintainer-only quality gates for the ai-hats codebase, delivered as
infrastructure (git hooks) rather than agent-side decision logic.

## What it ships

A single `pre-push` git hook —
`git_hooks/pre-push-e2e-master.sh` — installed by the assembler into
`.githooks/pre-push.d/maintainer-quality-gate-pre-push-e2e-master.sh`
during composition.

## When the gate fires

The hook reads the standard pre-push protocol on stdin:

    <local_ref> <local_sha> <remote_ref> <remote_sha>

It triggers iff **at least one line** has:

- `remote_ref == refs/heads/master`, AND
- `local_sha` is not all-zero (i.e. not a branch deletion).

Pushes to any other branch, deletions of master, or empty stdin are
fast-paths to exit 0 — pytest is never invoked in those cases.

## What it runs

When triggered, from the repo root, the hook first sweeps the dev
checkout's `build/` directory (HATS-568 — stale wheel-build artefacts
from prior `pip install` runs cause "File exists: build/bdist...dist-info"
collisions across ~5 worktree-tier e2e tests). The sweep is idempotent
`rm -rf build/` and emits a one-line stderr note when something was
actually removed.

Then:

    pytest -m "integration or smoke" tests/e2e/ tests/smoke/ \
           -q --tb=line --no-header -p no:cacheprovider

Exit codes:

| pytest rc | hook behavior     | Reason                                   |
|----------:|-------------------|------------------------------------------|
| 0         | exit 0 (allow)    | All gated tests passed.                  |
| 5         | exit 0 (allow)    | "No tests collected" — folders empty, defensive. |
| other     | exit 1 (block)    | Test failure tail printed to stderr.     |

If `pytest` is not on `PATH`, the hook **blocks** with a clear message —
silent skip would let `pip uninstall pytest` bypass the gate.

## How to bypass

You **can't** via env-var. There is no `AI_HATS_E2E_SKIP=1` and no
`--ack` override. This is intentional (see HATS-550 plan).

The only escape is the standard `git push --no-verify`, which disables
**every** hook on the chain (privacy, shared-state, this gate, anything
else attached). Use it only for hotfix paths where you accept the full
loss of the safety net.

## Why a separate skill

Per `rule_core_vs_usage_split`, this is project-specific (only the
ai-hats codebase has `tests/e2e/` + `tests/smoke/`). It belongs in
`library/usage/`, attached to the `maintainer` role. Bundling it into
`git-mastery` (universal core skill) would push a no-op hook onto every
consuming project.

## References

- Plan: `.agent/ai-hats/tracker/backlog/tasks/HATS-550/plan.md`
- E2e test: `tests/e2e/test_prepush_e2e_master_gate.py`
- Assembler install path: `src/ai_hats/assembler.py` → `_install_git_hooks`
- Sibling pattern: `library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh`
