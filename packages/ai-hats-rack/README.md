# ai-hats-rack

Minimal backlog kernel (epic HATS-1014, child K1 / HATS-1020), built **parallel** to
`ai-hats-tracker` — same `task.yaml` format, new engine. The kernel is a light FSM plus
transactional machinery, shipped with the K2 doc store (fs-as-truth view + frozen pins)
and project-root resolver; everything else (worktree, ownership, scaffold, plan-gate,
epic-automation, bash hooks) is an extension subscribing to kernel events.

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
- **backlog.yaml is the SSOT** of the topology (9 states; reclaim `execute→execute`, reopen
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
- **frozen-integrity** (HATS-1031) — in-lock on EVERY edge (the full state
  product, forced edges included): scans the task's frozen pins and aborts any
  transition whose pinned document changed or vanished; the reason carries the
  document name, both digests and the recovery recipe
  (`transition <ID> --freeze <name> --ack-frozen` / `--rm <name> --ack-frozen`).
  No waivers — force, epics and automation actors do not bypass evidence
  integrity. Priority 8: after the ownership single-slot guard, before the
  plan-gate. `standalone_extensions()` is the standalone kit
  (frozen-integrity + scaffold + gate).
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
— its consumer is **K7 audit log** (below); the bare kernel persists nothing.

### Audit log (K7)

The CLI attaches `JsonlJournalSink`: every dispatch appends one JSON line (schema `v: 1`:
ts, event, detail — edge from/to / epicify child / pre-destroy operation —, actor,
force+reason, per-subscriber outcomes, `result: persisted|aborted`, identity block) to
`tasks/<ID>/audit.jsonl`, size-rotated into `audit-NNN.jsonl` segments. Lossless by
contract (PROP-004): nothing is truncated or deleted. The identity block carries the
writing process's `AI_HATS_SESSION_ID` + `AI_HATS_ROOT_PID`, a verdict against the
claimed actor (`verified`/`mismatch`/`unverified` — blind zones are marked, PROP-080),
and an ownership-holder cross-check when `ownership.json` exists (PROP-076). Journal
write failures are loud on stderr but never break the already-persisted operation.
`rack context <ID> --attr audit` (`--event/--since/--actor`) is the query surface; it
warns when a task moved states with an empty journal (zero-events, PROP-005/076).

## CLI

Four verbs, each with `--json` (JSON-first, HATS-1031 API-D surface):

| Verb                   | Role                                                                                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `rack create <title>`  | new card; `--id/--parent/--depends/--tag/...`; initial state from backlog.yaml                                                |
| `rack ls [<ID>]`       | backlog scan (`--grep/--tag/--state/--parent`) or graph walk (`ls <ID> --deep N [--link <glob>…]`, repeatable OR)             |
| `rack context <ID>`    | THE read package: full card + top-level `links` + document paths; repeatable `--with <glob>` embeds, `--attr audit\|work_log` |
| `rack transition <ID>` | THE mutating verb: an ordered composite of ops under one lock                                                                 |

`transition` ops run in **argv order** under ONE task lock with a single persist;
effects of earlier ops are visible to later ones, any abort rolls the whole
sequence back (HATS-1030):

| Op                       | Effect                                                                           |
| ------------------------ | -------------------------------------------------------------------------------- |
| `--state <s>`            | FSM edge (guard + two-phase dispatch); `transition <ID> <s>` is sugar for it     |
| `--attach <src>[:n]`     | copy a file into `tasks/<ID>/` as document `n` (default: basename)               |
| `--freeze <name>`        | pin `{name, digest, frozen}`; re-pinning drifted content requires `--ack-frozen` |
| `--rm <name>`            | trash the document (recoverable, HATS-470); a frozen one requires `--ack-frozen` |
| `--log <msg>`            | append a work_log entry                                                          |
| `--link <kind>:<id>`     | add an edge of any configured, non-derived kind (default kind: `related`)        |
| `--unlink [<kind>:]<id>` | remove the edge(s) to `<id>`                                                     |

Command-level flags: `--force` (+ mandatory `--reason`) relaxes the FSM arrow only;
`--resolution` / `--final-state` stamp terminal metadata; `--ack-frozen` is the tiered
frozen hatch shared by `--rm` and `--freeze`.

The backlog root is resolved by a walk-up from CWD to the nearest ancestor
holding `.agent/` or `ai-hats.yaml` (K2, HATS-197 heir); from inside a linked
task worktree (neither marker present) a pure-filesystem gitlink hop resolves
the main checkout instead (HATS-1038 C2). `ai-hats.yaml` supplies `ai_hats_dir`
and `task_prefix`. Resolution never mkdirs, and outside any project it answers
with a typed `no_project_root` error instead of bootstrapping a phantom tracker
(HATS-839 heir). `--tasks-dir` / `RACK_TASKS_DIR` stay as the explicit override
(still anchoring `project_dir` at the real root, C2 gap #3).

```
$ rack transition HATS-001 done --tasks-dir tasks
error: Invalid transition for HATS-001: brainstorm → done. Legal edges from 'brainstorm': plan, blocked, cancelled
```

### Field edits — coexistence with `ai-hats task` (cutover, HATS-1038 C3)

`rack` has **no `update` verb**. During the cutover it owns lifecycle, reads,
documents, and links; scalar card fields are edited with the tracker's
`ai-hats task update <ID>` — a deliberate coexistence until field mutation is
generalized onto the rack (HATS-1044), not a gap. What lives where:

| Edit                                                             | Home                               |
| ---------------------------------------------------------------- | ---------------------------------- |
| state, work_log, documents (attach/freeze/rm), links, resolution | `rack transition <ID> --…`         |
| new card + initial parent                                        | `rack create …`                    |
| title, description, priority, reviewer, role, tags, re-parent    | `ai-hats task update <ID> --…`     |
| `hyp` · `proposal` · `close` · `plan-extract` · `sync`           | `ai-hats task …` (until HATS-1044) |

Both CLIs read and write the same `task.yaml` under the same task lock, so the
split is safe to interleave within one task.

## Doc store (K2, rev4 semantics)

**fs-as-truth**: the only way to write a document is to write a file into
`tasks/<ID>/` — `doc put`/`doc cat` do not exist. The ledger is a **view**:
`rack context` live-scans the directory (dotfiles and `task.yaml` excluded; legacy
`attachments/` blobs simply appear) and digests on the fly, so a directly-written
file is visible immediately — no registration, no write→register race.

- `rack context <ID>` prints a `Documents` block: name + **absolute path** + mtime +
  frozen mark (`frozen ✓` / `frozen ✗ modified|missing`); `--json` adds size,
  `sha256:<12hex>` digest and `drift`. Discovery, not injection — content is never
  inlined without `--with`; the agent reads by path (the 210K-character baseline F4
  lesson). Verification is internal: every context/ls checks pins; there is no
  `verify` verb. The read surface only FLAGS drift — the frozen-integrity extension
  (above) is what blocks transitions on it.
- freeze/rm are `transition` ops (table above): pins persist in task.yaml under the
  task lock; removal trashes to `$TMPDIR` (recoverable), never hard-deletes.

## Data format

Reads/writes the tracker's `task.yaml` unchanged; unknown keys (e.g. the legacy
`attachments` manifest) round-trip verbatim via `extras`. Old cards load with
defaults. The only K2 addition is the `documents:` list of frozen pins.
