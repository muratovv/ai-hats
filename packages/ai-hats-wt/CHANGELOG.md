# Changelog

All notable changes to `ai-hats-wt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.2]

### Fixed

- **A rebased branch is no longer refused as drifted** (HATS-1307). `_check_drift`
  compared the base SHA snapshotted at `wt create` against the current base, so a
  branch sitting exactly on the moved base — zero stale work — was still refused.
  The only exit was `--accept-drift`, a flag the `rack transition <id> done` path
  cannot pass, so auto-merge dead-ended the moment the base moved. Drift is now
  *containment*: the base (and `origin/<base>`) must be an ancestor of the
  worktree branch. Real drift is refused exactly as before.

### Changed

- **`base_sha_at_create` is retired from the state file** (HATS-1307). It had no
  consumer left; state files still carrying it load unchanged. Its removal also
  ends the silent no-op guard for state files that lacked it.
- **`WorktreeDriftError` carries `branch_name` / `base_branch` / `worktree_path`**
  (HATS-1307) so CLI handlers can print a concrete rebase recipe. The body stays
  facts-only per the HATS-509 contract.

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
