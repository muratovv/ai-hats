# ADR-0013: Worktree core extraction — hook-agnostic engine boundary (Option B)

## Status

Accepted (HATS-841, 2026-06-26; implemented 2026-07-01). Realized by child impl
tasks P1–P4 — HATS-849 (hook lift + extension-point contract), HATS-850
(path-base injection + D6 import-lint), HATS-851 (`wt/` sub-package + D9 exports),
HATS-852 (re-point verification + D9 standalone smoke test) — all merged to
master. Later extracted into the standalone `ai-hats-wt` workspace package
(`packages/ai-hats-wt/`, module `ai_hats_wt`) with a package-boundary import-lint
by the release slice HATS-880/882 (2026-07-02). Supersedes nothing; complements
[ADR-0006](0006-worktree-concurrency-layered-defense.md) (the layered lock model
that stays in the extracted core) and [ADR-0012](0012-worktree-data-transfer.md)
(the lifecycle-hook layer that this ADR keeps *outside* the core).

Grounded in the read-only investigation `wt-extraction-report.md` (attached to
HATS-841), which maps every coupling claim to `file:line`.

## Context

`ai-hats wt` is the framework's git-worktree isolation subsystem: each task or
sub-agent gets its own branch + filesystem working copy, surfacing through
`ai-hats wt create / merge / discard` plus the auto-create path in
`ai-hats task transition <id> execute`. It has grown to ~4,500 LOC across five
files, dense with `HATS-###` provenance comments, and many backlog tasks orbit
it. The supervisor's goal: a **simple, self-contained git-worktree engine**
decoupled from ai-hats complexity, with ai-hats's own accretions (composition,
tracker, lifecycle hooks, FSM) layered **on top** — so future work reasons about
a small, stable engine surface rather than a monolith.

The investigation found the subsystem is **far more decoupled than its LOC
suggests**. The engine (`WorktreeManager`, locks, guards) has almost no inbound
dependency on ai-hats internals:

- `worktree.py` module-level first-party imports are only `from .models import
  WT_TEARDOWN_EVENTS` (a plain 3-tuple, `models.py:203`), `from .paths import …`
  (where state/lock/hook files live, `worktree.py:22`), `from .worktree_hooks
  import run_worktree_hook` (the hook-run primitive, `worktree.py:23`), and
  `from .worktree_locks import …` (`worktree.py:25`). There is **no** import of
  `Assembler` / `composer` / `state` (verified: the only "assembler" tokens in
  `worktree.py` are a docstring and the `_materialized_hook` method name).
- All heavy coupling (composition, `Assembler`, `ai-hats.yaml`, the tracker) is
  funnelled through one function, `worktree_hooks.collect_carry_for_role`
  (`worktree_hooks.py:67`), which is **not** called by the engine — only by
  `state.py:1030` and `cli/worktree.py`. Hook data crosses into the engine as a
  plain JSON-serializable `dict` via `create(wt_hooks=…)` (`worktree.py:467`).
- The locks (`worktree_locks.py`) are already extracted (HATS-715) and near-pure.

So the boundary is almost entirely present; what remains is to (1) stop the
engine reaching back for ai-hats path conventions and the `models` constant, and
(2) stop the engine *running* hooks itself, while still firing the lifecycle
points hooks need.

**The supervisor refinement (the one new design subtlety).** The investigation
author placed the hook-run primitive (`run_worktree_hook`) in the extracted
core. The supervisor overrode this: the **entire** lifecycle-hook layer — run
*and* collect, the whole ADR-0012 worktree-data-transfer mechanism — **stays in
ai-hats**. The core is **hook-agnostic** and instead exposes **lifecycle
extension-points** that ai-hats plugs hooks into. The core defines *where* the
points are (including every teardown route); ai-hats defines *what* runs there
and the fail-closed policy. Reconciling this — without dropping a teardown route
or weakening the fail-closed contract — is the core design work below.

**Why Option B (decouple in-place), not Option A (separate repo).** The coupling
is one collection function + a path convention; there is no external consumer;
ADR-0012 builds *more* ai-hats↔wt integration, not less; the bidirectional
lifecycle (FSM auto-create/auto-merge, e2e that drives wt *through*
`ai-hats task transition`) means two repos would be co-released in practice —
paying distributed-system costs for monolithic coupling. B captures ~90% of the
benefit at a fraction of A's cost, and is the correct first phase of A
(a near-mechanical `git filter-repo` of an already-isolated `wt/`) if an external
consumer ever appears — so it is never wasted work.

## Decision

Establish a **module boundary** (not a repo boundary): an in-tree
`src/ai_hats/wt/` sub-package containing the hook-agnostic engine, with a
one-directional import rule. ai-hats accretions stay where they are and import
*from* `wt/`, never the reverse.

### D1 — The boundary: what is core vs accretion

**Core (`wt/`, hook-agnostic, tracker-agnostic):**

- `WorktreeManager` — `create` / `merge` / `discard` / `cleanup`, `load_for_task`
  / `load_for_branch` / `list_active`, static git probes
  (`is_inside_linked_worktree`, `main_worktree_root`, `worktree_toplevel`,
  `list_worktrees`, `branch_exists`), `assert_head_is_canonical_base`.
- All git mechanics + guards: drift (HATS-457), base-branch (HATS-518/533),
  mid-merge (HATS-587/602), dirty-check, branch-delete classification, the
  HATS-596 already-merged short-circuit.
- The full `worktree_locks.py` concurrency model (L1–L4, ADR-0006).
- State persistence as JSON, with the hook carry stored as an **opaque
  pass-through** (see D5).
- `IsolationMode` enum + the typed exceptions (already the clean seam). The count
  stays 11 across the lift: `WorktreeHookError` relocates to ai-hats (hook
  vocabulary, D8) and the hook-agnostic `WorktreeTeardownAborted` takes its place
  in core.
- **Lifecycle extension-points** (D2/D3) — the named callback sites, but **no
  hook-run policy**.

**Stays in ai-hats (accretion), importing from `wt/`:**

- The **whole lifecycle-hook layer** (ADR-0012): collection
  (`collect_carry_for_role` + `serialize_collected_hooks` +
  `composer.collect_worktree_hooks` / `assembler` materialization) **and** the
  hook-run primitive (`run_worktree_hook`) + the D7 contract + the fail-closed /
  warn-continue policy.
- FSM auto-create/auto-merge (`state._setup_worktree` / `_teardown_worktree`),
  `task transition` error-recipe translation, the `IsolationMode` *policy choice*
  (`--isolation`), the tracker redirect (`_project_dir` + HATS-524
  `main_worktree_root` consumption), and the `<ai_hats_dir>/sessions/worktrees/`
  path convention (injected, D4).

### D2 — Extension-point mechanism: a callback bundle, not an event-bus

The core exposes a small **callback bundle** with exactly two methods, default
**no-op** (so a bare core runs no hooks — hook-agnostic by default):

```python
class WorktreeLifecycle(Protocol):
    def on_created(self, ctx: LifecycleContext) -> None:
        """Never raises (warn-continue, D8); a create-time failure is friction."""
    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        """Raises core-owned WorktreeTeardownAborted to abort the route (D3/D8)."""
```

- `on_created` fires once after `git worktree add` succeeds (the current
  `_run_wt_in_hooks` site, `worktree.py:633`).
- `before_teardown(event, …)` fires at **every** teardown route, immediately
  before `_remove_worktree()`, parameterized by the route's event name
  (`"merge"` / `"discard"` / `"cleanup"`) — exactly where `_run_wt_out_hooks`
  is called today.
- `ctx` carries what the hook run needs and **nothing hook-policy**:
  `worktree_path`, `project_dir`, `branch_name`, the persisted carry dict, the
  `skip_hooks` flag, and `legacy: bool` (state predates wt-hooks — so the ai-hats
  callback can reproduce the warn-not-drop; `legacy` is needed because an absent
  carry key and an empty `{}` are indistinguishable from the dict alone, see D8).

**Why a callback bundle, not an event-bus / observer (rejected).** The
fail-closed contract (D3) requires synchronous, ordered, exception-**propagating**
invocation at each site: a hook failure must raise `WorktreeTeardownAborted`
(D8) *through* the core, which then aborts teardown. Observer/event-bus patterns
decouple emit from handling and typically aggregate or swallow subscriber
exceptions — the opposite of what fail-closed needs. A direct callback invoked
inline where `_run_wt_out_hooks` sits today preserves the existing ordering and
exception semantics with a near-mechanical edit.

**Why two methods, not N (design-minimalism).** Only two lifecycle directions
exist (create, teardown); one event-parameterized `before_teardown` covers all
teardown routes. A generic N-kind extension registry is out of scope until a
third independent extension kind with a real consumer appears.

### D3 — Injection point + the fail-closed ordering invariant

**Injection at construction, re-attached on load.** The callback bundle is
injected via `WorktreeManager.__init__` (default = the no-op bundle) and
re-attached by the `load_for_task` / `load_for_branch` classmethods. This is
required because:

- `cleanup()` is invoked from `__exit__` (`worktree.py:954`) — there is no
  per-call parameter channel for the context-manager (sub-agent) path.
- Teardown reconstructs a **fresh** manager from persisted state in a separate
  process/CLI call (`state._teardown_worktree` → `load_for_task`,
  `state.py:1085`; `wt merge` / `wt discard` CLI) — so the bundle cannot only be
  passed at `create`.

ai-hats always constructs / loads with its hook-running bundle; a bare core
defaults to no-op.

**The invariant the lift must preserve.** Today each teardown route runs
`self._run_wt_out_hooks(event)` *before* `_remove_worktree()`; a `wt_out` hook
failure raises, which aborts teardown and preserves the worktree
(`worktree.py:998–1047`). After the lift, `before_teardown` is invoked at the
same point; **a raised `WorktreeTeardownAborted` aborts teardown with the same
effect** — the core never reaches `_remove_worktree()`. The core then applies
**per-route abort handling** (this is teardown *control-flow*, owned by core, not
hook policy — see D8): `merge` / `discard` **propagate** the abort (the FSM /
CLI surfaces it, HATS-481 fail-loud); `cleanup` **suppresses** it (warn +
preserve + return, so a sub-agent's original error is not masked). ai-hats owns
only *whether* `before_teardown` raises (its fail-closed decision); the core owns
*what a raise means at each route*.

**Exhaustive lifecycle-site inventory (the ADR-0012 D4 trap — miss one and a
teardown route silently bypasses hooks):**

| Site                                          | `worktree.py`               | Event     | Direction                                |
| --------------------------------------------- | --------------------------- | --------- | ---------------------------------------- |
| after `git worktree add`                      | `:633` (`_run_wt_in_hooks`) | —         | `on_created`                             |
| merge — HATS-596 already-merged short-circuit | `:724`                      | `merge`   | `before_teardown`                        |
| merge — OriginalBranchMissing path            | `:802`                      | `merge`   | `before_teardown`                        |
| merge — success path                          | `:841`                      | `merge`   | `before_teardown`                        |
| discard                                       | `:893`                      | `discard` | `before_teardown`                        |
| cleanup                                       | `:932`                      | `cleanup` | `before_teardown`                        |
| `__exit__` → `cleanup()`                      | `:954`                      | `cleanup` | `before_teardown` (via the cleanup site) |

All three `merge` sites and the single `discard` / `cleanup` sites must invoke
the callback. `__exit__` reaches the callback through `cleanup()`, so it needs no
separate site — but the inventory lists it so a reviewer confirms the
context-manager path is covered.

### D4 — Path-base injection

Add `state_dir` and `hooks_dir` parameters to `WorktreeManager.__init__`,
threaded into `worktree_locks` (`worktree_locks.py:201`) and the hooks-log /
materialized-hook path helpers (`worktree.py:962`, `:966`). ai-hats always passes
its convention (`<ai_hats_dir>/sessions/worktrees/` and
`<ai_hats_dir>/library/wt-hooks/`); the core's **fallback is project-local** so
`wt/` never imports `ai_hats.paths`. This is what lets the import-lint (D6)
forbid `from ai_hats.paths import …` inside `wt/`.

> **Implementation note (P2 / HATS-850): `hooks_dir` dropped — `state_dir` only.**
> P1 (HATS-849) lifted the entire hook layer into ai-hats, including the
> hooks-log / materialized-hook path helpers (now `wt_lifecycle.py`, which
> resolves them from `ctx.project_dir`). By P2 the core had **zero** `hooks_dir`
> consumers — `worktrees_dir` was the *only* `ai_hats.paths` symbol it imported —
> so injecting `hooks_dir` would have been a dead parameter (design-minimalism).
> P2 injects **only `state_dir`**; the goal ("core imports no `ai_hats.paths`")
> holds. The bare-core fallback is `project_dir/.wt`, plus a `__debug__` assert
> (`lifecycle is NOOP_LIFECYCLE or state_dir is not None`) that catches an
> ai-hats driver omitting the base before it de-serializes the locks.

**The base must thread through the `load_*` classmethods too, not just
`__init__`.** `load_for_task` / `load_for_branch` / `list_active` resolve their
own directory via `worktrees_dir(project_dir)` (`worktree.py:1191`, `:1247`); if
only `__init__` is injected, teardown — which reconstructs a *fresh* manager from
`load_for_task` (`state.py:1085`) — would resolve a *different* directory than
`create` wrote to, and the state/lock files written at create would not be found
→ silent breakage. So `state_dir` / `hooks_dir` are parameters on the `load_*` /
`list_active` signatures as well, and every call site threads them: `state.py`
(`:1016`, `:1037`, `:1085`), `cli/task.py`, `cli/worktree.py`,
`subagent_runner.py:246`, and the internal L2 re-check (`worktree.py:520`).

**Lock-path-identity invariant (ADR-0006 must not regress).** The L1 create-mutex
and base-branch lock derive their paths from the same base
(`worktree_locks.py:337`, `:381`); these are **cross-process** mutexes —
serialization holds *only* if every process computes the **same** lock path for a
given worktree. Making the base config-dependent (this D4) means a call site that
passes a different base — or silently falls back to the project-local default
while a peer passes the ai-hats convention — gets a *different* lock file and the
mutex stops serializing (the HATS-479 / HATS-480 / HATS-715 class). The injected
base must therefore thread into the lock-path derivation, and the contract is:
**for one worktree, all processes resolve one base.** In practice ai-hats always
passes its convention; the project-local fallback is for the bare-core /
standalone path (D9), which has a single process. Because the fallback fails
*silent* (a wrong-but-valid path, which the import-lint cannot catch — it is a
runtime arg, not an import), P2 adds a cheap backstop: ai-hats debug-asserts that
its resolved base equals `worktrees_dir(project_dir)` at the construction
chokepoint, so an accidentally omitted-base call site is caught loud rather than
silently de-serializing the lock.

`WT_TEARDOWN_EVENTS` is **not** localized into the core. Its only core consumer
is the `on`-filtering inside `_run_wt_out_hooks` (`worktree.py:1017`), which P1
lifts into ai-hats; after P1 the core no longer references it, so there is no
import to "remove." It stays in ai-hats as **hook vocabulary** (the `models.py`
pydantic validator still needs it, `models.py:477–485`); the core fires plain
string literals (`"merge"` / `"discard"` / `"cleanup"`) at each route. Localizing
a copy would create two definitions of the teardown vocabulary that must agree —
a drift hazard (a hook validated `on:["cleanup"]` against the models copy must
match the event the core fires).

### D5 — Opaque persisted carry (no rename, no migration)

The core persists the hook carry dict verbatim in state JSON (`save_state`,
`worktree.py:1064`) and **never interprets it** — it is an opaque blob the core
stores at `create` and hands back to the callback at teardown. The persisted key
stays `wt_hooks` (no rename → no state migration for in-flight worktrees); the
core treats it as an opaque slot, the ai-hats callback is the only thing that
reads its shape. (Naming it generically, e.g. `carry`, was considered and dropped
— it forces a migration for zero behavior gain; see
[Alternatives](#alternatives-considered).)

### D6 — One-directional import rule, enforced in the existing gate

Forbid any `from ai_hats.{assembler,composer,materialize,models,state,paths,
worktree_hooks-collection} import …` inside `src/ai_hats/wt/`. Enforce it by
**extending the existing stdlib gate** `tests/test_import_hygiene.py` (HATS-758)
— which already encodes leaf-purity and cycle rules with a dependency-free AST
walk. Do **not** add `import-linter`: that gate already evaluated and rejected it
(grimp counts deferred + `TYPE_CHECKING` edges and cannot express the project's
"module-level runtime only" idiom). The rule must be **RED under revert**:
re-introducing a `wt/ → ai_hats.paths` import fails the gate.

### D7 — wt CLI stays a thin ai-hats wrapper

`cli/worktree.py` stays in ai-hats; the core exposes only the `WorktreeManager`
API. The CLI imports `_project_dir` (the HATS-524 tracker redirect),
`_guard_not_inside_linked_worktree`, and `console` from `cli/_helpers.py`
(`cli/worktree.py:13`), and owns the drift / mismatch error-recipe translation
(HATS-509 facts-only exception bodies, recipe owned by the CLI). Keeping the CLI
in ai-hats keeps the core **tracker-agnostic** as well as hook-agnostic. (The
investigation §4 placed the CLI in core; this ADR overrides that placement under
the "core is minimal" directive — see [Alternatives](#alternatives-considered).)

### D8 — Abort contract + policy ownership (hook-policy-free core)

The core distinguishes two callback outcomes — **returned** (proceed) vs
**raised `WorktreeTeardownAborted`** (abort) — and applies per-route control-flow
(D3). The abort signal is a **generic, core-owned, hook-agnostic** exception:

```python
# core (wt/), hook-agnostic:
class WorktreeTeardownAborted(Exception): ...   # "a before_teardown extension-point vetoed teardown"

# ai-hats callback, on a fail-closed hook failure:
raise WorktreeTeardownAborted(...) from WorktreeHookError(...)   # hook detail is the __cause__
```

**Why a generic core exception (A′), not reusing `WorktreeHookError` in core
(A).** `WorktreeHookError` (`worktree.py:69`, *"wt_out hook from skill X
failed"*) is **hook vocabulary** — it belongs with the hook layer that leaves
core. Keeping it in core to catch would re-leak the hook concept into the
hook-agnostic engine. Instead the core owns `WorktreeTeardownAborted` (it knows
only *"a teardown extension-point vetoed this route"*, nothing about hooks);
`WorktreeHookError` **relocates to ai-hats** and rides as the `__cause__`. Net
core exception count is unchanged (11): `WorktreeHookError` out,
`WorktreeTeardownAborted` in.

**Relocation blast radius (the only real catchers — grepped).** Just three files
reference `WorktreeHookError`: `worktree.py` (the definition, relocates),
`cli/worktree.py`, and `tests/test_worktree_wt_hooks.py`. The CLI catch type
**changes**, not just the field read: `cli/worktree.py:210` (merge) / `:365`
(discard) become `except WorktreeTeardownAborted as e:` and surface
`str(e.__cause__)` for the recipe (preserving today's message). `cli/task.py` does
**not** catch `WorktreeHookError` (specific types + a defensive `except Exception`
at `:622`) and `state._teardown_worktree` catches `except Exception`
(`state.py:1125`) — both treat old and new type identically, so **no change** is
needed there (the earlier "covers cli/task.py" wording was imprecise).
`test_worktree_wt_hooks.py` is the real test-migration — see the test strategy
below.

**Policy ownership.** The ai-hats callback owns *what runs* **and the
fail-vs-warn decision**:

- `on_created` (`wt_in`) — **warn-continue**: the callback logs and returns,
  **never raises** (create-time failure is friction, not data loss;
  `worktree.py:991`).
- `before_teardown` (`wt_out`) — **fail-closed**: on hook failure the callback
  **raises `WorktreeTeardownAborted`** (`worktree.py:1042` is today's raise site).
- `skip_hooks` / `--skip-hooks` (conscious data-loss escape, `worktree.py:1021`)
  and the legacy warn-not-drop (gated on `ctx.legacy`, `worktree.py:1005`) — both
  live in the ai-hats callback; on `skip_hooks` or legacy it returns (proceed).

The core owns **teardown control-flow**, which is *not* hook policy:

- *where* each extension-point fires (D3 inventory);
- *what a raised abort means per route* — `merge` / `discard` re-raise (propagate
  the abort so the FSM/CLI surface it, HATS-481); `cleanup` catches
  `WorktreeTeardownAborted`, warns, preserves the dir, and **returns without
  propagating** (`worktree.py:931–939` keeps its `try/except … return` shape,
  swapping the inner `_run_wt_out_hooks` call for `before_teardown`). This is the
  one route whose abort is non-fatal — intrinsic to the context-manager teardown
  (it must not mask a sub-agent's original exception), so it is core control-flow,
  not a thing ai-hats can express by "not raising" (not raising would let the core
  proceed to `_remove_worktree()` and **destroy** the unharvested worktree).
- **Cleanup recovery-message provenance.** Today the core's cleanup catch logs an
  actionable, *hook-specific* line (*"wt_out hook failed … recover with `ai-hats
  wt discard <branch> --skip-hooks`"*, `worktree.py:934–938`). The cleanup route
  **suppresses** the abort, so it never reaches the CLI — D8's "CLI reads
  `__cause__`" plan does **not** cover it. To avoid this sub-agent-path message
  regressing to generic, the **ai-hats callback authors the full recovery text
  into the `WorktreeTeardownAborted` message**, and the hook-agnostic core's
  cleanup catch logs `str(exc)` (it must not itself name "wt_out" / "--skip-hooks").

So the core is **hook-policy-free** — it never knows what runs, why a hook
matters, or warn-vs-fail — while still owning the teardown state machine. This is
the precise sense in which the supervisor refinement holds: the *hook* layer is
fully an ai-hats accretion; the *control-flow* of teardown is, and always was,
the core's.

### D9 — Public surface + standalone smoke (cheap consumability now)

Option B does not package wt for external distribution (deferred to Option A,
[Alternatives](#alternatives-considered)), but it should make the engine a
*verified consumable surface*, not merely an isolated one — at near-zero cost,
since the unit tier already runs on a bare `git init` (report §5):

- `src/ai_hats/wt/__init__.py` **explicitly exports** the public surface:
  `WorktreeManager`, `IsolationMode`, the typed exceptions (incl.
  `WorktreeTeardownAborted`), and the `WorktreeLifecycle` protocol + the no-op
  default bundle.
- A **standalone smoke test** drives `WorktreeManager(repo).create()` then
  `.merge()` / `.discard()` on a bare `git init`, with the **no-op** lifecycle
  bundle and the **project-local** path fallback — no `ai-hats.yaml`, no
  composition, no tracker. This proves a third party (or a power user via
  `from ai_hats.wt import WorktreeManager`) can construct and drive the engine
  standalone, and pins the "self-contained engine" claim to a test rather than an
  assertion.

This is the standalone user value Option B can deliver *today*; full external
packaging stays Option A's job, triggered by a real external consumer.

## Lifecycle scenario matrix (use cases → tests)

The contract above is only correct if **every (route × callback-outcome) state is
enumerated** — the same exhaustiveness the ADR-0012 D4 trap demands, now made
explicit so each state becomes a use case and each use case a test. The
**dimensions** are the lifecycle route (D3 inventory) and the callback outcome
(D8): *no-op / hooks-ok / hook-fail / skip_hooks / legacy*, plus the
cross-cutting core-only states (peer-torn-down, `NONE` mode). "Result" is the
observable: worktree dir removed?, branch?, exception propagated? "Owner" marks
whether the behavior is core control-flow or ai-hats callback policy.

**Create — `on_created` (`wt_in`, warn-continue; never aborts create):**

| #  | Precondition            | Callback outcome                           | Result                                           | Test                               |
| -- | ----------------------- | ------------------------------------------ | ------------------------------------------------ | ---------------------------------- |
| S1 | no hooks / no-op bundle | returns                                    | worktree created                                 | `test_worktree.py` (create)        |
| S2 | `wt_in` hooks succeed   | returns                                    | worktree seeded + created                        | `e2e/test_wt_in_runs.py`           |
| S3 | `wt_in` hook **fails**  | callback logs, **returns** (warn-continue) | worktree still created (friction, not data loss) | new unit: on_created-fail proceeds |
| S4 | `IsolationMode.NONE`    | extension-point **never fires** (D7)       | runs in project_dir, no worktree                 | `test_worktree.py` (NONE)          |

**Merge — success route (`worktree.py:841`):**

| #  | Precondition            | Callback outcome                 | Core control-flow | Result                                                                                               | Test                                   |
| -- | ----------------------- | -------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------- |
| S5 | no hooks                | returns                          | proceed           | remove + delete branch + clear state                                                                 | `test_worktree.py` (merge)             |
| S6 | `wt_out` hooks succeed  | returns                          | proceed           | harvest → remove + delete + clear                                                                    | `test_worktree_wt_hooks.py`            |
| S7 | `wt_out` hook **fails** | raises `WorktreeTeardownAborted` | **propagate**     | abort before remove; wt + branch + state **preserved** (HATS-587 F5 retry); FSM re-raises (HATS-481) | `e2e/test_wt_hooks_fail_closed.py`     |
| S8 | `skip_hooks=True`       | no-op + loud warn, returns       | proceed           | remove (data-loss accepted)                                                                          | new/`test_worktree_wt_hooks.py` (skip) |

**Merge — HATS-596 already-merged short-circuit (`worktree.py:724`):**

| #   | Precondition                   | Callback outcome       | Result                                                                                     | Test                                                |
| --- | ------------------------------ | ---------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| S9  | already-merged, hooks succeed  | returns                | harvest → remove + delete + clear (no `git merge`)                                         | `e2e/test_wt_merge_already_merged_head_wandered.py` |
| S10 | already-merged, hook **fails** | raises → **propagate** | abort; branch preserved → retry re-hits 596 and re-runs hook (`worktree.py:840` invariant) | new: 596 × hook-fail re-run                         |

**Merge — OriginalBranchMissing route (`worktree.py:802`):**

| #   | Precondition                        | Callback outcome                                 | Result                                                                                                                    | Test                                        |
| --- | ----------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| S11 | base branch deleted, hooks succeed  | returns                                          | harvest → remove → clear → raise `OriginalBranchMissingError` (wt branch preserved)                                       | `e2e/test_wt_merge_null_original_branch.py` |
| S12 | base branch deleted, hook **fails** | raises `WorktreeTeardownAborted` → **propagate** | abort **before** remove; `OriginalBranchMissingError` **not reached** (hook-fail takes precedence; wt + branch preserved) | new: OBM × hook-fail precedence             |

**Discard (`worktree.py:893`):**

| #   | Precondition            | Callback outcome       | Core control-flow | Result                                                     | Test                                         |
| --- | ----------------------- | ---------------------- | ----------------- | ---------------------------------------------------------- | -------------------------------------------- |
| S13 | no hooks                | returns                | proceed           | remove + delete branch + clear                             | `test_worktree.py` (discard)                 |
| S14 | `wt_out` hooks succeed  | returns                | proceed           | harvest → remove + delete                                  | `test_worktree_wt_hooks.py`                  |
| S15 | `wt_out` hook **fails** | raises → **propagate** | abort             | wt + branch **preserved** (discard ≠ accept data-loss, D4) | `e2e/test_wt_hooks_fail_closed.py` (discard) |
| S16 | `skip_hooks=True`       | no-op + warn           | proceed           | remove                                                     | new (discard skip)                           |

**Cleanup — incl. `__exit__` context-manager (`worktree.py:932`):**

| #   | Precondition                                                    | Callback outcome                 | Core control-flow            | Result                                                                  | Test                                                        |
| --- | --------------------------------------------------------------- | -------------------------------- | ---------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| S17 | sub-agent exit, no hooks                                        | returns                          | proceed                      | (squash per mode) remove; delete branch unless `BRANCH`                 | sub-agent isolation test                                    |
| S18 | hooks succeed                                                   | returns                          | proceed                      | harvest → remove                                                        | `test_worktree_wt_hooks.py` (cleanup)                       |
| S19 | hook **fails**                                                  | raises `WorktreeTeardownAborted` | **suppress** (warn + return) | dir **preserved**, **no exception propagated** — the blocker case       | **new (critical):** cleanup × hook-fail preserves, no raise |
| S20 | `__exit__` with an in-flight agent exception **and** hook fails | raises                           | **suppress**                 | dir preserved; the **agent's original exception surfaces** (not masked) | **new (critical):** `__exit__` masking-prevention           |

**Cross-cutting (core-only; extension-point does not fire):**

| #   | Precondition                                                  | Result                                                                                               | Test                                      |
| --- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| S21 | any teardown, **legacy** state (no carry recorded)            | callback warns "predates wt-hooks; can't harvest" via `ctx.legacy`, returns → proceed (don't drop)   | existing legacy test                      |
| S22 | teardown, worktree already torn down by a **peer** (dir gone) | no-op return **before** any `before_teardown` (`worktree.py:681`/`:883`/`:913`) — nothing to harvest | `e2e/test_wt_parallel_transition_done.py` |

P1 turns S3, S8, S10, S12, S16, S19, S20 (the new cells) into tests alongside the
existing-green ones; S19 + S20 are the cleanup-contract regression guards and are
**must-add**.

## Consequences

**Positive.**

- The engine becomes a self-contained, hook-agnostic, tracker-agnostic
  git-worktree core with an explicit contract; future wt tasks reason about a
  small surface.
- The one-directional import-lint prevents accretion creep back into core (the
  HATS-715 leaf-regression class) — a guard that does **not** exist today
  (`worktree.py` freely imports `.paths` / `.models`).
- Behavior is held constant: a structural refactor with the full e2e/unit suite
  green; no engine-behavior change.
- B is the correct first phase of A: if an external consumer materializes, the
  already-isolated `wt/` is a near-mechanical `git filter-repo` away.

**Negative / costs.**

- M-sized churn across `worktree.py` / `worktree_locks.py` / `worktree_hooks.py`
  / `state.py` / `cli/worktree.py`, plus a new sub-package move.
- A small indirection (callback bundle) replaces the inline hook calls — the cost
  of making the core hook-agnostic.

**Realized by child impl tasks (filed after approval, report §6 order; do P1
first):**

- **P1 — Lift the hook layer + introduce the extension-point contract.** Move
  `collect_carry_for_role` and `serialize_collected_hooks` out of
  `worktree_hooks.py` into an ai-hats module (e.g. `wt_carry.py`); update the two
  callers (`state.py:1030`, `cli/worktree.py`). Define the core `WorktreeLifecycle`
  bundle + `LifecycleContext` (incl. `legacy`) + the core `WorktreeTeardownAborted`
  exception (D2/D8); **relocate `WorktreeHookError` to ai-hats**. Replace the
  core's `_run_wt_in_hooks` / `_run_wt_out_hooks` bodies with `on_created` /
  `before_teardown` calls, keeping each route's control-flow (merge/discard
  propagate; `cleanup` keeps its `try/except WorktreeTeardownAborted: warn; return`
  — D8). ai-hats supplies the callback that runs `run_worktree_hook` (D7-contract
  stays ai-hats-side) and raises `WorktreeTeardownAborted(...) from
  WorktreeHookError(...)` on fail-closed. This also drops `from .models import
  WT_TEARDOWN_EVENTS` from core (its only consumer is lifted here). After P1 the
  core has no hook policy and no `Assembler` / `composer` / `models` reachability.
  Swap the two CLI catches (`cli/worktree.py:210`/`:365`) to `except
  WorktreeTeardownAborted` reading `str(e.__cause__)`; re-home the hook-execution
  unit tests (`test_worktree_wt_hooks.py`, S6/S14/S18) to the ai-hats side with the
  real bundle, and rewrite `pytest.raises(WorktreeHookError)` → `…Aborted`. Turn
  scenario cells S3/S8/S10/S12/S16/**S19**/**S20** into tests. **e2e-gate applies**
  (touches `worktree.py` + `cli/worktree.py`).
- **P2 — Inject the path base** (D4): `state_dir` param (only — `hooks_dir`
  dropped, see the D4 implementation note) on `__init__` **and** on
  `load_for_task` / `load_for_branch` / `list_active`; thread into
  `worktree_locks` (lock-path-identity invariant) and every call site
  (`state.py`, `cli/task.py`, `cli/worktree.py`, `subagent_runner.py`, the L2
  re-check). After P2 the core imports no `ai_hats.paths`. **The D6 import-lint
  was folded into P2** (HATS-850) so the task ships an enforced guarantee, keyed
  on `WT_CORE_MODULES = ("worktree", "worktree_locks")` in
  `test_import_hygiene.py` (RED-under-revert).
- **P3 — Formalize `src/ai_hats/wt/`**: move the now-pure engine files under the
  sub-package and re-point the D6 lint's `WT_CORE_MODULES` to the `wt/` package
  prefix (one-line generalization of the rule P2 already added); add the explicit
  `wt/__init__.py` public exports (D9). (The lint itself shipped in P2.)
- **P4 — Leave accretions in place**, now importing *from* `wt/`
  (`state._setup/_teardown_worktree`, `cli/task.py` translation, `_project_dir`,
  `wt_carry`); verify nothing imports *into* `wt/`; add the standalone bare-repo
  smoke test (D9).

**Test strategy for the impl (named for the e2e-gate, acceptance #3).** The
scenario matrix above is the test plan: each Sn maps to a test, existing-green or
new. The unit tier (16 `test_worktree*.py`) runs against a bare `git init` with
no `ai-hats.yaml` — strong evidence the core is already decoupled; **most** of it
travels with the extracted core unchanged.

- **Hook-execution unit tests re-home to ai-hats (P1, not "unchanged").**
  `tests/test_worktree_wt_hooks.py` `from ai_hats.worktree import WorktreeHookError`
  (`:18`) and asserts hook side-effects through the **bare core**
  (`mgr.create(wt_hooks=…); assert sentinel.exists()`, `:61–62`), plus
  `pytest.raises(WorktreeHookError)` on `mgr.merge()` / `.discard()` (`:81`, `:91`).
  After the lift a bare-core manager has the **no-op** bundle and runs **no
  hooks**, so these assertions and the import break — their subject (*the core runs
  hooks*) is exactly what leaves the core. P1 re-homes these to the ai-hats side
  (construct with the real hook-running bundle) and rewrites the `pytest.raises`
  to `WorktreeTeardownAborted` (asserting `__cause__` is a `WorktreeHookError`).
  The matrix rows that name this file — S6 / S14 / S18 (the unit hook-execution
  cells) — move with it; the S7 / S15 fail-closed guards are e2e (driven through
  the real launcher with the real bundle) and stay put.

Risk otherwise concentrates in the e2e tier:

- **Cleanup-contract regression guards (must-add, P1):** S19 (cleanup hook-fail →
  dir preserved, **no exception**) and S20 (`__exit__` with an in-flight agent
  exception + hook-fail → agent's original error surfaces, not masked). These pin
  the A′ fix; without them the binary-contract data-loss bug could silently
  regress.
- **Fail-closed invariant guard:** `tests/e2e/test_wt_hooks_fail_closed.py` stays
  green across P1 (S7/S15); `tests/e2e/test_wt_in_runs.py` covers S2.
- **Teardown routes:** `tests/e2e/test_wt_merge_*.py` (already-merged, drift,
  conflict-preserves-review, failed-preserves-worktree, head-wandered, mid-merge,
  null-original-branch) + `test_wt_parallel_transition_done.py` cover S9/S11/S22.
- **Hook-agnostic + standalone (P1/P4, D9):** a test that a bare core with the
  **no-op** bundle + **project-local** path fallback drives `create()/merge()` on
  a bare `git init` with **zero** hook execution and no `ai-hats.yaml` — proving
  the policy moved and the engine is consumable standalone (S1/S5/S13).

**Kill-criterion (resolved by A′; retained as a guard).** The plan's off-ramp was
"if the fail-closed ordering cannot be preserved across all routes with an
injected callback." The review surfaced exactly that risk at the `cleanup` route;
A′ resolves it (the abort is a core-owned `WorktreeTeardownAborted`, and per-route
propagate-vs-suppress is core control-flow that already exists). The criterion now
fires only if implementation finds a route whose correct behavior is *still*
inexpressible as "raise-to-abort + core per-route handling" — none is known
(every route already calls one `_run_wt_out_hooks(event)` before
`_remove_worktree()`, the substitution is positional).

## Alternatives considered

- **Option A — separate repo / pip package.** Rejected: no external consumer; A
  pays distributed-system costs (versioning, version-skew CI, split test suite,
  co-release-in-practice) for monolithic coupling. Activation trigger: a real
  external consumer appears → A becomes a near-mechanical `filter-repo` of `wt/`.
- **Hook run/collect inside the core** (the investigation author's placement).
  Rejected by the supervisor directive: the entire ADR-0012 hook layer is an
  ai-hats accretion. The core exposes the lifecycle points the run would have
  fired from, nothing more.
- **Event-bus / observer extension-points.** Rejected: incompatible with
  fail-closed (handlers' exceptions must propagate and abort teardown, not be
  swallowed/aggregated). See D2.
- **Binary callback contract with the swallow as ai-hats policy** (the first-draft
  D8: "core only sees returned-vs-raised; the `cleanup` swallow is the callback
  choosing not to raise"). Rejected — *provably loses data*: at `cleanup`, "callback
  returns" makes the core proceed to `_remove_worktree()`, destroying the
  unharvested worktree; "callback raises" preserves it but propagates out of
  `__exit__`, masking the sub-agent's original error. The three-outcome `cleanup`
  route is unreachable from a binary callback alone — the swallow is intrinsic
  core control-flow (D8). Caught at design review.
- **Reuse `WorktreeHookError` in core as the abort signal (A).** Rejected in
  favor of A′: `WorktreeHookError` is hook vocabulary and belongs with the
  lifted hook layer; the core's abort signal is the hook-agnostic
  `WorktreeTeardownAborted`, with the hook error riding as `__cause__` (D8).
- **Localize `WT_TEARDOWN_EVENTS` into core.** Rejected: its only core consumer is
  lifted in P1, so there is nothing to "remove"; a core copy would duplicate the
  teardown vocabulary against the `models.py` validator (drift hazard). It stays
  ai-hats-side; the core fires plain string literals (D4).
- **wt CLI moved into the core** (taking `project_dir` as an arg; investigation
  §4). Rejected: drags the tracker redirect (`_project_dir`) and error-recipe
  translation toward the core, or forces the core to re-implement project-dir
  resolution — against the "core is minimal / tracker-agnostic" directive. See D7.
- **Lint-only, skip the refactor.** Rejected — *empirically impossible today*:
  `worktree.py` imports `.paths` + `.models` at module level (`worktree.py:21–22`),
  so an import-lint forbidding them fails immediately; the lint can only be added
  *after* P1–P2 remove those imports. The lint is the enforcement of P1–P2, not
  an alternative to them.
- **Generic `carry` / `extra` opaque state key** instead of keeping `wt_hooks`.
  Rejected: forces a state migration for in-flight worktrees for zero behavior
  gain (D5).

## References

- `wt-extraction-report.md` — the investigation attached to HATS-841 (coupling
  map, T-shirt sizes, file:line evidence). The supervisor refinement (lines 6–16)
  is the source of the hook-agnostic-core directive.
- `src/ai_hats/worktree.py` — `WorktreeManager`; the create site (`:633`) and the
  teardown sites (`:724`, `:802`, `:841`, `:893`, `:932`, `__exit__` `:954`);
  `_run_wt_in_hooks` / `_run_wt_out_hooks` (`:970`, `:998`); the module-level
  first-party imports to remove/relocate (`:21` models, `:22` paths, `:23`
  `run_worktree_hook`); `WorktreeHookError` (`:69`, relocates to ai-hats, D8);
  `save_state` opaque carry (`:1064`).
- `src/ai_hats/worktree_locks.py` — the L1–L4 lock model (ADR-0006) that stays in
  core; `worktrees_dir` use at `:201` (path-base injection target, D4).
- `src/ai_hats/worktree_hooks.py` — `run_worktree_hook` (stays ai-hats, D1/D8);
  `collect_carry_for_role` (`:67`) + `serialize_collected_hooks` (the collection
  chokepoint lifted in P1).
- `packages/ai-hats-tracker/src/ai_hats_tracker/state.py` — `_setup_worktree` (`:981`, the `create` + carry-collect
  call site, `:1030`) / `_teardown_worktree` (`:1051`, `load_for_task` `:1085`).
- `src/ai_hats/cli/worktree.py` — the `wt` Click group; `_helpers` imports
  (`:13`) that keep the CLI an ai-hats wrapper (D7).
- `src/ai_hats/models.py` — `WT_TEARDOWN_EVENTS` (`:203`) **stays ai-hats-side**
  as hook vocabulary (pydantic validator `:477–485`); NOT localized — the core
  fires plain string literals (D4).
- `tests/test_import_hygiene.py` — the stdlib import gate (HATS-758) extended with
  the one-directional rule (D6).
- [ADR-0006](0006-worktree-concurrency-layered-defense.md) — the lock model that
  stays in core. [ADR-0012](0012-worktree-data-transfer.md) — the hook layer that
  stays outside it. HATS-715 — extracted locks (the decoupling precedent and the
  regression class the import-lint prevents). HATS-524 — tracker redirect (stays
  ai-hats). HATS-509 — facts-only exception bodies (CLI owns recipes).
- `docs/glossary.md` — "Worktree core (`wt/`) boundary" entry.
