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

`rack create/show/transition/log` + the `rack doc` group, each with `--json`
(JSON-first). The backlog root is resolved by a pure walk-up from CWD to the nearest
ancestor holding `.agent/` or `ai-hats.yaml` (K2, HATS-197 heir); `ai-hats.yaml`
supplies `ai_hats_dir` and `task_prefix`. Resolution never mkdirs, and outside any
project it answers with a typed `no_project_root` error instead of bootstrapping a
phantom tracker (HATS-839 heir). `--tasks-dir` / `RACK_TASKS_DIR` stay as the
explicit override.

```
$ rack transition HATS-001 done --tasks-dir tasks
error: Invalid transition for HATS-001: brainstorm → done. Legal edges from 'brainstorm': plan, blocked, cancelled
```

## Doc store (K2, rev4 semantics)

**fs-as-truth**: the only way to write a document is to write a file into
`tasks/<ID>/` — `doc put`/`doc cat` do not exist. The ledger is a **view**:
`rack doc ls` / `rack show` live-scan the directory (dotfiles and `task.yaml`
excluded; legacy `attachments/` blobs simply appear) and digest on the fly, so a
directly-written file is visible immediately — no registration, no write→register
race.

- `rack doc ls <ID>` — name, absolute path, mtime, `sha256:<12hex>` digest, frozen
  mark, drift status. A frozen pin whose file changed (or vanished) exits 1.
- `rack doc freeze <ID> <name>` — pins `{name, digest, frozen}` into task.yaml
  (atomic tmp+rename under the task lock). Idempotent on unchanged content;
  re-pinning changed content requires `--refreeze`.
- `rack doc rm <ID> <name>` — moves the file to a trash session under `$TMPDIR`
  (HATS-470 delete policy: recoverable, never hard-deleted); a frozen document
  additionally requires `--ack-frozen` and drops its pin.
- `rack show <ID>` prints a `Documents` block: name + **absolute path** + mtime +
  frozen mark. Discovery, not injection — content is never inlined; the agent reads
  by path (the 210K-character baseline F4 lesson). Verification is internal: every
  ls/show checks pins; there is no `verify` verb.

## Data format

Reads/writes the tracker's `task.yaml` unchanged; unknown keys (e.g. the legacy
`attachments` manifest) round-trip verbatim via `extras`. Old cards load with
defaults. The only K2 addition is the `documents:` list of frozen pins.
