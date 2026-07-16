# Changelog — ai-hats-library

All notable changes to this package are documented here. Versioning is semantic,
on the library **format schema** (see README § Versioning).

## 0.2.0

- The shipped **`backlog-manager`** skill now declares its tool dependency in
  `SKILL.md` frontmatter: `ai_hats.requires.cli: ai-hats-tracker` (with a
  `check`/`hint`), per ADR-0016 / HATS-991. The skill stays portable content in
  the library content layer; it is **not** co-located inside `ai-hats-tracker`.
  Declaration only — the verify-and-warn verifier lands separately (HATS-992).
  Coupled with `ai-hats-tracker>=0.6.0`, which exposes the `ai-hats-tracker`
  console entry the `requires.cli.check` probes. Format `schema_version` is
  unchanged (`1`) — content-level change, not a schema change.

## 0.1.0

- Initial extraction from the `ai-hats` integrator into a standalone, data-only
  workspace package (ADR-0014 §3–6; HATS-876 / T18). The `core/` + `usage/` +
  `hooks/` content is unchanged; the move makes it independently
  `pip install`-able and `git clone`-droppable. The integrator now resolves its
  built-in library layer from `importlib.resources.files("ai_hats_library")`
  through a single `as_file` seam (review P1 #14).
