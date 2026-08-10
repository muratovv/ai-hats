# Changelog — ai-hats-rack

All notable changes to this package are documented here. Semantic versioning on
the `rack` CLI surface and the backlog-kernel format.

## 0.2.0

- **The rack owns its own point names** (HATS-1541, ADR-0019 D11). New public
  module `ai_hats_rack.checks`: the `edge:<from>--<to>` grammar, the filter
  against the topology the kernel is running, the in-lock subscriber, and the
  `CheckPort` an integrator implements to supply declarations and to run one.
  Until now the integrator held all of it and validated names against a
  *packaged* topology while the kernel ran the resolved one, so a point aimed at
  a sibling backlog was indistinguishable from a typo — and refusing on it took
  unrelated transitions down. A point this instance has no edge for is now
  skipped, not refused.
- **The kernel-factory entry point is loaded inside a `try`**. A half-installed
  or version-skewed integrator raised a bare `ImportError`/`AttributeError` out
  of `_provider()` naming nothing; it is now a `RackConfigError` that says which
  entry point failed and why running bare instead would be worse.
- The rack still never executes: `subprocess` remains forbidden by the import
  pin, so a bound check travels back over the port to the integrator that can
  spawn it. The per-check deadline moved here with `LOCK_TIMEOUT`, and is passed
  in the request rather than duplicated on both sides.

## 0.1.7

- **`depends_on` has a reverse view: `blocks`** (HATS-1208). Derived-kind
  resolution stopped being hardcoded to `children` — any kind declaring
  `derived: true` resolves from the kernel's reverse scan, and the tasks
  backlog declares `blocks` as the inverse of `depends_on`. `rack context <id>`
  renders a `Blocks:` section; nothing new is stored, so the two directions
  cannot drift.
- Reverse scans are answered from a single catalog pass, memoized per kernel
  instance and dropped on that kernel's writes. Asking per target re-read the
  whole catalog once per node, so a `--deep 2` walk of the 871-card ai-hats
  backlog went from ~2.0 s to ~0.10 s. `children_of` / `is_epic` deliberately
  stay off the memo — their contract is a fresh count per dispatch.
- A neighbour card that does not parse no longer sinks the read. The reverse
  scan reaches cards the forward read never touches, and it caught only
  `OSError`/`ValueError` — a malformed `task.yaml` (`yaml.ScannerError`) or a
  `CardLoadError` took down `rack context` for *every* card in the backlog.
- The scan reads a scalar link field off the card text and defers to the parser
  for shapes a flat read cannot judge, so an id carrying a free-form tail
  (`HATS-100 fix`) keeps its children view and a duplicate key resolves the way
  YAML does — last one wins.
- `hierarchy_kind` prefers a scalar candidate now that a `many` kind can carry a
  derived inverse too, and still elects a lone `many` one. Requiring `arity:
  one` outright would have silently disabled epic automation for any registry
  written before `blocks`, since `many` is the arity default.

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
    legacy spelling. Its inverse ("Subsumed by") is not derived. (Derived kinds
    were `children`-only at the time; 0.1.7 generalised the mechanism and added
    `blocks`. `folded_into`'s inverse is still not derived.)
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
