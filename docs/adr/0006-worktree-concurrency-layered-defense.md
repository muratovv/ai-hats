# ADR-0006: Worktree concurrency — layered defense

## Status

Accepted (HATS-477 epic, 2026-05-25). Realized incrementally by
HATS-121, HATS-479, HATS-481, HATS-480.

## Context

`ai-hats wt` is the framework's git-worktree-based isolation
subsystem: each task or sub-agent gets its own branch + filesystem
working copy, surfacing through `ai-hats wt create / merge / discard`
plus the auto-creation path in `ai-hats task transition <id> execute`.

The subsystem accreted four distinct concurrency primitives over
~7 months, each shipped against a confirmed race condition or silent
data-loss bug. By mid-2026 the picture was 4 separate docstrings,
4 plan.md files, scattered constants, and no architectural overview.
A maintainer asking "why are there so many lock files under
`<ai_hats_dir>/sessions/worktrees/`?" had nowhere to look.

The bugs that drove the layering:

| Ticket   | Race / bug                                                                                | Year-Q   |
| -------- | ----------------------------------------------------------------------------------------- | -------- |
| HATS-121 | `save_state` / `_clear_state` / `_load_by_key` corrupting the per-key state JSON          | 2025-Q4  |
| HATS-479 | TOCTOU `wt create` parallel + `/tmp` leak + branch-graveyard on `git worktree add` fail   | 2026-Q2  |
| HATS-481 | Silent data loss in `task transition done` — `index.lock` contention swallowed at WARNING | 2026-Q2  |
| HATS-480 | R-03: `wt merge X` vs `wt discard X` on the same branch — half-merged commit / branch graveyard | 2026-Q2  |

A fifth ticket (HATS-486 — stale-lock detection) is filed; without an
explicit architectural model it would have drifted in granularity or
lock-ordering.

## Decision

Codify a **four-layer model** with a fixed lock-ordering hierarchy.
Each layer is keyed at the granularity matching its actual contention
surface; layers compose without inversion, so no deadlock is reachable.

### П1 — Layered granularity, NOT a single repo-wide lock

Each subsystem gets its own lock keyed at the right scope:

| Layer | Lock                                | Key granularity              | Hold time     | Closes                                  |
| ----- | ----------------------------------- | ---------------------------- | ------------- | --------------------------------------- |
| 1     | `<state>.json.lifecycle.lock` (480) | per `(project, wt_branch)`   | seconds       | merge↔discard on same wt branch         |
| 2     | `<state_dir>/.base-<ref>.lock` (481)| per `(project, base_ref)`    | hundreds of ms| Two merges into the same base ref       |
| 3     | `<state_dir>/.git-worktree-create.lock` (479)| repo-wide, create-only | tens of ms    | TOCTOU `git worktree add` across peers  |
| 4     | `<state>.json.lock` (121)           | per `(project, state JSON)`  | microseconds  | Concurrent state JSON I/O               |

A repo-wide write-lock would have collapsed all of this into one
file, but it degrades parallelism unacceptably: two merges into
**different** base refs (`master` + `develop`) would serialize for
no reason. The chosen keying matches bors / Kodiak / Mergify
consensus for the merge layer, and the natural sharding for the others.

### П2 — Lock ordering hierarchy is fixed; future layers justify their position

When more than one lock is held simultaneously, acquisition is
**always outer → inner** by layer number above:

```
1. lifecycle  (per wt branch)        ← outermost, longest hold
2. base       (per base ref)
3. create     (repo-wide, create-only) — never co-held with merge layers
4. state-JSON (per state JSON)        ← innermost, shortest hold
```

In practice only `merge()` co-holds layers 1+2+4 (lifecycle → base →
state); `create()` co-holds 3+4; everything else is single-layer.
There is no path where an inner layer is held while reaching for an
outer one, so deadlock is unreachable by construction. A new layer
is introduced only after justifying its position in this hierarchy —
the module docstring of `worktree.py` is the canonical reference.

### П3 — Two contention sources, two strategies

Internal contention (ai-hats vs ai-hats, same machine) is closed by
`filelock.FileLock` (`fcntl.flock` advisory locks). External
contention (an IDE, a manual `git commit`, a long-running script
briefly holding `.git/index.lock` or `.git/config.lock`) is closed
by:

* **AWS full-jitter exponential backoff** retries on a whitelisted
  stderr substring set (`unable to create`, `index.lock`,
  `another git process`, `could not lock`).
* **Git's own wait flags** — `core.filesRefLockTimeout=5000` /
  `core.packedRefsTimeout=5000` passed via `-c`, letting git absorb
  ref-lock contention internally without burning a userspace retry
  attempt. Free win on git ≥ 2.31; older versions silently ignore.

Conflating these strategies is a recurring temptation — e.g. trying
to "filelock the IDE" or "retry around ai-hats peers". Don't. Filelock
fails fast for ai-hats vs ai-hats (we want determinism + readable
error); retry absorbs external transient noise (we want the user's
`git commit` not to break our merge).

### П4 — Idempotency at the lifecycle layer, fail-loud at the integrity layer

When a parallel peer completes destructive lifecycle ops (`merge` or
`discard` on the same branch) before this caller acquires the
lifecycle lock, the late arrival no-ops with `logger.info("already
torn down by a peer")` and exits 0. The semantic gate is **worktree
directory existence**: peer's `_remove_worktree` is the irreversible
event, and `worktree_path.exists()` is the cheap, reliable post-lock
check.

But idempotency stops at this layer. The data-integrity gate —
HATS-481 L4' in `state._teardown_worktree` — **re-raises** any merge
failure (except `OriginalBranchMissingError`). `task transition <id>
done` then aborts before persisting the DONE state; the task stays
in `review` and the user re-runs after resolving contention. This
prevents the "GitHub Merge Queue April-2026" silent-loss class (DONE
without verified merge). Fail-loud above, idempotency below — never
the other way around.

### П5 — All lock files under `<ai_hats_dir>`

Every worktree-subsystem lock lives under
`<ai_hats_dir>/sessions/worktrees/`, resolved through
`worktrees_dir(project_dir)` → `ai_hats_dir()` (paths.py:79).
The path precedence is `AI_HATS_DIR` env > yaml `ai_hats_dir:` >
default `<project>/.agent/ai-hats/`. Tests override via
`AI_HATS_DIR=<tmp_path>` to isolate; users with a custom
`ai_hats_dir:` get locks adjacent to state without split-brain.
No lock file lives outside `<ai_hats_dir>`, and the default
`<project>/.agent/` is git-ignored end-to-end.

## Consequences

* **Bounded lock surface.** For one project: 1 create-lock (constant),
  N base-locks (one per active base ref, typically 1–2), M lifecycle
  + state locks (one pair per active wt branch). Stale lock files on
  disk are harmless — kernel auto-releases the underlying `fcntl`
  lock on process death; the file is just a name.
* **Fail-under-revert tests per layer.** Each ticket landed at least
  one test that flips red when its specific lock is stubbed out
  (TC-N1..N20 in `tests/test_worktree_concurrency.py`). A future
  refactor that "simplifies" the locking model needs to flip these
  tests deliberately.
* **Extension path is open.** HATS-486 (stale-lock detection) plugs
  in as a sidecar — it doesn't add a new lock layer, it adds
  recovery logic over the existing four. The 4-layer model is
  expected to remain stable.
* **One canonical reference.** This ADR + the `worktree.py` module
  docstring are the two places that describe the full picture.
  Plan.md files of the four tickets are kept for historical /
  decision-fork archaeology.

## Alternatives considered

* **Merge-queue daemon (bors / Kodiak / GH Merge Queue style).**
  Overkill: queue gives batching (we have no CI) and fairness
  (FCFS via filelock is sufficient for ≤20 agents). Filelock is
  strictly simpler with identical safety properties.
* **Optimistic CAS via `git push --force-with-lease`.** Works for
  remote push, not for local merge commits — the contention we care
  about is local index/refs.
* **Single repo-wide write-lock.** Degrades parallelism on
  unrelated branches / base refs. Failure to ship cleanly: two
  agents merging into `master` and `develop` would serialize for no
  reason.
* **Reuse `<state>.json.lock` for the lifecycle layer.** Cascading
  hang: state-lock is held for microseconds (JSON I/O); lifecycle
  for seconds (fetch + merge + remove). Mixing the hold-time
  budgets makes `wt list` / `load_for_branch` on peers block
  through entire merges.
* **Open-ended retry instead of fail-loud L4'.** Would have
  re-introduced the silent-loss class (HATS-481 motivation): a
  retry that eventually succeeds is invisible to the operator,
  while a retry that eventually times out and silently marks DONE
  is the exact GitHub Merge Queue 2026 incident pattern.

## References

- HATS-477 — epic that bundled the concurrency-hardening work.
- HATS-476 — audit report enumerating R-01..R-08 + B-01..B-08;
  see `tracker/backlog/tasks/HATS-476/plan.md`.
- HATS-121 — state-JSON filelock (foundation).
- HATS-479 — create-time concurrency (L1+L2+L3+L4).
- HATS-480 — lifecycle lock (R-03 merge↔discard race).
- HATS-481 — base-branch merge lock + L4' fail-loud teardown.
- HATS-486 — stale-lock detection (deferred follow-up).
- `src/ai_hats/worktree.py` module docstring — canonical
  in-code reference; mirror this ADR's П2 hierarchy.
- `src/ai_hats/state.py:_teardown_worktree` — site of L4' re-raise
  (data-integrity gate).
- `tests/test_worktree_concurrency.py` — TC-N1..N20 fail-under-revert
  matrix.
- `docs/how-to-advanced.md` §2.7 — user-facing summary of what
  serializes vs what runs in parallel.
