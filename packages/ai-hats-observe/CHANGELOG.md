# Changelog

All notable changes to `ai-hats-observe` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [0.14.0]

### Added

- `Session.startup_diagnostics` — what the injected recovery reported at
  `create_session`, as `ai_hats_core.diagnostics.Diagnostic` values; `()` under
  the no-op default. The runner decides the channel. Requires `ai-hats-core`
  0.14.0.

## [0.13.0]

Additive to `events/v1`: new `raw_code` values and one more producer of an
existing kind.

### Added

- The Claude transcript reader reads `attachment` records: a hook that exited
  non-zero (`hook_non_blocking_error`) or timed out (`hook_cancelled`) is a
  `Notice(surface_warning)` naming the hook, the command and the exit code or
  timeout — a gate that ran and delivered no verdict, recorded nowhere before.
  The other 29 measured subtypes are named silent with their reason; an
  unmeasured one is `Notice(unsupported_record, raw_code: attachment/<subtype>)`
  instead of the silence `attachment` as a whole used to get.
- A `fallback` content block — the API rerouting one call — is
  `Notice(model_switched)` naming the model that took over, no longer drift.

### Changed

- The top-level record types the reader keeps silent are a named set with the
  reason per group (`_SILENT_RECORD_TYPES`); `KNOWN_RECORD_TYPES` is unchanged
  as a name and as a set.
- `APPROACHING_LIMIT` no longer claims a live stream is its only producer: a
  surface's own status line reports it too (claude, HITL, in `ai-hats`).

### Fixed

- A record carrying a raw U+2028 inside a string was cut in two: the reader
  split on `str.splitlines`, which breaks on it, and reported both halves as
  `malformed-json` — 94 notices for zero malformed records over the measured
  corpus, and the six records' content lost. Only a newline ends a record.

## [0.12.0]

### Added

- `composition_names(record)` / `CompositionNames`: the one reader of a
  session's composition record (`metrics.json["composition"]`,
  `role_materialization.json`) — traits off the trace's remaining terms, rules
  off the prompt members named `rules::`, skills off the record's skills, each
  name paired with what brought it (a composite, `overrides::global`,
  `overrides::project`, or `expression` for a member the trace does not
  attribute). `audit.md`'s `## Composition` section renders through it, and so
  does the integrator's session-reviewer prompt: two consumers, one shape.
- `snapshot_names(snapshot)`: the reader of the older snapshot (`traits` /
  `rules` / `skills` lists with a `provenance` layer map) that sessions before
  2026-09-16 hold on disk, kept apart from the record's reader.
  `stored_composition_names(composition)` picks the reader by shape, for what
  comes back off disk — an audit rebuild (`session backfill`), a review of an
  archived session.

### Changed

- `init_audit(composition=…)` takes the plan's record only; a live session has
  not written the snapshot since 2026-09-16. The snapshot is read, never
  written: `AuditWriter` re-emits an archived session's section through
  `stored_composition_names`.

## [0.11.0]

Everything here is additive to `events/v1`: new kinds and new optional fields
with `None` defaults, which an older decoder skips or ignores by contract.

### Added

- `RunStarted` / `RunEnded`: the first and last lines of `events.jsonl`, said
  by `EventLogWriter` itself at `start()` and `close(exit_code=…)` — the one
  thing a file that stopped growing cannot tell a follower. The ending is
  written after a fault too, carrying it in `detail`.
- `PersonAsked` (`kind`: `question` | `permission`): the run is waiting on a
  person. Opened by the Claude transcript reader on a question tool
  (`AskUserQuestion`), by `ai_hats.surfaces.gate_log` beside a `GateVerdict`
  whose decision is `ask`, and by the Claude channel when the surface shows
  its own permission prompt. No closing twin: the wait is open while the call
  it names has no `ToolResultReceived`.
- `PromptReceived.origin` (`person` | `harness`), from the transcript's own
  provenance fields; absent when the surface did not say.
- `agent` on every event: the sub-agent that produced it, absent for the main
  agent. `EventSource(path, agent)` for the writer's `locate`, which stamps
  events per source; `Surface.event_sources()` names a session's records,
  and Claude's adds `<sid>/subagents/agent-*.jsonl`.
- `WorthRecording.INTERRUPTED`: a person stopped the turn. Both Claude readers
  emit it for the harness's `[Request interrupted by user…]` marker, which is
  no longer a `PromptReceived`; the response it cut ends `CANCELLED` unless
  the model had already reported a stop.
- `GateVerdict(before_tool, deny)` from the Claude transcript when the auto-mode
  classifier or the person refused a tool: `hook` names the decider, `source`
  is `claude/jsonl`.
- `canonical.now()`: this instant in the form every surface's stamps take.

### Changed

- `EventLogWriter.close()` takes `exit_code`; `events_written` counts the two
  lifecycle lines; the file exists from `start()` (before: only once a source
  had produced an event).
- `parsers.claude_events.INTERRUPT_MARKERS` is public, shared with the SDK
  reader.

## [0.10.0]

### Added

- `EventLogWriter` (`ai_hats_observe.event_log_writer`): the session-time writer
  of `events.jsonl`. Follows a surface's record through its `EventReader` on a
  thread and appends each event as it appears; `close()` adopts a source that
  appeared late, tells every reader the run is over and drains what a live
  reader held back. The `EventReader` protocol gains `close()` for that.
- `GateVerdict` in the canonical events, with `GatePoint` and `GateDecision`:
  what a gate said about a call or a stop, named after the moment rather than
  any surface's hook vocabulary. Additive to `events/v1` — an older decoder
  skips the line.
- `event_log.append_event`: one event onto the end of a file, the call a
  producer outside the session's own writer makes.
- `ClaudeTranscriptReader` reads `system/stop_hook_summary` as a `GateVerdict`
  at the stop, plus a `SURFACE_WARNING` per hook that failed; it was silenced
  as bookkeeping.

### Changed

- `write_events` writes each line with one `write(2)` on an `O_APPEND`
  descriptor and creates the file private (`0o600`), so two producers appending
  at once never interleave and the artifact is never world-readable, even
  briefly. `SESSION_FILE_MODE` and the private opener moved to
  `ai_hats_observe.artifacts` (still importable from `session`).

## [0.9.0]

A session is read as a stream of canonical events, and the usage report is built
on it. `ai_hats_observe.canonical` carries two axes: content (a `Response`
identified by the call, its items, and how it completed) and run health
(`PersonActionRequired` / `HarnessActionRequired` / `Notice`, named after who
must act rather than after a provider's error vocabulary). Surface adapters
implement `EventReader`; `ClaudeTranscriptReader` is the first.

### Fixed

- A call's token cost is counted once however many transcript records carried
  it. The previous reader summed a per-record `usage` that repeats byte-identical
  across the fragments of one API call, inflating every total — measured 2.48x
  over the 40 largest real transcripts. This moves the numbers in `usage.json`,
  `metrics.json` and the audit header; existing reports are not migrated.
- Every text fragment of a turn reaches the audit. The previous reader
  overwrote, keeping only the last, which discarded intermediate reasoning in
  45.3% of turns.
- A tool's outcome reaches the audit, success or failure. Tool results were
  dropped wholesale, so a failed tool call was invisible.
- An API-error notice can no longer be rendered as the agent's answer.
- Thinking is retained as text instead of `len(text) // 200` reported as
  seconds.

### Added

- `ai_hats_observe.parsers.claude.ClaudeParser.from_events` — the parsed shape
  from any stream of canonical events, so a replayed event log produces the same
  audit as the transcript it came from (identical over 300 real transcripts).
- `usage/v2`: `api_calls` (inference calls, which is what cost is proportional
  to) and `signals` (the run-health axis). `flags` keeps its meaning — parse
  quality only. A `usage/v1` report on disk still renders.
- `ai_hats_observe.event_log` — a session's events as one JSON object per line,
  written append-first so a reader tolerates a file still being written.
- `ParsedTranscript.responses` and `.signals`, both defaulting empty, so a
  surface adopts them when its own parser is ready.

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
