---
name: maintainer-quality-gate
description: Maintainer-only quality gates — pre-push e2e+smoke to master, plus the ->merge and ->done gates on the two roads a card takes into it
ai_hats:
  # HATS-550 / HATS-686 — hook-carrier skill. Since HATS-1337 the gate is NOT
  # copied anywhere: `ai-hats self init` installs one dispatcher per event in
  # `.githooks/`, and the script below is resolved from this skill directory and
  # run in place at push time. Default (git pre-push): INSTANT pass-marker check
  # keyed to the tree of the pushed master commit. `--run` (run-e2e-gate.sh):
  # runs the ~27-min suite out of band, marker on pass + clean tree.
  # Hard gate: no env-var bypass; `git push --no-verify` is the only escape.
  git_hooks:
    pre-push:
      - git_hooks/pre-push-e2e-master.sh
license: MIT
---

# maintainer-quality-gate

Maintainer-only quality gates for the ai-hats codebase, delivered as
infrastructure (a git hook and a `composition.apps` binding) rather than agent-side
decision logic.

## What it ships

Three gates and the mechanism they share:

- `git_hooks/pre-push-e2e-master.sh` — the dual-mode **pre-push** gate on
  pushes to master (HATS-550 / HATS-686), plus its wrapper
  `scripts/run-e2e-gate.sh`.
- `hooks/merge-gate.sh` — the **`->merge`** gate, bound to `pre-merge` under
  `apps.wt` (a direct `ai-hats wt merge`). It asks: is this branch fit to enter
  master. The agent is alone at that refusal, so only what the agent can fix
  without an arbiter belongs to it (HATS-1614, ADR-0026 D3).
- `hooks/done-gate.sh` — the **`->done`** gate, bound to `edge:review--done`
  under `apps.rack.tasks` (the FSM automerge). It asks: is master green after
  this card — the one question no earlier gate can ask, since two independently
  green branches make a red master. The supervisor is present at this edge.
- `lib/gate.sh` — **the gate primitive** (HATS-1604, ADR-0026 D6): the discipline
  itself, including both whole modes (HATS-1614). It resolves the project's
  dispatcher and asks what this gate is made of, runs that composition stopping
  at the first red, applies "green AND a clean tree → marker", looks the marker
  up, renders the refusal, and maps the outcome onto the calling channel's exit
  codes (git 0/1, checks 0/2/126/127). A gate script keeps two decisions:
  **which tree** it judges and **what runs** on it.
- `lib/gate-marker.sh` — the pass-marker store, written per gate, read across
  them.

Every gate follows the same shape: the expensive run happens **out of band** and
leaves a marker; the gate on the critical path only looks it up, so it is
instant. The marker is keyed to a **tree** and records the **stages** that earned
it, so it cannot go stale in either direction — different content is a different
key, and a gate that grew a stage stops honouring markers that never ran it.

A run also covers any gate whose composition it contains (**absorption**,
ADR-0026 D5): the lookup unions what *every* gate recorded for that tree, so
`make done-gate` also clears `->merge` on the same content and a typical card
costs one run instead of two. That is why `merge-gate` must stay a **subset** of
`done-gate` — `tests/test_gate_entrypoint_parity.py` refuses a composition that
breaks the nesting.

**The composition belongs to the project, never to this skill.** Every gate asks
`scripts/ci-local.sh --stages <gate>` and refuses when the answer is empty: a
library file that restated project content drifted from it within days
(ADR-0026 D7).

## The two card gates (HATS-1137, HATS-1614)

### Who runs what, and when

**The agent**, once the work is finished and committed, in its task worktree:

    cd <task worktree> && make done-gate     # clears BOTH edges
    cd <task worktree> && make merge-gate    # clears the merge only

That is the only step invoked by hand. It runs the named composition (minutes)
and, on green and a clean tree, leaves a marker behind.

Prefer `make done-gate`: it is the superset, so its marker clears the merge too
(absorption). `make merge-gate` is for merging now and closing the card later —
it saves `merge-smoke`, and the card will need the fuller run before `done`.

**The harness**, with nobody involved, when the card is moved:

1. `rack transition <ID> done`.
2. The rack kernel takes the **per-task file lock** — `<tasks_dir>/<ID>/.lock`,
   30s timeout (`ai_hats_rack.kernel.LOCK_TIMEOUT`).
3. Still in the lock, it dispatches `edge:review--done` down the priority
   ladder. The checks subscriber (`ai_hats_rack.checks.CheckSubscriber`,
   subscriber name `checks`) sits at **priority 15**, `Phase.IN_LOCK`.
4. It asks the integrator's port (`ai_hats.rack_consumers.AiHatsCheckPort`) for
   what the **active role** composes — in a session resolved from the surface's
   own skill mirror, outside one from the live library (ADR-0019 D9) — keeps the
   rows whose point is an edge of the topology this kernel runs (ADR-0019 D11),
   and finds the `maintainer` role's rows — two since HATS-1545, one per app,
   binding one gate script per point (`done-gate.sh` here, `merge-gate.sh` on
   `apps.wt` since HATS-1614).
5. `hook_exec.run_hook` spawns the script with **no argv at all**, `stdin`
   `/dev/null`, `cwd` = the project dir, a 20s budget
   (`ai_hats_rack.checks.EDGE_CHECK_TIMEOUT_S`), and `AI_HATS_TASK_ID` in the env. The script's own
   dispatch (`case "${1:---check}"`) therefore lands in `--check`.
6. Exit 0 → the ladder continues. Exit 2 → `AbortOperation` carrying the
   script's stdout tail verbatim, and the kernel persists nothing at all.

Every run's full transcript lands beside the card at

    <tasks_dir>/<ID>/.checks/edge-review--done~rack~tasks~maintainer-quality-gate~hooks+done-gate.sh~<8hex>.log

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

1. `AI_HATS_TASKS_DIR` set and NOT this project's own tracker tasks dir →
   **pass (0)**. Role scope is not backlog scope: a role-scoped binding fires on
   every backlog the rack CLI touches, scratch `--tasks-dir` included, and this
   gate guards what enters THIS repo. A **declared** fail-open — the engine
   states the context and the policy lives here (supervisor ruling 2026-08-08).
   The comparison reads `ai-hats.yaml` and the documented default, never
   `AI_HATS_DIR`: that variable is the leaky one, and whose tracker this is is
   the whole question. Absent at `pre-merge`, which resolves no backlog.
2. `AI_HATS_WORKTREE_PATH` empty → **pass (0)**. The subject of the gate is the
   code entering master through this card; a doc/research card brings none. The
   runner refuses on its own when it could not TELL, so absent means absent here,
   never unknown.
3. The path gone from disk → **pass (0)**. Rack's
   own teardown either finalizes an already-merged branch or refuses the merge
   itself, so there is no live branch content to gate.
4. `git -C <wt> rev-parse HEAD^{tree}` — the **task branch's tree**, never the
   main checkout's: at priority 15 the merge has not happened, so the content
   under judgement is what the branch holds. Unresolvable → **refuse (2)**.
5. The composition, asked of the dispatcher **in that worktree** — not the main
   checkout's. The marker certifies stages that ran there, so asking elsewhere
   judges one tree by another tree's rules. Empty → **refuse (2)**.
6. A marker for that tree covering every demanded stage — **from any gate that
   ran on it** (absorption) → **pass (0)**. Missing → **refuse (2)** with the
   copy-pasteable `cd <wt> && make <this gate>`.

**Two scripts, one body.** Both gates run the same `gate_check_task_worktree`
out of `lib/gate.sh` and differ only in the name they ask the dispatcher about
and the command their refusal prints. They are separate FILES rather than one
script with a flag because the checks channel has no argv: `run:` is
`<skill>/<script>` and the runner spawns it bare. The tree under judgement
arrives as `AI_HATS_WORKTREE_PATH` at *both* points, resolved once by the runner
(HATS-1540 R2) instead of each gate re-deriving
`sessions/worktrees/task-<id>.json` and parsing the JSON by hand. The primitive
*removes* an unresolved value from the inherited environment rather than letting
an ambient one through — that applies to `AI_HATS_TASKS_DIR` too, so a stale one
cannot reach the script at `pre-merge` and read as "not my backlog".

### `--run` — `make done-gate` / `make merge-gate`, and where the marker lands

Asks the dispatcher for that gate's composition and runs those stages, stopping
at the first red, from `git rev-parse --show-toplevel`. On green **and a clean
tree** it writes

    <git-common-dir>/ai-hats/<gate>/<HEAD tree>

carrying `tree=`, `timestamp=` and `stages=` — the composition it actually ran.
Written per gate, read across gates: the directory records which gate earned it,
while a lookup unions every gate's marker for that tree (absorption). Two more
things about that path:

- **`--git-common-dir`, never `--git-dir`.** The common dir is the main
  checkout's `.git`, shared by every linked worktree — which is exactly what
  makes a marker **written inside the task worktree** visible **from the main
  checkout**, where the check runs. `--git-dir` would file it under
  `.git/worktrees/<id>/` and the check would never see it.
- It lives under `.git/`, so it is never committed. Markers are tiny; no GC.

A marker counts only when its filename and its recorded `tree=` line agree
(`gate_marker_ok`) — a half-written or hand-copied file names content it does
not certify.

A **dirty tree** runs the suite and writes nothing (exit 0, loudly): the gate
ran against the working tree, so what passed is not what the branch tip holds.
Commit, then re-run. Run it **in the task worktree** — the marker is keyed to
that branch's tree, which is the content the check looks up.

The composition lives in `scripts/ci-local.sh`, not here: changing what "green
enough to be done" means is a project-side edit, and the gate script never
moves.

### Why the marker cannot go stale

Its key is the **content** it certifies, and its body is the **composition** it
ran. Different content is a different key; a gate that grew a stage no longer
matches markers that never ran it. There is no expiry and no invalidation step,
and none is needed — while one run covers every card sitting on that same tree,
including the `--no-ff` merge commit that re-parents it unchanged (HATS-1601:
18 of the last 20 merges), and every gate whose composition it contains.

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
an accident from reading as a verdict. That is also why both card gates run
under `set -uo pipefail` with **no** `-e`, and spell out every exit.

### Why two steps and not one

Either composition takes minutes. The rack task lock times out at 30s and
the per-check budget is 20s (`ai_hats_rack.checks.EDGE_CHECK_TIMEOUT_S`, `< LOCK_TIMEOUT`
at import time), so the heavy work cannot run in the lock at all: it would be
killed at 20s, and a peer waiting on the lock would mis-blame a concurrent
operation. Splitting it leaves the critical path as one file read — instant.

### Why a separate script from the pre-push gate

The pre-push gate's default mode reads git's pre-push protocol from **stdin**,
and the check runner gives its children `stdin=DEVNULL`. Empty stdin hits that
script's "no master target" fast path and it exits 0 — reusing it would have
produced a gate that is silently always green.

## Binding several scripts to one point

`composition.apps` takes **one script per row**, so two scripts on one point is
two rows. Nothing else changes — same point, same edge, same lock:

```yaml
composition:
  skills:
    - maintainer-quality-gate # a binding never pulls its skill in (ADR-0019 D2)
  apps:
    rack: # the application; below it, rack's own grammar
      tasks: # the backlog this row gates (name or cli_alias)
        - run: maintainer-quality-gate/hooks/done-gate.sh
          at: [edge:review--done]
          on_error: refuse
        - run: maintainer-quality-gate/hooks/changelog-entry.sh
          at: [edge:review--done, edge:execute--review]
          on_error: warn
    wt: # ai-hats's own app: rows sit directly under the key
      - run: maintainer-quality-gate/hooks/merge-gate.sh
        at: [pre-merge]
        on_error: refuse
```

`run:` is `<skill>/<path-inside-it>`, replacing the `skill:` / `script:` pair.
ai-hats owns three keys of a row — `run:`, `at:`, `on_error:` — and carries
everything else to whoever owns `<app>`; the depth between the app key and the
row is that app's grammar too (HATS-1545, ADR-0017 §3).

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
  `.checks/<event>~<app>[~<level>…]~<skill>~<script>~<digest>.log`, with `/`
  escaped to `+`. Before HATS-1137 the name carried the edge only, so binding #2
  truncated #1's file; the trailing digest (HATS-1545) covers `at:` and the rest
  of the row's identity, so two rows differing only in the points they bind
  cannot share a file either. Glob for the stem — do not hand-build the name.
- `at:` is a list, so **one row may name several points**, and **several rows
  may name the same point**. Only an exact `(app, path, run, at + cargo)`
  identity collapses: `check_points.resolve_checks` dedups on it, keeps the
  **first** declaration's slot, warns naming both declarers, and hardens
  `on_error` to the strictest of the two — a later `warn` can never relax a gate
  an earlier row declared `refuse`.
- Binding to a skill the role does not compose is a loud composition error, not
  an implicit compose. `on_error: warn` is rejected outright at a data-protection
  point (`apps.wt` at `pre-merge`), whose failure policy
  `check_points.wt_points()` fixes (ADR-0019 D4).
- The field is `at:`, not `on:`. YAML 1.1 resolves a bare `on` key to the boolean
  `True`, and under an opaque cargo block no parser can remap it back — ai-hats
  does not know the key is significant. HATS-1545 removed the trap by choosing a
  word outside YAML 1.1's truthy set and deleted the old remap; a config still
  carrying the retired `checks:` key gets a **typed refusal** naming where the
  rows moved.

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

The fix decouples the run from the push via a pass-marker keyed to the pushed
content, preserving the HATS-550 "no-broken-master, no-bypass" contract.

## How the pre-push gate works

### Run mode — `scripts/run-e2e-gate.sh` (or `… --run`)

Run this **before** pushing master. From the repo root the hook requires
`pytest` on PATH (absent → ABORT, no marker) and a dispatcher that names a
`push-gate` composition (absent → ABORT, no marker). The cheap stages come
first in that composition and a red one aborts before the ~25-min tier starts
(HATS-726 — the marker has to mean "everything CI checks is green"). Then it
sweeps the dev checkout's `build/` directory (HATS-568 —
stale wheel-build artefacts cause "File exists: build/bdist...dist-info"
collisions across worktree-tier e2e tests), then reaps stale tmp cruft
(`ai-hats-wt-*`, pytest run dirs) via `scripts/clean-tmp-cruft.sh`
(HATS-731/HATS-570 — keeps APFS metadata ops fast on a loaded host). The sweep
**deletes by default**, but only on proof of death (HATS-1624): a worktree git
no longer tracks, a run dir whose `.lock` names an exited pid. It was a dry-run
preview for as long as the sweeper judged by name alone — and freed nothing
while 145 GB accumulated. `AI_HATS_E2E_CLEAN_TMP=1` escalates to `--force`,
which also takes the unlocked run dirs pytest keeps for triage.
Then it runs the tier as the project's own `e2e`
stage — one selection, not a second copy of it (HATS-1604) — carrying the gate's
own flags in `PYTEST_ADDOPTS`: `--tb=line --no-header -p no:cacheprovider`, plus
`-n min(cpus,8) --dist=loadgroup` when pytest-xdist is present (HATS-589/592).
`AI_HATS_E2E_REQUIRE_VENV=1` is armed so the tier-2 venv fixture fails closed
(HATS-645); quarantined known-flaky tests are deselected by the selection itself
(HATS-676).

On **pass** AND a **clean working tree**, it writes a marker keyed to
`git rev-parse HEAD^{tree}`:

| condition                             |         marker         | exit |
| ------------------------------------- | :--------------------: | :--: |
| every stage green, clean tree         |       ✅ written       |  0   |
| tier rc 5 (nothing collected), clean  | ✅ written (defensive) |  0   |
| green but **dirty** tree              |        ❌ none         |  0   |
| tree unresolvable / marker unwritable |        ❌ none         |  0   |
| a stage failed (other rc)             |        ❌ none         |  1   |
| pytest absent / no composition named  |     ❌ none, ABORT     |  1   |

The clean-tree invariant matters: the gate builds wheels from the *working
tree*, so a marker is only honest when the working tree is the tree you push.
A dirty tree runs the suite but writes no marker (commit first, re-run).

### Check mode — the pre-push hook (default)

When you `git push`, git invokes the hook with the standard protocol on stdin:

    <local_ref> <local_sha> <remote_ref> <remote_sha>

For every line targeting `refs/heads/master` with a non-zero `local_sha`
(i.e. not a deletion), the hook resolves that commit's **tree** and requires a
marker for it under `<git-common-dir>/ai-hats/e2e-gate/` covering every stage
the composition names. All present → allow (exit 0, **instant** — no pytest, no
network). Any missing → block (exit 1) with the run command. Pushes to other
branches, master deletions, and empty stdin are fast-path no-ops.

Markers live under `.git/` (never committed, shared across worktrees via
`git rev-parse --git-common-dir`). They are tiny; no GC is performed.

## Typical flow

    scripts/run-e2e-gate.sh        # ~27 min, out of band; writes the marker on green
    git push origin master         # pre-push check passes instantly

## How to bypass

You **can't** in the normal flow via env-var: there is no `AI_HATS_E2E_SKIP=1`
and no `--ack` override (HATS-550 intent).

Forging a marker (`touch <git-common-dir>/ai-hats/e2e-gate/<tree>` with a
matching `tree=` line) *is* a bypass, but it is the moral equivalent of
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
- Gate compositions: `scripts/ci-local.sh` → `gate_composition`, one line per
  gate; `--stages <gate>` prints one
- Binding shape and outcome policy: ADR-0019 D2 / D4; exit contract: ADR-0020 D2
- Check runner and log path: `src/ai_hats/rack_consumers.py`; in-lock ladder:
  `src/ai_hats/rack_wiring.py` → `build_rack_kernel`
- Git-hook install path (dispatchers only, HATS-1337):
  `src/ai_hats/hooks_manager.py` → `install_git_hooks`; gate resolution at
  spawn: `src/ai_hats/githooks_resolve.py`
- Sibling pattern:
  `packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh`
