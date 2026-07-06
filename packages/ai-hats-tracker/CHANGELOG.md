# Changelog

All notable changes to `ai-hats-tracker` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

The standalone backlog CLI. The `task` and `attach` Click groups move into the
package and run worktree-free; `ai-hats-wt` becomes an optional extra.

### Added

- `ai_hats_tracker.cli` — the `task` (create / list / show / transition / log /
  link / plan-extract / update / close / sync) and `attach` Click groups, driven
  by the wt-free `_seam` factory defaults. Standalone consumability proven by
  `test_cli_standalone.py`.
- `[project.optional-dependencies]` `wt` extra (`ai-hats-wt`) — install it to
  enable the worktree UX; the CLI runs wt-free without it.
- `click` / `rich` runtime dependencies (the backlog CLI).

### Changed

- `ai_hats_wt` is now soft-imported (optional) rather than a hard dependency —
  the backlog CLI is absent-tolerant. The ai-hats integrator injects its
  wt-wired manager / project-dir / guard over the `_seam` defaults at mount.

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
