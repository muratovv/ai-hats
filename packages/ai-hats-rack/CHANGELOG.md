# Changelog — ai-hats-rack

All notable changes to this package are documented here. Semantic versioning on
the `rack` CLI surface and the backlog-kernel format.

## 0.1.2

- Declare `see_also` and `folded_into` as link kinds on the tasks backlog
  (HATS-1279). Both were already typed storage on the card model, so cards
  written by the retired `ai-hats task` CLI carried edges `rack context` walked
  right past — the registry drives the projection, and an undeclared kind is
  invisible. Ten cards in the ai-hats backlog were affected.
  - `see_also` — symmetric soft pointer, alongside `related`.
  - `folded_into` — directional, `arity: one`; `--link fold:<ID>` preserves the
    legacy spelling. Its inverse ("Subsumed by") is not derived: derived kinds
    are still `children`-only.
- `rack ls` rows carry `completed_at` when set (HATS-1279) — the first time
  signal on a listing row, so `--state done` can be windowed with `jq` instead
  of a context read per card. Emitted only when set, like `backlog`/`project`.

## 0.1.0

- Initial release — the minimal backlog kernel for ai-hats (epic HATS-1014):
  `backlog.yaml` topology, a two-phase subscriber dispatcher, single-persist
  transitions under one per-task lock, and a dispatch journal.
- `rack` CLI: `create` / `ls` / `context` / `transition` / `plan-extract`, with
  field edits via `transition --set` / `--append` and the `rack hyp` /
  `rack proposal` backlog groups.
