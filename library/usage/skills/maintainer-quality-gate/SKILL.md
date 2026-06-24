---
name: maintainer-quality-gate
description: Maintainer-only quality gates — pre-push e2e+smoke when pushing to master
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
---
# maintainer-quality-gate

Maintainer-only quality gates for the ai-hats codebase, delivered as
infrastructure (git hooks) rather than agent-side decision logic.

## What it ships

A single dual-mode `pre-push` script —
`git_hooks/pre-push-e2e-master.sh` — installed by the assembler into
`.githooks/pre-push.d/maintainer-quality-gate-pre-push-e2e-master.sh`
during composition, plus an ergonomic wrapper `scripts/run-e2e-gate.sh`.

## Why two modes (HATS-686)

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

## How it works

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

| condition                          | marker | exit |
|------------------------------------|:------:|:----:|
| pytest rc 0, clean tree            |  ✅ written | 0 |
| pytest rc 5 (no tests), clean tree |  ✅ written (defensive) | 0 |
| pytest rc 0 but **dirty** tree     |  ❌ none | 0 |
| pytest failure (other rc)          |  ❌ none | 1 |
| pytest not on PATH                  |  ❌ none, ABORT | 1 |

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
- E2e test: `tests/e2e/test_prepush_e2e_master_gate.py`
- Wrapper: `scripts/run-e2e-gate.sh`
- Assembler install path: `src/ai_hats/assembler.py` → `_install_git_hooks`
- Sibling pattern: `library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh`
