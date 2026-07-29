# Changelog — ai-hats-rack

All notable changes to this package are documented here. Semantic versioning on
the `rack` CLI surface and the backlog-kernel format.

## 0.1.6

- New verb: **`rack doctor`** — read-only backlog integrity report over every
  backlog mounted in the current project (HATS-1335). Checks: dangling links
  (all stored kinds, cross-backlog refs routed to their sibling catalog),
  transitive link cycles on directional kinds (the shape the HATS-1327 pair
  guard deliberately leaves uncovered), stored-inverse mirror drift, duplicate
  ids in list kinds, missing `required`/`required_on` fields, and unreadable
  cards — a card directory `scan_cards` would silently skip is a finding here.
  Exit 0 clean, exit 1 with findings. No autofix by design: a "fixed" id is a
  guess; repair stays a human `transition --link/--unlink` decision.
- `link`/`unlink` are no longer exported from the package root (HATS-1335).
  They are the lock-free wrappers that fire **no** link events, so a library
  caller could silently bypass the `mirror-link` reaction; the event-bearing
  write path is `transition --link`. The functions remain module-internal
  (`ai_hats_rack.linked`) with the contract documented.

## 0.1.5

- `parent_task` now goes through the same link op as `depends_on` and
  `transition --link` on every write path (HATS-1333). `create --parent` and
  `set_parent` each wrote the field raw, so neither validated the target.
  Consequences:
  - **A parent that does not exist is refused** (`unknown_task`). This is how
    malformed ids landed in real backlogs — a card written with `parent_task:
    1092` (no prefix) is silently orphaned from its epic: epic automation never
    fires, `ls --deep` does not show it, and parent-context inheritance
    (`work_policy`) never reaches it.
  - **Self-parent is one typed `self_link` refusal**, not a bare `ValueError`
    raised separately per write path. The message now routes through the CLI
    error table like every other link refusal.
  - **`create --parent` logs the link** in `work_log`, as `--depends` has since
    0.1.4.
- Reading a card that already holds a dangling parent is unchanged and stays
  tolerant — the tightening is on the write side only, so pre-existing data
  keeps loading and transitioning.

## 0.1.4

- `create` now writes its declared links through the same op as
  `transition --link` (HATS-1327). Previously `--depends` wrote the field raw,
  bypassing every guard the link path applies. Two behaviour changes follow:
  - **`--depends` validates the target.** A dependency on an id that does not
    exist is now a typed `unknown_task` refusal, matching `transition --link`;
    it used to be accepted and persisted as a dangling edge.
  - **`create --depends` logs what it linked.** The card used to be persisted
    with an empty `work_log`.
- A mutual pair on a link kind that declares no `inverse` is refused as
  `reciprocal_link` (HATS-1327). `A depends_on B` plus `B depends_on A` is a
  deadlock, not a relationship; the same holds for `folded_into`. Kinds that
  declare an inverse (`related`, `see_also`, `parent_task`) are bidirectional by
  design and unaffected. Restores the guard the retired tracker enforced; like
  the tracker, only the immediate pair is detected, not a transitive
  `A → B → C → A`.

## 0.1.3

- Zero-residue docstring fix in `plan_extract.py` (HATS-1262).

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
