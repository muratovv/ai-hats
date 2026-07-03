# Changelog — ai-hats-core

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions adhere to [SemVer](https://semver.org/spec/v2.0.0.html).

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
