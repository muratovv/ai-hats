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
   start_time, claimed_at}`. Every op takes the lock, loads the whole registry,
   sweeps dead records, decides in RAM, atomic-writes; **reads take the lock too**
   (`filelock` has no shared mode). `owner_of` is O(1).

2. **Reclaim-on-certain-death, no TTL.** Liveness = owner `root_pid` + OS
   `start_time` (`ps -o lstart=`, reuse-proof; `os.kill` fallback), the same
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
