# Changelog — ai-hats-core

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions adhere to [SemVer](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-07-07

### Added

- `ai_hats_core.session_artifacts` — the shared session-dir naming vocabulary
  (artifact filename constants + `session_dirname`/`strip_session_prefix`),
  promoted from `ai_hats.paths` so `ai_hats_observe` (T15) and the integrator
  share one home (ADR-0014 Phase 1, HATS-948). `ai_hats.paths.session_artifacts`
  is now a re-export shim; the consumer floor rises to `>=0.5.0`.
- `ai_hats_core.trace` — `TraceTag` (trace-line tag vocabulary) + `ENV_SESSION_ID`
  (the sidecar/runtime session handshake), promoted from `ai_hats.constants`
  (HATS-948). `ai_hats.constants` re-exports both, unchanged for its consumers.

## [0.4.0] - 2026-07-06

### Added

- `ai_hats_core.migrations` — generic step-gated `Migration[Ctx]` runner
  (`run_pending` / `latest_step`, HATS-868). Consumed by `ai_hats.migrations`.
  Version bumped so a remote-channel install resolves a local/published wheel
  that carries the module: the published `0.3.0` predates it, so the floor pin
  `ai-hats-core>=0.3.0` was resolving a `core` without `.migrations`
  (`ModuleNotFoundError`, HATS-937 — same skew class as HATS-923). Publishing
  0.4.0 to PyPI + raising the consumer pin to `>=0.4.0` closes it.

## [0.3.0] - 2026-07-06

### Added

- `file_lock` context manager + `LockTimeoutError` — read-modify-write file lock
  helper (HATS-526). Consumed by `ai_hats.cli.assembly`. Version bumped so the
  workspace/uv install resolves the local wheel over the stale published `0.2.0`
  that lacked these symbols; publishing 0.3.0 to PyPI + raising the consumer floor
  pin to `>=0.3.0` is a release follow-up (HATS-923).

## [0.2.0] - 2026-07-03

### Added

- Kernel growth (HATS-862, ADR-0014 T2): `scrubbed_git_env` (git-env hygiene),
  `CompositionResult` / `ResolvedComponent` / `ComponentKind` (composition
  value-types), `YamlModel` (pydantic YAML base), `ai_hats_core.safe_delete`
  (trash-bin destructive ops).

### Changed

- Charter: "dependency-free / pure stdlib" → "minimal deps, each load-bearing".
  First sanctioned dependency: `pydantic>=2`.

## [0.1.0] - 2026-07-02

### Added

- Initial release: `atomic_write_text` / `atomic_write_bytes` (HATS-879).
