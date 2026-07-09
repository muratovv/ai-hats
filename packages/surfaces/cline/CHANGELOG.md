# Changelog

All notable changes to `ai-hats-cline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

### Added

- `materialize_runtime_skills` override — copies the composed role's skills
  into `<project>/.cline/skills/` (cline's native discovery path, HATS-963).
  `/skills` in the TUI now shows the role's skills; `/skill-name` loads bodies.
  Idempotent; user-authored skills preserved via `.ai-hats-managed` marker.

### Changed

- `build_system_prompt` now uses `include_skills=False` — the native
  `.cline/skills/` registry replaces the text index (~1.5k tok/session saving).

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
