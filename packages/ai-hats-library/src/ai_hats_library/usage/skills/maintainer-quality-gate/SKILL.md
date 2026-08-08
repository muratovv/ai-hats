---
name: maintainer-quality-gate
description: Maintainer-only quality gates — pre-push e2e+smoke to master, and the review→done quality gate
ai_hats:
  # HATS-550 / HATS-686 — hook-carrier skill. Since HATS-1337 the gate is NOT
  # copied anywhere: `ai-hats self init` installs one dispatcher per event in
  # `.githooks/`, and the script below is resolved from this skill directory and
  # run in place at push time. Default (git pre-push): INSTANT pass-marker check
  # keyed to the pushed master local_sha. `--run` (scripts/run-e2e-gate.sh):
  # runs the ~27-min suite out of band, marker on pass + clean tree.
  # Hard gate: no env-var bypass; `git push --no-verify` is the only escape.
  git_hooks:
    pre-push:
      - git_hooks/pre-push-e2e-master.sh
license: MIT
---

# maintainer-quality-gate

Maintainer-only quality gates for the ai-hats codebase, delivered as
infrastructure (a git hook and a `checks:` binding) rather than agent-side
decision logic.

## What it ships

Two gates and the mechanism they share:

- `git_hooks/pre-push-e2e-master.sh` — the dual-mode **pre-push** gate on
  pushes to master (HATS-550 / HATS-686), plus its wrapper
  `scripts/run-e2e-gate.sh`.
- `hooks/done-gate.sh` — the dual-mode **`edge:review--done`** gate
  (HATS-1137). Its `--check` mode is what the `composition.checks` binding
  runs in the rack lock; its `--run` mode is what `make done-gate` invokes.
- `lib/gate-marker.sh` — the SHA pass-marker store both read and write,
  parameterised by gate name.

Both gates follow the same shape: the expensive run happens **out of band**
and leaves a marker keyed to a commit SHA; the gate on the critical path only
looks that marker up, so it is instant. A marker cannot go stale — its key is
the content it certifies, so a new commit is simply a commit with no marker.

## The review→done gate (HATS-1137)

### Who runs what, and when

**The agent**, once the work is finished and committed, in its task worktree:

    cd <task worktree> && make done-gate

That is the only step invoked by hand. It runs the whole `done-gate` stage
(minutes) and, on green, leaves a marker behind.

**The harness**, with nobody involved, when the card is moved:

1. `rack transition <ID> done`.
2. The rack kernel takes the **per-task file lock** — `<tasks_dir>/<ID>/.lock`,
   30s timeout (`ai_hats_rack.kernel.LOCK_TIMEOUT`).
3. Still in the lock, it dispatches `edge:review--done` down the priority
   ladder. The checks runner (`ai_hats.rack_consumers.CheckRunnerExtension`,
   subscriber name `checks`) sits at **priority 15**, `Phase.IN_LOCK`.
4. The runner resolves what the **active role** composes for that point — in a
   session from the session snapshot, outside one from the live library
   (ADR-0019 D9) — and finds the `maintainer` role's single `checks:` row.
5. `hook_exec.run_hook` spawns the script with **no argv at all**, `stdin`
   `/dev/null`, `cwd` = the project dir, a 20s budget
   (`EDGE_CHECK_TIMEOUT_S`), and `AI_HATS_TASK_ID` in the env. The script's own
   dispatch (`case "${1:---check}"`) therefore lands in `--check`.
6. Exit 0 → the ladder continues. Exit 2 → `AbortOperation` carrying the
   script's stdout tail verbatim, and the kernel persists nothing at all.

Every run's full transcript lands beside the card at

    <tasks_dir>/<ID>/.checks/edge-review--done~maintainer-quality-gate~hooks+done-gate.sh.log

— one file per (task, edge, binding). The leading-dot directory keeps it out of
the document registry (`docstore._is_document`), so a gate never pins its own
output into every agent's `rack context`.

### Why priority 15

The in-lock order is `single-slot(5) < frozen(8) < plan-gate(10) < checks(15) <
ownership claim(20) < scaffold/worktree(30) < ownership release(40)`
(`rack_wiring.build_rack_kernel`). On **this** edge the subscriber that matters
sits behind 15: the worktree **teardown-merge** at 30 (it subscribes to every
edge into a terminal state, and `to_state == "done"` is the one that merges).
The ownership claim at 20 subscribes only to edges into `execute`, so it never
fires here; the release at 40 does, and also runs after the gate.

So a refusal at 15 leaves the card in `review`, the card file byte-unchanged —
the kernel's single persist is always last — and **no merge commit on master**.

### `--check` — what the binding actually runs

In the script's real order; every branch below is an explicit `exit`:

1. No `AI_HATS_TASK_ID` → **refuse (2)**. The gate cannot tell which branch to
   judge, and guessing is worse than refusing.
2. No `<project>/scripts/ci-local.sh` → **refuse (2)**. No dispatcher means no
   `done-gate` stage, so no marker could ever be earned honestly. A gate that
   cannot verify must not pass; the message names both fixes (add the stage, or
   drop the `checks:` row).
3. Locate the tracker that actually holds this card — `AI_HATS_DIR`, then
   `ai-hats.yaml`'s `ai_hats_dir:`, then `.agent/ai-hats`. Anchoring on the
   card, not on a bare `tracker/`, is what stops a leaked `AI_HATS_DIR` from
   another checkout reading as "this card has no worktree". None holds it →
   **refuse (2)**.
4. `<base>/sessions/worktrees/task-<id, lowercased>.json` absent → **pass (0)**.
   The subject of the gate is the code entering master through this card; a
   doc/research card brings none.
5. The record's `worktree_path` empty or gone from disk → **pass (0)**. Rack's
   own teardown either finalizes an already-merged branch or refuses the merge
   itself, so there is no live branch content to gate.
6. `git -C <wt> rev-parse HEAD` — the **task branch's tip**, never the main
   checkout's HEAD: at priority 15 the merge has not happened, so the content
   under judgement is what the branch holds. Unresolvable → **refuse (2)**.
7. Marker for that exact SHA → **pass (0)**. Missing → **refuse (2)** with the
   copy-pasteable `cd <wt> && make done-gate`.

`AI_HATS_WORKTREE_PATH` is deliberately absent at edge points —
`hook_exec._hook_env` *removes* an unresolved value from the inherited
environment rather than letting the ambient one through, so the script resolves
the worktree from the session record instead of trusting a stale path.

### `--run` — `make done-gate`, and where the marker lands

Runs `scripts/ci-local.sh done-gate` (`lint → unit → integration →
merge-smoke`, stopping at the first red) from `git rev-parse --show-toplevel`.
On green **and a clean tree** it writes

    <git-common-dir>/ai-hats/done-gate/<HEAD sha>

carrying `sha=`, `timestamp=` and `stage=done-gate`. Two things about that path:

- **`--git-common-dir`, never `--git-dir`.** The common dir is the main
  checkout's `.git`, shared by every linked worktree — which is exactly what
  makes a marker **written inside the task worktree** visible **from the main
  checkout**, where the check runs. `--git-dir` would file it under
  `.git/worktrees/<id>/` and the check would never see it.
- It lives under `.git/`, so it is never committed. Markers are tiny; no GC.

A marker counts only when its filename and its recorded `sha=` line agree
(`gate_marker_ok`) — a half-written or hand-copied file names a commit it does
not certify.

A **dirty tree** runs the suite and writes nothing (exit 0, loudly): the gate
ran against the working tree, so what passed is not what the branch tip holds.
Commit, then re-run. Run it **in the task worktree** — the marker is keyed to
that branch's tip, which is the commit the check looks up.

The composition lives in `scripts/ci-local.sh`, not here: changing what "green
enough to be done" means is a project-side edit, and the gate script never
moves.

### Why the marker cannot go stale

Its key is the **content** it certifies. A new commit is a new SHA and simply
has no marker, so the gate refuses again by construction. There is no expiry
and no invalidation step, and none is needed — while one run covers every card
sitting on that same commit.

### The exit contract it obeys (ADR-0020 D2)

| exit                     | class   | what the runner does                                                            |
| ------------------------ | ------- | ------------------------------------------------------------------------------- |
| `0`                      | pass    | on to the next binding                                                          |
| `2`                      | refuse  | abort the transition; the stdout tail **is** the reason the agent reads         |
| `126` / `127`            | corrupt | abort; `on_error: warn` can **never** soften it (else `rm` would disarm a gate) |
| anything else, incl. `1` | broke   | governed by `on_error`: `refuse` aborts, `warn` downgrades to a work-log note   |

Refuse is `2` and not `1` because `1` is what a shell script produces **by
accident**: under `set -e` any stray non-zero command — a failed `grep`, a
missing file — ends the script with 1. Reserving 1 for "the check broke" keeps
an accident from reading as a verdict. That is also why `hooks/done-gate.sh`
runs under `set -uo pipefail` with **no** `-e`, and spells out every exit.

### Why two steps and not one

The `done-gate` stage takes minutes. The rack task lock times out at 30s and
the per-check budget is 20s (`EDGE_CHECK_TIMEOUT_S`, asserted `< LOCK_TIMEOUT`
at import time), so the heavy work cannot run in the lock at all: it would be
killed at 20s, and a peer waiting on the lock would mis-blame a concurrent
operation. Splitting it leaves the critical path as one file read — instant.

### Why a separate script from the pre-push gate

The pre-push gate's default mode reads git's pre-push protocol from **stdin**,
and the check runner gives its children `stdin=DEVNULL`. Empty stdin hits that
script's "no master target" fast path and it exits 0 — reusing it would have
produced a gate that is silently always green.

## Binding several scripts to one point

`composition.checks` takes **one script per row**, so two scripts on one point
is two rows. Nothing else changes — same point, same edge, same lock:

```yaml
composition:
  skills:
    - maintainer-quality-gate # a binding never pulls its skill in (ADR-0019 D2)
  checks:
    - skill: maintainer-quality-gate
      script: hooks/done-gate.sh
      on: [edge:review--done]
      on_error: refuse
    - skill: maintainer-quality-gate
      script: hooks/changelog-entry.sh
      on: [edge:review--done, edge:execute--review]
      on_error: warn
```

What that means at run time:

- **Order is composition order** — traits in the order the role lists them,
  then the role's own rows (`composer._resolve_traits`, then the role's own
  `declared_checks.extend`). The runner filters that tuple by point and does
  **not** re-sort it.
- **The first refusal stops the rest.** The runner raises on the spot, so a
  later binding never spawns. The one exception is a binding whose outcome is
  *broke* under `on_error: warn`: that one is downgraded to a work-log note and
  the loop continues.
- **Each binding writes its own log**,
  `.checks/<event>~<skill>~<script>.log`, with `/` escaped to `+`. Before
  HATS-1137 the name carried the edge only, so binding #2 truncated #1's file.
- `on:` is a list, so **one row may name several points**, and **several rows
  may name the same point**. Only an exact `(skill, script, point)` triple
  collapses: `check_points.resolve_checks` dedups on it, keeps the **first**
  declaration's slot, and hardens `on_error` to the strictest of the two — a
  later `warn` can never relax a gate an earlier row declared `refuse`.
- Binding to a skill the role does not compose is a loud composition error, not
  an implicit compose. `on_error: warn` is rejected outright at the
  data-protection points (`wt:pre-merge`, `wt:teardown[*]`), whose failure
  policy the catalog fixes (ADR-0019 D4).
- YAML 1.1 resolves a bare `on:` key to the boolean `True`; the row parser
  normalizes it, so both `on:` and `"on":` are accepted.

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

Run this **before** pushing master. From the repo root the hook requires
`pytest` on PATH (absent → ABORT, no marker), then runs the `lint` and `unit`
stages of `scripts/ci-local.sh` as a preamble (HATS-726 — the marker has to
mean "everything CI checks is green"; a red stage aborts before the ~25-min
tier starts). Then it sweeps the dev checkout's `build/` directory (HATS-568 —
stale wheel-build artefacts cause "File exists: build/bdist...dist-info"
collisions across worktree-tier e2e tests), then previews stale tmp cruft
(`ai-hats-wt-*`, `pytest-of-*`) via `scripts/clean-tmp-cruft.sh`
(HATS-731/HATS-570 — keeps APFS metadata ops fast on a loaded host). The
preview is **dry-run by default** — the sweeper matches every `ai-hats-wt-*` by
name and cannot tell a leaked test worktree from a live session, so the gate
never auto-deletes one; opt in to real `--force` deletion with
`AI_HATS_E2E_CLEAN_TMP=1`. Then runs:

    pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ \
           -q --tb=line --no-header -p no:cacheprovider

(parallelised with pytest-xdist when present — `-n min(cpus,8) --dist=loadgroup`
— HATS-589/592; `AI_HATS_E2E_REQUIRE_VENV=1` armed so the tier-2 venv fixture
fails-closed — HATS-645; `@pytest.mark.quarantine` known-flaky tests deselected
— HATS-676).

On **pass** AND a **clean working tree**, it writes a marker keyed to
`git rev-parse HEAD`:

| condition                             |         marker         | exit |
| ------------------------------------- | :--------------------: | :--: |
| pytest rc 0, clean tree               |       ✅ written       |  0   |
| pytest rc 5 (no tests), clean tree    | ✅ written (defensive) |  0   |
| pytest rc 0 but **dirty** tree        |        ❌ none         |  0   |
| HEAD unresolvable / marker unwritable |        ❌ none         |  0   |
| pytest failure (other rc)             |        ❌ none         |  1   |
| `lint` or `unit` preamble red         |     ❌ none, ABORT     |  1   |
| pytest not on PATH                    |     ❌ none, ABORT     |  1   |

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
- Check runner and log path: `src/ai_hats/rack_consumers.py`; in-lock ladder:
  `src/ai_hats/rack_wiring.py` → `build_rack_kernel`
- Git-hook install path (dispatchers only, HATS-1337):
  `src/ai_hats/hooks_manager.py` → `install_git_hooks`; gate resolution at
  spawn: `src/ai_hats/githooks_resolve.py`
- Sibling pattern:
  `packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh`
