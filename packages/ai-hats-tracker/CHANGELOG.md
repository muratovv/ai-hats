# Changelog

All notable changes to `ai-hats-tracker` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

Initial standalone release: the TaskCard schema and the worktree-free task state
machine extracted from ai-hats.

### Added

- `TaskCard` / `TaskState` / `Attachment` / `WorkLogEntry` — the task-card schema.
- `TaskManager` — the `brainstorm → … → done` task FSM (create / transition /
  close / link / log / STATE.md sync), worktree-free via the injected
  `WorktreeEffects` seam (default `None` = a pure FSM).
- `TrackerPaths` — the injected on-disk layout contract.
- `plan_extract` (`Candidate` / `extract_candidates` / `mark_extracted`) and the
  `linked_context` / `attachments` submodules.
- A core-wired migration seam (`migrations.run_pending`, empty registry).
- Standalone operation on a bare directory (no ai-hats config), proven by
  `test_tracker_standalone.py` and guarded by `test_boundary.py`.
