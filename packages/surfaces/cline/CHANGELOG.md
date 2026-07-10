# Changelog

All notable changes to `ai-hats-cline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `get_env` now sets `CLINE_HUB_PORT` to a per-session ephemeral port, moving
  each ai-hats cline session off the default hub port (25463) so parallel
  sessions and `cline --help` no longer crash with `EADDRINUSE` (HATS-973).

## [0.3.0]

`ClineParser` (Adapter B, HATS-960) + TS plugin hooks (HATS-964).

### Added

- `ensure_runtime_hooks` override — materializes a TS plugin wrapper
  (`ai-hats-hooks.ts`) + hook index (`ai-hats-hooks.json`) into
  `<project>/.cline/plugins/` (HATS-964). `CLINE_HOOKS_DIR` env +
  `--hooks-dir` CLI flag point cline at the directory so the plugin loads
  (cline v3.0.3 does NOT auto-discover `.cline/plugins/`). The plugin's
  `beforeTool` hook bridges cline's AgentPlugin lifecycle to ai-hats's
  existing bash guard (`pre_bash_shared_state_guard.sh`), translating
  `context.input` → `{"tool_input":{"command":...}}` and blocking on
  non-zero exit (explicit fail_closed). `.cline/plugins/` auto-gitignored.
- `get_env` now sets `AI_HATS_DIR` + `AI_HATS_PROJECT_DIR` +
  `CLINE_HOOKS_DIR` (path to the materialized plugins) so the plugin can
  locate the guard scripts at runtime and cline scans the directory at
  session start.
- `ai_hats_cline.ClineParser` — a `TranscriptParser` that normalizes cline's
  single-object `<id>.messages.json` into observe's `ParsedTranscript` (turns +
  `model_stats` + `agg_usage`) and a full `usage/v1` report (always-on proxy,
  reconstructed-attribution timeline, tool aggregates). Wired via
  `ClineProvider.transcript_parser()` (HATS-948), replacing the default
  trace-only parse.

### Changed

- Depends on `ai-hats-observe>=0.3.0` (the `TranscriptParser` base + `usage/v1`
  schema `ClineParser` reuses) — the MVP omitted it.
||||||| parent of 0a9be1e (fix(cline): wire CLINE_HOOKS_DIR + --hooks-dir for plugin loading (HATS-964))
- `get_env` now sets `AI_HATS_DIR` + `AI_HATS_PROJECT_DIR` so the plugin
  can locate the materialized guard scripts at runtime.
=======
- `get_env` now sets `AI_HATS_DIR` + `AI_HATS_PROJECT_DIR` +
  `CLINE_HOOKS_DIR` (path to the materialized plugins) so the plugin can
  locate the guard scripts at runtime and cline scans the directory at
  session start.
>>>>>>> 0a9be1e (fix(cline): wire CLINE_HOOKS_DIR + --hooks-dir for plugin loading (HATS-964))

## [0.2.0]

Native skill materialization into `.cline/skills/` (HATS-963).

### Added

- `materialize_runtime_skills` override — copies the composed role's skills
  into `<project>/.cline/skills/` (cline's native discovery path, HATS-963).
  `/skills` in the TUI now shows the role's skills; `/skill-name` loads bodies.
  Idempotent; user-authored skills preserved via `.ai-hats-managed` marker.
  `filelock` guards the wipe-and-rebuild against concurrent sessions.
- `.cline/skills/` auto-added to project `.gitignore` (materialized mirror).

### Changed

- `include_skills=True` kept as safe fallback — flip to `False` gated on a
  live smoke proving `/skills` works in the TUI (plan R7 kill criteria).

## [0.1.0]

First cut of the Cline surface plugin (HATS-956) — the first in-tree consumer of
the provider IoC seam (HATS-870). Registers `cline` as an ai-hats provider via
the `ai_hats.providers` entry point, with zero edits to `src/ai_hats/**`.

### Added

- `ai_hats_cline.ClineProvider` — the `Provider` adapter for the `cline` CLI:
  inline-`-s` role delivery, interactive TUI for HITL (`cline -i`), headless
  `--yolo --json` for the automate path, no `--worktree`, ambient
  `CLINE_DATA_DIR` (keeps the machine's cline auth). Registered under the
  `ai_hats.providers` entry point as `cline`.
