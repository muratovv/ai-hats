# Changelog

All notable changes to `ai-hats-wt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`WorktreeManager.probe_blockers()` and `Blocker`** (HATS-1654) — every
  refusal `merge()` would raise right now, in its own order, as a read-only
  pass: rebased branch, consent, wandered HEAD, dirty tree, drift. No lock and
  no mutation; the one cost is the drift check's bounded `git fetch`. The
  `wt:pre-merge` point is not probed — a check is an arbitrary command with no
  "would you refuse?" mode. Callers use it to name every blocker in one run
  instead of one per run.

## [0.5.0]

### Added

- **`WorktreeLifecycle.before_merge` and `WorktreeMergeAborted`** (HATS-1540) —
  the `wt:pre-merge` extension point. Fires inside `merge()` after `_check_clean`
  / `_check_drift` / consent and before every mutation, so a veto leaves the base
  branch, the worktree and the branch exactly as they were and the retry is in
  place. Deliberately not the teardown veto (ADR-0012 / HATS-775 rejected that as
  a gate: it fires after the merge commit exists and can only strand a worktree).
- **`WorktreeManager.peek_worktree_path`** — the recorded worktree for a task as
  a **pure read**. `load_for_task` unlinks a state file whose worktree is gone,
  which makes merely *naming* a tree mutate lifecycle state; a caller that only
  wants the path (a gate's env) must not do that. `FileNotFoundError` answers
  `None`; every other error propagates, because "cannot read the record" is not
  "this card has no worktree".

### Changed

- **BREAKING for out-of-tree lifecycle bundles.** `WorktreeLifecycle` is a
  structural Protocol, so a bundle written against the two-method shape now
  raises `AttributeError` mid-`merge()`. This is deliberate — a tolerant
  `getattr` would mean a gate that silently does not fire. Add a no-op
  `before_merge(self, ctx) -> None` to any custom bundle.
- `cleanup(IsolationMode.SQUASH)` publishes to the base branch and does **not**
  fire `before_merge` — a recorded decision, not an omission (`cleanup`
  suppresses lifecycle vetoes by design, ADR-0013 D8, so a refusal there would be
  swallowed). Pinned by `test_the_squash_cleanup_path_does_not_fire_the_point`.

## [0.4.2]

### Fixed

- **A rebased branch is no longer refused as drifted** (HATS-1307). `_check_drift`
  asked only whether the base SHA snapshotted at `wt create` still matched the
  current base, so a branch sitting exactly on the moved base — zero stale work —
  was still refused. The only exit was `--accept-drift`, a flag the
  `rack transition <id> done` path cannot pass, so auto-merge dead-ended the
  moment the base moved. Drift now requires **both** terms: the base moved since
  create **and** it is not an ancestor of the worktree branch. Real drift is
  refused exactly as before, and the fork shape (`base_branch` != `merge_target`,
  HATS-942) — whose target is never an ancestor of the branch by design — keeps
  merging cleanly.

### Changed

- **`WorktreeDriftError` carries `branch_name` / `base_branch` / `worktree_path`**
  (HATS-1307) so CLI handlers can print a concrete rebase recipe. The body stays
  facts-only per the HATS-509 contract.
- **The drift summary's path list is computed from the merge-base** (three-dot
  diff, HATS-1307), so it lists only what the base added — the two-dot form also
  reported the branch's own work as a reversed change.

## [0.4.1]

### Fixed

- **`list_active` drops worktrees git no longer backs** (HATS-1205). It
  documented "Prunes stale entries" but only dropped entries whose state JSON
  had vanished, so a directory surviving without its `.git` link file (or one
  `git worktree prune` disowned) stayed "active" forever and kept padding the
  selector-ambiguity list callers build from it. Liveness is the presence of the
  `.git` link — local, no subprocess. The directory is untouched; only the claim
  is dropped.

## [0.4.0]

### Changed

- **Merge is supervisor-gated** (HATS-1019): `WorktreeManager.merge` refuses
  without `AI_HATS_MERGE_ACK=1` in env, raising the new typed
  `WorktreeMergeConsentError`. The HATS-596 already-merged short-circuit stays
  consent-free (cleanup publishes nothing). Neither `force` nor `accept_drift`
  bypasses the gate.

## [0.3.1]

### Fixed

- `Worktree.reclaim_if_clean` (HATS-979): discard a worktree only when it
  carries no unmerged work — kept on a dirty tree, own commits not in the
  canonical base, or a caller-supplied `has_extra_hold` predicate (the seam for
  gitignored state such as pending hunk review).

## [0.3.0]

### Added

- Configurable base / merge-target: `get_default_base_branch` /
  `get_default_merge_branch` public API (HATS-942).

## [0.2.1]

### Added

- `workspace_pythonpath` helper and named layout constants (HATS-913).

### Changed

- The internal `_scrubbed_git_env` copy is gone; the engine imports
  `scrubbed_git_env` from `ai-hats-core` (now pinned `>=0.2.0`) (HATS-909).

## [0.2.0]

### Changed

- The worktree carry schema moved into `ai_hats_wt`, with match-based carry row
  parsing and a flat collision check (HATS-863).

## [0.1.0]

Initial standalone release: the hook-agnostic git-worktree engine extracted from
ai-hats.

### Added

- `WorktreeManager` — create / merge / discard linked git worktrees, with the
  static git probes and a layered file-locking concurrency model.
- The `WorktreeLifecycle` extension-point (`NOOP_LIFECYCLE` default) and the
  typed exception seam (`ai_hats_wt.__all__`).
- Standalone operation on a bare git repo (no ai-hats config), with a
  project-local `.wt` state directory.
