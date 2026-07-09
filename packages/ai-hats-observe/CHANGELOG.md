# Changelog

All notable changes to `ai-hats-observe` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

Standalone session-browse CLI (ADR-0014 Phase 1, T15/0.2.0). The `ai-hats session`
browse commands are lifted out of the integrator into the package, so a third
party can browse recorded sessions with only `ai-hats-observe` installed.

### Added

- `ai_hats_observe.cli` — the `session` Click group with `list` / `show` /
  `audit`, plus a `_seam` of injectable resolvers (`_PROJECT_DIR`, `_RUNS_DIR`,
  `_TAG_FILTER_PARSER`, `_CONSOLE`) defaulting to worktree-free, project-local
  values (`<project>/.agent/sessions/runs`). Standalone drive proven on a bare
  tmp dir (`test_session_cli_standalone.py`). `click` / `rich` join the deps.
- The ai-hats integrator overrides the `_seam` resolvers with its
  AI_HATS_DIR/yaml-aware versions at mount and re-attaches the retro subcommands
  (`retro` / `retro-validate`, downstream consumers that stay integrator-side).

## [0.1.0]

Observe core (ADR-0014 Phase 1, T15). The session/trace/audit domain is extracted
from the `ai-hats` integrator into a standalone, core-only package: session
lifecycle + writer, a versioned trace/audit schema with a migration seam, and a
surface-agnostic `AuditWriter` fed by a pluggable `TranscriptParser` adapter.

### Added

- `ai_hats_observe` — `SessionManager`, `Session`, `SidecarTracer`, `AuditWriter`,
  `TraceEntry`, `Turn`, re-exported from `__init__`. Standalone drive proven on a
  bare tmp dir with `recovery=None` (`test_observe_standalone.py`).
- `ai_hats_observe.parsers` — the `TranscriptParser` protocol + `ParsedTranscript`
  result, and the built-in `ClaudeParser` (structured JSONL + trace-chrome
  fallback). `AuditWriter` is surface-agnostic: it holds no provider parsing.
- A `schema_version` on the trace/audit metrics schema (first versioned surface)
  and an initially-empty migration seam (`OBSERVE_MIGRATIONS`).

### Changed

- Session logging no longer eager-imports environment recovery: `SessionManager`
  defaults to `recovery=None` (a pure no-op); the ai-hats integrator injects
  `EnvironmentRecovery` at the compose/CLI seam.
