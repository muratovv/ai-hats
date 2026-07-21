---
name: rack-advanced
description: "Author a new custom rack backlog — its backlog.yaml definition and how it mounts under tracker/. Use when standing up a backlog beyond the built-in tasks/HYP/PROP (its own prefix, FSM, fields, or link kinds), editing a backlog.yaml, or debugging one that won't mount or route."
license: MIT
---

# Rack Advanced

Advanced rack operations beyond day-to-day lifecycle. Current content: **authoring
a new backlog** — defining a `backlog.yaml` and mounting it under `tracker/`.

## When to Use

Reach here when the task is to **define a new backlog** (a distinct catalog with
its own id prefix, state machine, fields, and link kinds) — e.g. a project
`decisions`, `experiments`, or `incidents` backlog alongside the built-in
tasks / hypotheses / proposals.

Not this skill when you are:

- **filing a card** (`rack create`, or the `backlog-create` shim) — that adds a
  card to an existing backlog, it does not define one;
- **driving a card's lifecycle / ops** (transition, log, link, hyp/proposal
  verbs) — that is **hatrack**.

The moment you are editing `backlog.yaml` itself (not a `task.yaml`), you are here.

## Procedure

There is **no `rack init` / register verb** — a backlog is drop-a-file. Author
the definition, place it under `tracker/`, and it mounts on the next `rack` call.

1. **Pick identity.** A `prefix` (cards become `<prefix>-<n>`) unique within the
   root, and a `cli_alias` (the short `rack <group>` name) unique across mounted
   backlogs. Do not reuse `HATS` or an existing alias (`DuplicatePrefixError` /
   `DuplicateGroupNameError` at mount).
2. **Make the catalog dir** under `<ai_hats_dir>/tracker/` — convention
   `tracker/backlog/<name>/`, a sibling of `tasks/` (where the shipped HYP/PROP
   live). Anywhere under `tracker/` works except inside the tasks catalog.
3. **Author `backlog.yaml`** at that dir's root. Full key grammar +
   fail-closed invariants: **`references/backlog-yaml-grammar.md`**. Fastest
   start — copy a shipped definition
   (`packages/ai-hats-rack/src/ai_hats_rack/definitions/hypotheses/backlog.yaml`)
   and adapt. Minimum skeleton:

   ```yaml
   name: decisions
   prefix: DEC
   cli_alias: decision
   fsm:
     initial: open
     states: [{ name: open }, { name: accepted }, { name: superseded }]
     edges:
       - { from: open, to: accepted, name: accept }
       - { from: open, to: superseded, name: supersede }
   links:
     kinds: # must be NON-EMPTY; a symmetric self-inverse kind is the minimum
       - { name: related, arity: many, inverse: related }
   fields:
     - { name: context, type: str, default: "" }
   ```

4. **It auto-mounts.** `Workspace.discover` → `_scan_sibling_backlogs` walks
   `tracker/**` and picks up every `backlog.yaml`. No registration step.
5. **Verify it landed:** `rack --help` lists the `<cli_alias>` group; `rack
   <alias> create "…"` mints `<PREFIX>-001` (ids are zero-padded, min 3
   digits); `rack context <PREFIX>-001` routes by prefix; `rack ls --backlog
   <alias>` filters to it.

**Before you save — invariant checklist** (each is a fail-closed loader error):

- Every top/section key is in the grammar — a stray key (e.g. `sections:`) is
  rejected (`UnsupportedBacklogKeyError`).
- `links.kinds` is **non-empty** (`LinksRegistryError` otherwise) — a backlog
  with no cross-links still needs one kind; `related` (symmetric) is the minimum.
- No edge `name` equals a state name (`EdgeNameStateCollisionError`).
- Any **stored inverse pair** (`inverse:` on both, both stored) carries
  `handlers: [mirror-link]` (`MissingMirrorReactionError`). Symmetric
  (`related`) and derived (`children`) kinds are exempt.
- Complex fields are `type: any` **plus** a `validator:` name.
- Every handler / validator / extension name resolves in the stock code-side
  registry — you cannot introduce one in YAML alone.

**Plan sections are separate.** If (and only if) the backlog uses the plan gate,
its plan-section catalog lives in a sibling `plan-sections.yaml`, **not** a
`backlog.yaml` key (HATS-635 never-drift). Most custom backlogs need neither.

## What the backlog gets for free

Once mounted, the definition alone yields (no per-backlog code):

- `rack <alias> create …` — schema-driven from `fields` (required/choices enforced).
- `rack <alias> update <ID> --<field> …` — scalar (str/int) field edits.
- Extension verbs any declared `extensions` contribute (e.g. `append-verdict`).
- Prefix-routed reads/moves: `rack context <ID>`, `rack ls <ID>`, `rack
  transition <ID> <state|edge-name>`, `rack ls --backlog <alias>`.

## Completion

Done when: the `backlog.yaml` sits under `tracker/`, `rack --help` shows its
group, and `rack <alias> create` + `rack context <PREFIX>-001` both work — with no
loader error on any `rack` call.

**Validation scenario (RED → GREEN).** RED — asked to add a project `decisions`
backlog, an agent *without* this skill hunts for a `rack init-backlog`/register
verb that does not exist, reuses the `HATS` prefix (`DuplicatePrefixError`), puts
a `sections:` key in the file (rejected — unknown key), or declares a
`supersedes`/`superseded_by` pair with no `mirror-link`
(`MissingMirrorReactionError`) — a multi-error discovery loop. GREEN — with the
skill it writes `tracker/backlog/decisions/backlog.yaml` (unique `prefix: DEC` +
`cli_alias: decision`, valid fsm/fields/links, no `sections:`, `mirror-link` on
any stored inverse), drops it in, and `rack decision create "…"` works first try.

## Anti-Patterns

- Searching for a create/register/init command — mounting is drop-a-file;
  `Workspace.discover` does it.
- Reusing `HATS` (or another backlog's prefix / alias) — collisions fail closed
  at mount.
- Putting a `sections:` (or any undeclared) key in `backlog.yaml` — plan sections
  are a sibling `plan-sections.yaml`; unknown keys are rejected.
- Declaring a stored inverse link pair without `mirror-link` — the reverse edge
  would drift, so the loader refuses it.
- Inventing a handler / validator name in YAML with no code-side factory — the
  name must resolve in the stock registry or composition fails closed.
