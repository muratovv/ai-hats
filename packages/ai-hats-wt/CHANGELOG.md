# Changelog

All notable changes to `ai-hats-wt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
