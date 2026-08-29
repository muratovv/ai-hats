# Changelog — ai-hats-core

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions adhere to [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ai_hats_core.lazy.lazy_facade` — the `__getattr__`/`__dir__` pair for a package
  that binds its exports on first use. Written by hand in six `__init__` first
  (HATS-1869), which grew two conventions for the same table and a `__dir__`
  missing from four of them.

### Changed

- The facade binds its exports lazily (PEP 562) — the dependency set is
  unchanged, only the moment it loads. Importing `ai_hats_core.deadline`, which
  is stdlib-only and sits on the hook path of every tool call, no longer costs
  pydantic, filelock and asyncio through the parent `__init__` (HATS-1869; see
  ADR-0014 Amendments). A bare `import ai_hats_core` no longer exposes the submodules
  the facade used to import for it (`locks`, `yaml_model`, …) as attributes;
  `from ai_hats_core import locks` is unaffected, as is the documented API —
  `__all__` plus `safe_delete`.

## [0.9.0] - 2026-08-14

### Added

- `ResolvedCheck.source_path` — where `script_path` pointed before a session
  re-based it onto that session's frozen mirror; `None` under live resolution.
  Only the rebaser ever sees both paths, and without keeping one a refusal
  cannot tell whether the bytes that refused are still the bytes the library
  ships — the gap that reported a green gate as "no green marker" for a whole
  session (HATS-1651).

## [0.6.1] - 2026-08-02

### Added

- `ResolvedCheck` and `ResolvedComponent` — check binding value-types for loud
  composition-time validation (HATS-1140). Version bumped so a remote-channel /
  e2e skew install resolves the local/published wheel carrying these symbols.

## [0.5.0] - 2026-07-07

### Added

- `ai_hats_core.paths.default_project_dir` — the worktree-free project-root
  resolver (walk up for `.agent`/`.git`), shared by the tracker and observe
  standalone CLIs so the walk-up is no longer copied per package (HATS-952). The
  integrator keeps its richer wt-coupled `_project_dir` (linked-worktree hop).
- `ai_hats_core.recovery` — the `RecoveryProtocol` (`run()` contract) +
  `NoOpRecovery` (the package-pure default), promoted from
  `ai_hats.environment_recovery` (HATS-948, T15). A domain-agnostic DI seam so
  `ai_hats_observe`'s `SessionManager` defaults to a no-op recovery; the heavy
  `EnvironmentRecovery` stays in the integrator and is injected at the run-path
  seam. `ai_hats.environment_recovery` re-exports the pair; consumer floor
  `>=0.5.0`. (Session/trace vocab stays in `ai_hats_observe`, NOT core — each
  module owns its schema.)

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
