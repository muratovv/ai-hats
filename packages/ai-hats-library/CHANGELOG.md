# Changelog — ai-hats-library

All notable changes to this package are documented here. Versioning is semantic,
on the library **format schema** (see README § Versioning).

## 0.1.0

- Initial extraction from the `ai-hats` integrator into a standalone, data-only
  workspace package (ADR-0014 §3–6; HATS-876 / T18). The `core/` + `usage/` +
  `hooks/` content is unchanged; the move makes it independently
  `pip install`-able and `git clone`-droppable. The integrator now resolves its
  built-in library layer from `importlib.resources.files("ai_hats_library")`
  through a single `as_file` seam (review P1 #14).
