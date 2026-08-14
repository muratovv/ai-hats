# ADR-0015: Task ownership & crash-safe handoff between agents

## Status

Accepted (HATS-955, 2026-07-09).

## Context

A task may be left mid-flight: ai-hats is closed (the agent process dies) or the
agent moves on. A second agent needs to (a) detect that a task is abandoned and
safe to pick up, and (b) be guaranteed the first agent cannot silently re-take it
or double-work its worktree. Before this change there was no runtime ownership at
all — `assignee` means "human responsible", the agent's `AI_HATS_SESSION_ID` is
never persisted onto a task, and worktrees survive a crash untorn-down.

This is a distributed-lock problem, but with a local, single-host, file-based
substrate and an unusual resource: the protected thing is a git **worktree**, and
a resurrected owner would write to it *directly with files* — unfenceable
cheaply. That rules out TTL leases (which admit a window where a live-but-slow
worker is wrongly reclaimed).

## Decision

**Single serialized ownership registry + a single-slot rule, orthogonal to task
state.**

1. **Registry.** One local JSON file (`<tasks_dir>/../ownership.json`, gitignored)
   guarded by one `filelock`, keyed by task id → `{session_id, root_pid,
   start_time_utc, claimed_at}`. Every op takes the lock, loads the whole registry,
   sweeps dead records, decides in RAM, atomic-writes; **reads take the lock too**
   (`filelock` has no shared mode). `owner_of` is O(1).

2. **Reclaim-on-certain-death, no TTL.** Liveness = owner `root_pid` +
   `start_time_utc` (`ps -o lstart=` under a pinned `TZ=UTC`/`LC_ALL=C` so the
   claiming and the checking session render one instant identically — unpinned,
   an ambient difference read as reuse and stole a live owner's task, HATS-1339;
   reuse-proof, `os.kill` fallback), the same
   technique as the version-GC liveness ref (ADR precedent). `record_is_live` is
   **biased to True on any uncertainty** — a transient `ps` error must never read
   as death, or a working neighbour's task gets stolen. `False` only on positive
   proof (no such process, or a reused pid).

3. **Ownership is tied to the `execute` state, and is orthogonal to it.** Claim on
   *entering* execute; release on *leaving* it (or reaching a terminal state).
   A crashed owner leaves the task in `execute`; a second agent reclaims via the
   **`execute → execute` self-loop** — a plain (FSM-valid, non-force)
   `transition <id> execute`, gated by the ownership claim, not by the FSM.

4. **Single-slot on every transition.** An agent transitions the task it owns, or
   tasks when it owns nothing; transitioning any *other* task while holding one is
   refused. This makes a live owner provably still on its task — which is what
   makes task-keyed ownership correct.

5. **No force-steal.** `--force` relaxes the FSM guard, never ownership: a live
   owner is respected; a stuck one is reclaimed only once its process dies (kill
   it). Release is purely FSM-driven — there is no standalone `stop` verb.

6. **Liveness anchor via env.** The durable session process exports
   `AI_HATS_ROOT_PID` (harness: `wrap_runner` / `subagent_runner`); the ephemeral
   `ai-hats task` subprocess reads it. Without it (a bare standalone tracker, no
   harness) ownership degrades to advisory single-slot with no death-reclaim.

## Consequences

- Ownership lives entirely in the tracker package (`ownership.py` + inline calls
  in `transition`) — no integrator module, no `OwnershipEffects` DI seam. The
  integrator contributes only the env export. Liveness is inlined (a ~30-line
  copy of `version_refs`) rather than lifted to `ai_hats_core`: one consumer, so
  a shared home would couple two packages for no gain.
- Ownership is never committed (a pid is host-local, would merge-conflict).
  Single-host only — cross-host coordination is out of scope.
- The single-slot-everywhere rule is strict: an agent executing T1 cannot advance
  *any* other task (not even brainstorm→plan) until it leaves execute on T1.
- Operator surface: `task list --reclaimable` shows execute-state tasks whose
  owner is dead/absent; reclaim = re-`transition <id> execute`.
- Out of scope (activation triggers in the HATS-955 plan): per-write epoch
  fencing, TTL leases, a reverse index, a visible `parked` state, an auto-reclaim
  daemon. Epic-transition policy is scattered and tracked for a cohesive refactor
  (HATS-958).

## Divergence — where ownership actually lives (2026-08-14, HATS-1655)

The mechanism — all six decisions above — is intact and shipping. The **first
consequence is not**: ownership no longer lives in the tracker package, and it
no longer works by inline calls in `transition`.

- `packages/ai-hats-tracker` was deleted with the CLI it backed (HATS-1262,
  `710f45d3`). The registry module moved to the **integrator** —
  `src/ai_hats/ownership.py` (`take`, `finish`, `held_by`, `owner_of`, `sweep`,
  `record_is_live`), still one `filelock`-guarded JSON at
  `<tasks_dir>/../ownership.json`, still `ps -o lstart=` under a pinned
  `TZ=UTC`/`LC_ALL=C`, still the deliberate ~30-line copy of `version_refs`
  (its module docstring says so).
- The "no integrator module, no `OwnershipEffects` DI seam" clause did not
  survive the move onto the rack. Ownership is now three rack **subscribers**
  wired from `src/ai_hats/rack_wiring.py` — `OwnershipSingleSlot` (in-lock,
  priority 5, every edge), `OwnershipClaim` (priority 20, edges into `execute`),
  `OwnershipRelease` (priority 40, edges leaving `execute` or terminal, plus a
  post-lock `epicify` reaction). The seam the ADR refused is exactly what the
  declaration/code-channel split of ADR-0017 §4 required; the *reasoning* that
  refused it ("one consumer, a shared home would couple two packages") no
  longer applies once the consumer and the integrator are the same package.
- What that buys, and it is a strengthening rather than a loss: the ordering the
  ADR left implicit is now an explicit priority chain, and release fires on
  *leaving* execute through `_keys_leaving_execute_or_terminal` — forced
  non-topology exits included. The `execute → execute` reclaim self-loop of
  point 3 is a declared edge in
  `packages/ai-hats-rack/src/ai_hats_rack/backlog.yaml`
  (`{ from: execute, to: execute, name: reclaim }`), and "force does not bypass
  ownership" (point 5) is spelled in both refusal messages.
- The session id the registry keys on comes from the identity envelope
  (`SessionIdentity.from_env`, ADR-0024) rather than being read raw; the
  `AI_HATS_ROOT_PID` liveness anchor of point 6 is unchanged, but its ephemeral
  reader is now `rack`, not the retired `ai-hats task`.
- **The operator surface of the last consequence is gone.** `task list --reclaimable`
  has no rack equivalent — `docs/migration-v0.14.0.md` lists it under "Removed
  with no equivalent". Reclaim itself still works exactly as decided:
  re-`rack transition <id> execute`.
