# Changelog — ai-hats-rack

All notable changes to this package are documented here. Semantic versioning on
the `rack` CLI surface and the backlog-kernel format.

## 0.1.0

- Initial release — the minimal backlog kernel for ai-hats (epic HATS-1014):
  `backlog.yaml` topology, a two-phase subscriber dispatcher, single-persist
  transitions under one per-task lock, and a dispatch journal.
- `rack` CLI: `create` / `ls` / `context` / `transition` / `plan-extract`, with
  field edits via `transition --set` / `--append` and the `rack hyp` /
  `rack proposal` backlog groups.
