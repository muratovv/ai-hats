# ai-hats-rack

Minimal backlog kernel (epic HATS-1014, child K1 / HATS-1020), built **parallel** to
`ai-hats-tracker` — same `task.yaml` format, new engine. The kernel is a light FSM plus
transactional machinery; everything else (worktree, ownership, scaffold, plan-gate,
epic-automation, doc store, bash hooks) is an extension subscribing to kernel events.

## Transition pipeline

```
FileLock(task) → FSM-guard → in-memory mutation → in-lock subscribers → SINGLE persist (last)
                                                                       → lock release
                                                                       → post-lock subscribers
```

- **Single persist is unbypassable by construction**: subscribers get an immutable state
  copy + return a delta; they hold no store reference. Any raise before the persist means
  zero bytes changed on disk (heirs of HATS-723 / HATS-481 / HATS-866-AC3).
- **Bare kernel = pure FSM**: `Kernel(tasks_dir)` with no subscribers walks the full
  lifecycle with no side effects (heir of HATS-866/AC4). Unit tests never need git.
- **`force` relaxes only the FSM arrow** and requires a reason (journaled). It is passed
  to subscribers as information, never as a safety-off switch (HATS-518/596/697).
- **fsm.yaml is the SSOT** of the topology (9 states; reclaim `execute→execute`, reopen
  `done→execute`, `blocked` hub, `cancelled` exits). Editing it edits the kernel contract.
  `document` must exist (PROP-012). Invalid transitions answer with the legal edges.

## Subscriber contract

```python
class MyExtension:
    name = "my-extension"
    def subscriptions(self) -> list[Subscription]:  # (event_key, phase, priority)
        return [Subscription("edge:plan--execute", Phase.IN_LOCK, priority=10)]
    def on_event(self, ctx: DispatchContext) -> Delta | None:
        ...  # return a Delta, None, or raise AbortOperation("actionable reason")
```

- `DispatchContext` carries: `event`, `task` (deep copy), **`caller_cwd`** (mandatory;
  subscribers never read `Path.cwd()` — HATS-840), **`is_epic`** (recomputed from the
  current child-set on every dispatch — HATS-794/977/979), `actor` (who triggered:
  `session:…` / `agent:…` / `human:…`), `force` + `reason`.
- **in-lock** phase: blocking; `AbortOperation` → typed `OperationAborted` with the
  actionable reason (the reason channel); any other exception propagates raw. Either way
  nothing is persisted. There is no catch-and-warn mode for this phase.
- **post-lock** phase: reactions after persist + lock release. Failures are journaled as
  `error` outcomes — reported, never swallowed, never aborting. Post-lock extensions may
  drive further kernel calls (one task lock at a time, never nested — HATS-690 rule).

## Event registry (name-your-consumer, PROP-030)

| Event key           | Fired by                                             | Named consumer                                                                                                |
| ------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `edge:<from>--<to>` | `Kernel.transition`                                  | K3 core extensions (plan-gate, ownership, worktree in-lock; epic-automation, views post-lock); K4 hook-runner |
| `epicify`           | `Kernel.create` / `Kernel.set_parent` (child gained) | K3 ownership + worktree reconciliation handlers (idempotent release / `discard_if_empty`, HATS-977/979)       |
| `pre-destroy`       | extensions via `Kernel.publish`                      | K3 guards on irreversible ops (abort / extract before worktree merge-discard, PROP-047/058)                   |

A new event lands in this table together with its subscriber, or it does not land.

## Stock extensions (K3, HATS-1022)

Pure extensions live in `ai_hats_rack.extensions` (no integrator/wt/git imports):

- **plan-scaffold / plan-gate** — one config-driven section catalog feeds both,
  so template and enforcement can never drift (HATS-635); the gate names every
  empty required section, waives epics (HATS-794) and reopen (HATS-328).
  `standalone_extensions()` is the standalone kit (scaffold + gate only).
- **epic-automation** — post-lock; the pure `decide()` table maps every epic
  source state × child trigger to reopen/advance/activate/no-op
  (HATS-690/692/789) and drives the epic through journaled FSM-valid kernel
  hops under the `rack:epic-automation` actor (also the anti-cascade guard).
- **derived-views** — post-lock STATE.md regeneration, own lock, atomic replace.

Ownership and worktree adapters depend on the integrator's wt engine and live
on the integrator side (`ai_hats.rack_wiring`, with `build_rack_kernel()` as
the assembly mirror of `cli/_helpers._task_manager`); the boundary stays
one-directional — the rack never imports them.

## Lock model (deadlock excluded structurally)

| Lock                      | Scope                                         | Holder    |
| ------------------------- | --------------------------------------------- | --------- |
| `tasks/<ID>/.lock`        | transaction window: guard → in-lock → persist | kernel    |
| `tasks/.alloc.lock`       | atomic id alloc+reserve (HATS-936)            | kernel    |
| resource locks (git/base) | inside the owning extension's operation       | extension |

Rules: in-lock subscribers have **no API to take locks** (max one task lock held at any
time); post-lock subscribers are notified **after** release; acquisition order is always
task lock → resource lock; every kernel lock uses the single loud-fail timeout (30s).

## Journal

Every dispatch produces a `DispatchRecord` (event, task, actor, force+reason, one
outcome per subscriber: `ok` / `delta` / `abort` / `error`). Records ride the result of
every mutating call, including aborted dispatches. `JournalSink` is the persistence seam
— its consumer is **K7 audit log**; K1 persists nothing.

## CLI

`rack create/show/transition/log`, each with `--json` (JSON-first). Root resolution is
explicit (`--tasks-dir` / `RACK_TASKS_DIR`); the walk-up project resolver belongs to K2.

```
$ rack transition HATS-001 done --tasks-dir tasks
error: Invalid transition for HATS-001: brainstorm → done. Legal edges from 'brainstorm': plan, blocked, cancelled
```

## Data format

Reads/writes the tracker's `task.yaml` unchanged; unknown keys (e.g. `attachments`,
owned by K2) round-trip verbatim via `extras`. Old cards load with defaults.
