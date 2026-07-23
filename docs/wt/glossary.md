# Worktree (wt) Glossary

Glossary of core terms and concepts for the `ai-hats-wt` git-worktree engine and worktree integration in `ai-hats`.

---

## Canonical base branch

The name (or names) of the branch that worktrees are expected to be created from and merged back into. Defaults to `master` and `main`, in that priority order (HATS-518); the first one that actually exists in the repo is the comparison target. A project can override the split (base ≠ merge-target) via the `worktree` block in `ai-hats.yaml` — see [How to configure › the `worktree` block](../how-to-configure.md#1a-the-worktree-block--fork-workflows-base--merge-target) (HATS-942).

- **Why it matters.** `WorktreeManager.create()` captures whatever branch the main repo's HEAD currently points at as the worktree's `_original_branch` — and that is the branch `ai-hats wt merge` later lands commits on. Two silent-wrong-branch failure modes:
  - *Create-time.* Operator parks the main repo on a feature branch before `wt create` / `task transition <ID> execute`. The worktree quietly inherits that branch as its merge target; CLI reports "merged" while master never sees the work.
  - *Merge-time.* Even when create-time HEAD was on a canonical base, the main-repo HEAD can wander off `_original_branch` between create and merge — manual `git checkout`, IDE branch-switch, a peer agent operating directly in the main repo without a linked worktree. `_fast_forward_merge` / `_squash_merge` run `git merge` in the main-repo cwd, so the merge lands on whatever branch is currently checked out — not on `_original_branch`.
- **Two refusal gates.**
  - *Create-time refusal* (HATS-518) — no worktree exists yet; the refusal aborts before `git worktree add` runs. Retry creates the worktree fresh once HEAD is on a canonical base.
  - *Merge-time refusal* (HATS-533) — the worktree dir and worktree branch are preserved untouched; the refusal happens before `_check_clean` / `_check_drift` / the actual `git merge`. Retry from the corrected HEAD finishes the merge as if the refusal hadn't happened.
- **`--force` / `--accept-drift` do NOT bypass either guard.** `--force` is the dirty-worktree consent; `--accept-drift` is the moved-base consent. Neither addresses wrong-branch protection — three independent safety contracts, three independent flags.
- **Configurable per project (HATS-942).** The default is the `master`/`main` two-name set, but a fork/dogfood repo can point the split (base ≠ merge-target) at its own branches via the `worktree` block — full contract in [How to configure](../how-to-configure.md#1a-the-worktree-block--fork-workflows-base--merge-target). When unset, behavior is byte-identical to the historical hardcoded set.

## Worktree data transfer (carry-in / carry-out)

The mechanism by which **gitignored** data crosses the `ai-hats wt` boundary in either direction — **carry-in** (seeded *into* a worktree at `wt create`) or **carry-out** (harvested *out of* a worktree before `wt merge` / `wt discard` teardown). The primitive is a **worktree lifecycle hook** — a component-declared script run at `wt_in` (after create) or `wt_out` (before every teardown route, fail-closed). Shipped today is the `wt_in` / `wt_out` hook form (HATS-823); the declarative `seed_in` / `harvest_out` path-list sugar (designed in ADR-0012) is **shelved** — HATS-775 cancelled, no confirmed consumer the primitive does not already serve.

See [how-to-extend → Worktree lifecycle hooks](../how-to-extend.md#worktree-lifecycle-hooks) for the author contract (declaration, lifecycle, `.env` / secrets) and [ADR-0012](../adr/0012-worktree-data-transfer.md) for the design (hooks-first, resolution map, creds boundary D5).

## wt core / extraction boundary

The in-tree module boundary that splits the git-worktree **engine** (create / merge / discard / cleanup, the L1–L4 locks, drift / base-branch guards, git plumbing) from the **ai-hats accretions** layered on top (composition, the lifecycle-hook layer, FSM auto-create/auto-merge, the tracker redirect, error-recipe translation). The engine is **hook-agnostic**: it owns *where* the lifecycle points fire and *that a callback raising aborts teardown* (fail-closed); it does **not** know what runs there. ai-hats plugs behavior in through a **lifecycle extension-point** — a callback bundle (`on_created` / `before_teardown(event, ctx)`, default no-op) injected at construction, so the whole worktree-data-transfer hook layer (ADR-0012) stays an accretion. The boundary is **one-directional**: `ai_hats → ai_hats_wt`, never back, enforced by the package-boundary import-lint (`packages/ai-hats-wt/tests/test_boundary.py`, HATS-882).

## needs_worktree effect

The seam that keeps the task FSM (tracker) free of any worktree dependency (ADR-0014 P0 #3, HATS-866). `TaskManager` never imports `ai_hats_wt`; instead it emits worktree side-effects through an injected handler typed by the **`WorktreeEffects`** protocol (`state.py`, primitives-only signatures): `setup` on `→ execute` (returns the worktree path; logged to the card's work_log as `Worktree: <path>`), `teardown` on `→ done` / `failed` / `cancelled` (returns a past-tense outcome — `merged` / `discarded` — logged as `Worktree <outcome>`), and `assert_canonical_base` for the forced-execute guard. The wt-backed implementation is **`WtWorktreeEffects`** (`wt_effects.py`, integrator-side); the CLI injects it at its `_task_manager` chokepoint. No handler → pure FSM: transitions work, no worktree is created or torn down. Handler exceptions propagate inside the transition's lock window, so a failed merge still aborts before the DONE state persists (HATS-481).

## Layered file-locking (L1–L4 locks)

The 4-tier concurrency control model that prevents race conditions and repository corruption during parallel worktree operations:
- **L1 (State Lock):** Per-worktree lock (`<project>/.wt/state/<id>.lock`) protecting state metadata mutations.
- **L2 (Manager Lock):** Global process-level lock (`<project>/.wt/manager.lock`) serializing worktree creation and removal.
- **L3 (Git Index Retries):** Retries around `.git/index.lock` contention during git operations.
- **L4 (Ref Lock Waits):** Retries around `.git/refs/heads/<branch>.lock` contention during branch updates.

See [ADR-0006](../adr/0006-worktree-concurrency-layered-defense.md) for full concurrency design.

## IsolationMode

Enum defining the lifecycle and merge strategy for a linked worktree:
- `BRANCH` — Create a linked worktree on a dedicated branch, fast-forward merge on completion.
- `SQUASH` — Create a linked worktree on a dedicated branch, squash merge into target branch on completion.
- `DISCARD` — Create a temporary linked worktree, discard all changes and branch on teardown.
- `NONE` — Execute directly in the main repository checkout without worktree isolation.
