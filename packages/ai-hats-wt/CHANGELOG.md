# Changelog

All notable changes to `ai-hats-wt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
