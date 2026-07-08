# ai-hats-observe

A standalone **session-logging engine**: open a session on a bare directory,
append trace entries, write an incremental audit, and finalize a versioned
`metrics.json` — with no configuration, no worktree engine, and no
`ai-hats.yaml`.

`ai-hats-observe` is the observability core extracted from the
[ai-hats](https://github.com/muratovv/ai-hats) framework (ADR-0014 Phase 1, T15).
It has no dependency on the `ai-hats` integrator: everything below runs against a
plain directory. Its only runtime dependency is
[`ai-hats-core`](https://pypi.org/project/ai-hats-core/) (dependency-free
filesystem primitives + the migration seam).

## What it owns

- **Session lifecycle** — `SessionManager` / `Session`: allocate a session
  directory, resolve its artifact paths, list/filter past sessions.
- **Trace + audit writer** — `log_trace`, `init_audit`, `append_audit`,
  `finalize_audit`; the enriched `AuditWriter` that turns a transcript into
  `audit.md` + a versioned `metrics.json`.
- **Versioned trace/audit schema** — a `schema_version` on the metrics surface,
  with a wired (initially empty) migration seam.
- **Surface-agnostic parsing** — a `TranscriptParser` adapter: the `AuditWriter`
  holds no provider-specific parsing; the concrete `ClaudeParser` (structured
  JSONL + trace-chrome fallback) is one implementation. A new surface (Gemini,
  or a future CLI) plugs in its own parser — the writer never changes.

## Extending to a new surface

The parser is carried by the session's provider, not a central registry: a
`Provider` yields its `TranscriptParser`, and the integrator injects it into the
`AuditWriter`. Adding a surface means shipping a parser that satisfies the
`TranscriptParser` protocol and a provider that names it — no edit to
`ai-hats-observe` internals.

## Dependency direction

`ai-hats-observe` imports **only** `ai-hats-core` and the standard library. It
never imports the `ai-hats` integrator; ai-hats imports *from* here. The boundary
is enforced by an AST import-lint (`test_observe_boundary.py`).
