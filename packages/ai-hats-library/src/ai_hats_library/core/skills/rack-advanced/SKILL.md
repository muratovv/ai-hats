---
name: rack-advanced
description: "Advanced rack: author a new custom backlog (backlog.yaml + mounting), and sweep a backlog across several projects. Use when standing up a backlog beyond the built-in tasks/HYP/PROP (its own prefix, FSM, fields, or link kinds), editing a backlog.yaml, debugging one that won't mount or route, registering projects with `rack root`, or listing/reading one backlog across projects (`ls --projects/--root`, `<root>:<id>`)."
license: MIT
---

# Rack Advanced

Advanced rack operations beyond day-to-day lifecycle: **authoring a new backlog**
(defining a `backlog.yaml` and mounting it under `tracker/`) and **cross-project
sweeps** (registering projects and listing/reading one backlog across them).

## When to Use

Reach here when the task is to **define a new backlog** (a distinct catalog with
its own id prefix, state machine, fields, and link kinds) — e.g. a project
`decisions`, `experiments`, or `incidents` backlog alongside the built-in
tasks / hypotheses / proposals.

Also reach here for a **cross-project sweep** — registering projects with `rack
root` (add / list / remove), or listing / reading one backlog across several
projects (`ls --projects` / `--root`, a `<root_id>:<id>` read). See
[Cross-project sweep](#cross-project-sweep-registry--search).

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
   start — print a shipped example (HYP / PROP) from the installed package and
   adapt (works in any project, no repo checkout needed):

   ```bash
   python -c "from ai_hats_rack.definition import packaged_definition_source as s; print(s('hypotheses'))"  # or: proposals
   ```

   (If a bare `python` can't import `ai_hats_rack`, use the interpreter that
   backs `rack`.) Or start from the minimum skeleton:

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

## Cross-project sweep (registry + search)

List or read **one backlog across several projects** — "hypotheses of all
projects" in one command (HATS-1081). Two parts: a project registry, and the
cross-project flags on `ls` / `context`.

**Register the projects you sweep** — a persistent list at `~/.ai-hats/roots.yaml`
(override the path with `RACK_ROOTS_FILE`):

```bash
rack root add <path>   # register a project root (must hold .agent/ or ai-hats.yaml)
rack root ls           # list registered roots: root_id, path, reachable?
rack root rm <path>    # unregister a root by path
```

`root_id` is the project's **folder name** (no alias). `add` validates the path is
a project, stores it resolved, and de-dupes (a re-add is a no-op); `rm` takes the
same path. Registering is optional — `--root` sweeps ad-hoc roots without touching
the file.

**Search across projects** on the no-id scan (rows gain a `project` marker column /
`project` json key; the current project is **always** included, roots dedup by real
path):

```bash
rack ls --projects all               # every registered project ∪ the current one
rack ls --projects projA,projB       # a named subset (by root_id) ∪ the current one
rack ls --root ../other              # ad-hoc: add a root by path (repeatable)
rack ls --backlog hyp --projects all # the hyp backlog in EVERY swept project
```

**Read across projects** — a bare id is ambiguous when two projects share a prefix,
so qualify it (or mount an unregistered project by path):

```bash
rack context projB:HATS-9              # <root_id>:<id> routes into a registered project
rack context projB:HATS-9 --root ../projB   # or mount it by path
```

**Boundaries.**

- **Read-only.** The sweep and qualified `context` are reads; cross-project
  *writes* (qualified `transition`, cross-project `create`, cross-project links)
  are out of scope — a sweep never mutates another project.
- **Read-tolerant registry, fail-fast `--root`.** A registered root that has
  vanished is *skipped* with a non-silent footer (the sweep survives); an explicit
  `--root` to a non-project is a hard error.
- **Folder-name collision** (two `ai-hats` clones): the `<root_id>:<id>` qualifier
  is ambiguous — disambiguate with the full path via `--root`.

## Completion

**Authoring a backlog** — done when: the `backlog.yaml` sits under `tracker/`,
`rack --help` shows its group, and `rack <alias> create` + `rack context
<PREFIX>-001` both work — with no loader error on any `rack` call.

**Cross-project sweep** — done when: `rack root add <path>` then `rack root ls`
shows it, and `rack ls --projects all` lists cards from the registered project(s)
alongside the current one, each row carrying its `project` marker.

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
- Expecting `--projects` / `--root` to route a *write* — the sweep is read-only;
  cross-project mutation is out of scope.
- Leaning on a `<root_id>:<id>` qualifier when two roots share a folder name — the
  id is ambiguous; use the full path via `--root`.
- Hand-editing `~/.ai-hats/roots.yaml` for a bad path — `rack root add` validates
  and resolves; a non-project path is rejected there.
