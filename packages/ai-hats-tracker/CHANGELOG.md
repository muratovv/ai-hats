# Changelog

All notable changes to `ai-hats-tracker` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0]

### Added

- `ai-hats-tracker` console entry (`[project.scripts]`, ADR-0016 / HATS-991).
  `pip install ai-hats-tracker` now puts an `ai-hats-tracker` command on `PATH`
  (a root Click group over the wt-free `task`/`attach`/`hyp`/`proposal` groups,
  plus `--version`). This makes the engine a *provided tool*: a portable skill
  declares `requires.cli: ai-hats-tracker` and probes it with
  `ai-hats-tracker --version`, rather than being co-located inside this package.
  The engine still ships **no** skill and declares **no** `ai_hats.skills`
  entry-point.

## [0.5.1]

### Fixed

- Release task ownership when a task becomes an epic (HATS-977). A task that
  claimed ownership while childless kept the hold after gaining children,
  orphaning it and refusing every later ownership-gated transition for the
  session. Ownership is now released at the epicification event (create and
  update-reparent) and unconditionally on leaving execute / a terminal state.

## [0.5.0]

### Added

- Task-ownership registry (HATS-955): one locked JSON file lets a second agent
  safely reclaim a task left mid-flight. `OwnershipRefused` enforces the
  single-slot invariant and the live-owner reclaim guard; ownership is claimed
  and released across `execute` transitions, with certain-death reclaim.
- `task stop` verb and `task list --reclaimable` (HATS-955).

## [0.4.0]

### Changed

- The wt-free `_seam` project-root default now delegates to the shared
  `ai_hats_core.paths.default_project_dir` (HATS-952) instead of carrying its own
  copy of the `.agent`/`.git` walk-up — the same primitive the observe CLI uses.
  No behaviour change. Core floor rises to `ai-hats-core>=0.5.0`.

## [0.3.0]

Hypotheses and proposals. The `hypothesis/` domain (stores, quorum, intake) and
the `task hyp` / `task proposal` Click groups move into the package; the tracker
package now owns the full backlog domain, still core-only.

### Added

- `ai_hats_tracker.hypothesis` — `HypothesisStore`, `ProposalStore`, the models
  (`Hypothesis`, `Proposal`, `ValidationLogEntry`, `Vote`, …), quorum
  (`autoclose_quorum`, `find_quorum_closures`, `DEFAULT_QUORUM_K`), and intake.
  Re-exported from `ai_hats_tracker.__init__`. Standalone drive proven by
  `test_hypothesis_standalone.py`.
- `ai_hats_tracker.cli.hyp` / `ai_hats_tracker.cli.proposal` — the `task hyp` and
  `task proposal` Click groups, driven by the wt-free `_seam`.
- `_seam._HYPOTHESES_DIR` / `_seam._PROPOSALS_DIR` — path-resolver slots (wt-free
  `.agent`-derived defaults; the integrator overrides them at mount with the
  `AI_HATS_DIR`/yaml-aware `ai_hats.paths` versions).

### Changed

- The tracker `__all__` surface gains the hypotheses/proposals symbols.

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
  `test_tracker_standalone.py` and guarded by `test_tracker_boundary.py`.
