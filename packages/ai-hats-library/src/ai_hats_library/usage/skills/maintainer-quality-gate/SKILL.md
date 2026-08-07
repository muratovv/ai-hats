---
name: maintainer-quality-gate
description: Maintainer-only quality gates — pre-push e2e+smoke to master, and the review→done quality gate
ai_hats:
  # HATS-550 / HATS-686 — hook-carrier skill. The assembler installs the
  # dual-mode pre-push script into `.githooks/pre-push.d/` at composition
  # time. Default (git pre-push): INSTANT pass-marker check keyed to the
  # pushed master local_sha. `--run` (scripts/run-e2e-gate.sh): runs the
  # ~27-min suite out of band and writes the marker on pass + clean tree.
  # Hard gate: no env-var bypass; `git push --no-verify` is the only escape.
  git_hooks:
    pre-push:
      - git_hooks/pre-push-e2e-master.sh
license: MIT
---

# maintainer-quality-gate

Maintainer-only quality gates for the ai-hats codebase, delivered as
infrastructure (git hooks) rather than agent-side decision logic.

## What it ships

Two gates and the mechanism they share:

- `git_hooks/pre-push-e2e-master.sh` — the dual-mode **pre-push** gate on
  pushes to master (HATS-550 / HATS-686), plus its wrapper
  `scripts/run-e2e-gate.sh`.
- `hooks/done-gate.sh` — the dual-mode **`edge:review--done`** gate
  (HATS-1137), bound through `composition.checks` and run by
  `make done-gate`.
- `lib/gate-marker.sh` — the SHA pass-marker store both read and write,
  parameterised by gate name.

Both gates follow the same shape: the expensive run happens **out of band**
and leaves a marker keyed to a commit SHA; the gate on the critical path only
looks that marker up, so it is instant. A marker cannot go stale — its key is
the content it certifies, so a new commit is simply a commit with no marker.

## The review→done gate (HATS-1137)

`rack transition <ID> done` runs `hooks/done-gate.sh` **inside the per-task
lock**, at priority 15 — before ownership (20) and before the worktree merge
(30), so a refusal leaves neither. The lock budget is 30s and the check budget
20s, which is why nothing heavier than a marker lookup can run there.

### Check mode (default — what the binding runs)

1. `AI_HATS_TASK_ID` → `<ai-hats-dir>/sessions/worktrees/task-<id>.json`.
2. **No worktree record → PASS.** The subject of the gate is the code entering
   master through this card; a card with no worktree contributes none.
3. Otherwise the task worktree's branch tip (`git -C <wt> rev-parse HEAD`) —
   *not* the main checkout's HEAD, because at priority 15 the merge has not
   happened and the content under judgement is the branch's.
4. Marker for that exact SHA → PASS. **Missing → refuse (exit 2)** with the
   run command in the reason.
5. **No `scripts/ci-local.sh` → refuse.** A gate that cannot verify must not
   pass, so this names both the fix and how to unbind rather than failing open.

`AI_HATS_WORKTREE_PATH` is deliberately absent at edge points, so the script
resolves the worktree from the session record itself.

### Run mode — `make done-gate`

Runs `scripts/ci-local.sh done-gate` (lint → unit → integration → merge-smoke)
and, on green **and a clean tree**, writes the marker for HEAD. Run it **in the
task worktree**: the marker is keyed to that branch's tip, which is what the
check looks up. A dirty tree runs the suite and writes nothing — the marker has
to describe committed content.

The composition lives in `scripts/ci-local.sh`, not here: changing what "green
enough to be done" means is a project-side edit, and the gate script never
moves.

### Why a separate script from the pre-push gate

The pre-push gate's default mode reads git's pre-push protocol from **stdin**,
and the check runner gives its children `stdin=DEVNULL`. Empty stdin hits that
script's "no master target" fast path and it exits 0 — reusing it would have
produced a gate that is silently always green.

## The master pre-push gate — why two modes (HATS-686)

The gate suite takes ~27 min. Running it **inside** the pre-push hook is
incompatible with pushing to GitHub over SSH: git opens the SSH connection
for ref-advertisement *before* running the hook, then runs the hook, then
sends the pack. GitHub closes the idle connection after ~30s, so any hook
that takes longer dies with `Connection closed by remote host` → SIGPIPE
(exit 141) and the pack is never sent. This happened twice in HATS-684;
client-side `ServerAliveInterval` (tried 60 **and** 15) did **not** fix it
(the 2022 community keepalive workaround no longer works on GitHub — do not
re-attempt it). So the slow suite must run **out of band**, not while git
holds the connection open.

The fix decouples the run from the push via a pass-marker keyed to the
commit SHA, preserving the HATS-550 "no-broken-master, no-bypass" contract.

## How the pre-push gate works

### Run mode — `scripts/run-e2e-gate.sh` (or `… --run`)

Run this **before** pushing master. From the repo root the hook first sweeps
the dev checkout's `build/` directory (HATS-568 — stale wheel-build artefacts
cause "File exists: build/bdist...dist-info" collisions across worktree-tier
e2e tests), then previews stale tmp cruft (`ai-hats-wt-*`, `pytest-of-*`) via
`scripts/clean-tmp-cruft.sh` (HATS-731/HATS-570 — keeps APFS metadata ops fast
on a loaded host). The preview is **dry-run by default** — the sweeper matches
every `ai-hats-wt-*` by name and cannot tell a leaked test worktree from a live
session, so the gate never auto-deletes one; opt in to real `--force` deletion
with `AI_HATS_E2E_CLEAN_TMP=1`. Then runs:

    pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ \
           -q --tb=line --no-header -p no:cacheprovider

(parallelised with pytest-xdist when present — `-n min(cpus,8) --dist=loadgroup`
— HATS-589/592; `AI_HATS_E2E_REQUIRE_VENV=1` armed so the tier-2 venv fixture
fails-closed — HATS-645; `@pytest.mark.quarantine` known-flaky tests deselected
— HATS-676).

On **pass** AND a **clean working tree**, it writes a marker keyed to
`git rev-parse HEAD`:

| condition                          |         marker         | exit |
| ---------------------------------- | :--------------------: | :--: |
| pytest rc 0, clean tree            |       ✅ written       |  0   |
| pytest rc 5 (no tests), clean tree | ✅ written (defensive) |  0   |
| pytest rc 0 but **dirty** tree     |        ❌ none         |  0   |
| pytest failure (other rc)          |        ❌ none         |  1   |
| pytest not on PATH                 |     ❌ none, ABORT     |  1   |

The clean-tree invariant matters: the gate builds wheels from the *working
tree*, so a marker is only honest when tree == HEAD == the SHA you will push.
A dirty tree runs the suite but writes no marker (commit first, re-run).

### Check mode — the pre-push hook (default)

When you `git push`, git invokes the hook with the standard protocol on stdin:

    <local_ref> <local_sha> <remote_ref> <remote_sha>

For every line targeting `refs/heads/master` with a non-zero `local_sha`
(i.e. not a deletion), the hook requires a valid marker for that `local_sha`
under `<git-common-dir>/ai-hats/e2e-gate/`. All present → allow (exit 0,
**instant** — no pytest, no network). Any missing → block (exit 1) with the
run command. Pushes to other branches, master deletions, and empty stdin are
fast-path no-ops.

Markers live under `.git/` (never committed, shared across worktrees via
`git rev-parse --git-common-dir`). They are tiny; no GC is performed.

## Typical flow

    scripts/run-e2e-gate.sh        # ~27 min, out of band; writes the marker on green
    git push origin master         # pre-push check passes instantly

## How to bypass

You **can't** in the normal flow via env-var: there is no `AI_HATS_E2E_SKIP=1`
and no `--ack` override (HATS-550 intent).

Forging a marker (`touch <git-common-dir>/ai-hats/e2e-gate/<sha>` with a
matching `sha=` line) *is* a bypass, but it is the moral equivalent of
`git push --no-verify`: a deliberate local act by the trusted maintainer, not
an accidental normal-flow skip. The contract guarded here is "no green without
a real run **or** an explicit override".

The standard escape stays `git push --no-verify`, which disables **every**
hook on the chain (privacy, shared-state, this gate, anything else attached).

## Why a separate skill

Per `rule_core_vs_usage_split`, this is project-specific (only the
ai-hats codebase has `tests/e2e/` + `tests/smoke/`). It belongs in
`library/usage/`, attached to the `maintainer` role. Bundling it into
`git-mastery` (universal core skill) would push a no-op hook onto every
consuming project.

## References

- Plan (decoupling): `.agent/ai-hats/tracker/backlog/tasks/HATS-686/plan.md`
- Plan (gate origin): `.agent/ai-hats/tracker/backlog/tasks/HATS-550/plan.md`
- Plan (review→done gate): `.agent/ai-hats/tracker/backlog/tasks/HATS-1137/plan.md`
- E2e tests: `tests/e2e/test_prepush_e2e_master_gate.py`,
  `tests/e2e/test_done_gate.py`
- Wrapper: `scripts/run-e2e-gate.sh`
- Gate composition: `scripts/ci-local.sh` → `done-gate` stage
- Binding shape and outcome policy: ADR-0019 D2 / D4; exit contract: ADR-0020 D2
- Assembler install path: `src/ai_hats/assembler.py` → `_install_git_hooks`
- Sibling pattern: `library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh`
