# Changelog

All notable changes to `ai-hats-observe` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- New session directories use mode `0700`, and sensitive session artifacts use
  mode `0600` across creation, append, copy, and atomic-replacement paths. The
  invariant is provider-neutral and does not rewrite existing sessions.
  (HATS-1688)

## [0.6.0]

`session_env(session_id, trace_path)` — the session-scoped environment variables
without a live `Session`. `Session.get_env()` now returns it, so the keys have one
spelling; `--dry-run` reports the child's environment before a session exists and
must not carry a second copy of them (HATS-1548).

Minor, not patch: a new public function, additive for every existing caller.

Note: no `[0.5.0]` section exists — that release shipped without a changelog
entry, and this one does not invent its contents in hindsight.

## [0.4.0]

Cross-process unique session ids. Until now the id was a UTC second plus a
per-process counter, so two runs starting in the same second keyed the same
session dir and silently merged — the defect behind the plugin-dir shredding
patched surgically in HATS-604.

### Fixed

- `SessionManager.create_session` mints ids unique across processes. The id was
  a UTC second plus a per-process counter, so two runs starting in the same
  second produced the same id and silently shared one session dir — interleaved
  `trace.log`, last-writer-wins `metrics.json` / `audit.md`. The id now carries
  the pid, and the counter is process-wide so several managers in one process
  cannot collide either. Shape: `<YYYYMMDD-HHMMSS>-<counter>-<pid>`; consumers
  parsing the timestamp prefix are unaffected. (HATS-1248)
- `session list --json` emits `started_at`. It was gated on a
  `YYYYMMDDTHHMMSSZ_` id shape this package has never minted, so the field was
  absent for every real session. (HATS-1248)

### Added

- `ai_hats_observe.artifacts.session_start_dt` — the one parse of a session id's
  timestamp prefix, so the uniqueness suffix stays free to change. (HATS-1248)

## [0.3.0]

`usage/v1` behind the transcript-parser adapter (ADR-0014 Phase 1, T15/0.3.0).
The context-cost usage report is folded into the package as a parser output, so
a surface's parser produces its own report rather than the writer/step hardcoding
Claude JSONL — the last Claude-JSONL assumption living outside the parsers.

### Added

- `ai_hats_observe.usage` — the pure `parse_session_usage` producer of the
  `usage/v1` report (measured always-on, ordered timeline, aggregates, sidechain
  linkage), plus `empty_usage_report` / `SCHEMA_VERSION`. Runnable standalone as
  `python -m ai_hats_observe.usage <transcript.jsonl>`.
- `TranscriptParser.parse_usage(jsonl_path, trace_path)` — a second parse method
  on the adapter: `ClaudeParser` builds the measured report from JSONL (else the
  trace fallback); `TraceParser` returns a well-formed empty report (the trace
  surface carries no token telemetry). Standalone drive proven on a bare tmp dir
  (`test_usage_standalone.py`).

## [0.2.0]

Standalone session-browse CLI (ADR-0014 Phase 1, T15/0.2.0). The `ai-hats session`
browse commands are lifted out of the integrator into the package, so a third
party can browse recorded sessions with only `ai-hats-observe` installed.

### Added

- `ai_hats_observe.cli` — the `session` Click group with `list` / `show` /
  `audit`, plus a `_seam` of injectable resolvers (`_PROJECT_DIR`, `_RUNS_DIR`,
  `_TAG_FILTER_PARSER`, `_CONSOLE`) defaulting to worktree-free, project-local
  values (`<project>/.agent/sessions/runs`). `_PROJECT_DIR` delegates to the
  shared `ai_hats_core.paths.default_project_dir` (same primitive the tracker CLI
  uses). Standalone drive proven on a bare tmp dir
  (`test_session_cli_standalone.py`). `click` / `rich` join the deps.
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
