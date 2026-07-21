# `backlog.yaml` grammar reference

The complete key grammar for a rack backlog definition. The **authority** is the
packaged `backlog-schema.yaml` (the loader reads its allow-sets from that file)
and `definition.py` (the fail-closed checks). Design rationale: ADR-0017.

Anything not listed at a level is rejected — `UnsupportedBacklogKeyError` names
the key. Structure is in the file; **semantics** (handler / validator /
extension names) resolve against a code-side registry at composition.

## Top level

`name / prefix / cli_alias / fsm / links / fields / extensions / extras`

| Key          | Req | Type          | Meaning                                                                        |
| ------------ | --- | ------------- | ------------------------------------------------------------------------------ |
| `name`       | ✔   | str           | Backlog identity; the default CLI group name when `cli_alias` is unset.        |
| `prefix`     | ✔   | str           | Id prefix — cards are `<prefix>-<n>`. **Unique within a root** (see Mounting). |
| `cli_alias`  |     | str           | Short `rack <group>` name. Defaults to `name`. Must be unique across mounts.   |
| `fsm`        | ✔   | mapping       | The state machine — `{initial, states, edges}`.                                |
| `links`      | ✔   | mapping       | `{kinds: [...]}` — the card-to-card edge kinds. `kinds` must be **non-empty**. |
| `fields`     |     | list          | Card schema beyond the engine anchor.                                          |
| `extensions` |     | list[ref]     | Ambient (self-subscribing) extensions — e.g. `hyp-verdicts`, `prop-votes`.     |
| `extras`     |     | allow\|forbid | Unknown-key policy for stored cards. Default `allow`.                          |

The **engine anchor** — `id`, `state`, `title`, `work_log`, `created`,
`updated` (+ the `extras` passthrough) — is kernel-owned and is **not** declared
in `fields`. `title` is the only required create input. There is **no
`sections:` key**: the plan-section catalog lives in a sibling
`plan-sections.yaml` (HATS-635 never-drift), config of the plan extension, not
of the backlog.

## `fsm` → `initial / states / edges`

- `initial` — the start state's `name`.
- `states` — list; each entry a bare string **or** `{name, on_enter?, on_exit?}`.
- `edges` — list of edge objects; **every edge is explicit** (no adjacency map).

### state → `name / on_enter / on_exit`

| Key                    | Meaning                                                                                                                           |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `name`                 | State id.                                                                                                                         |
| `on_enter` / `on_exit` | list[handler-ref] — entry / exit effects; expand over the full edge product so a forced non-topology transition still fires them. |

### edge → `from / to / name / handlers / skip`

| Key         | Req | Meaning                                                                                                                                                            |
| ----------- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `from`,`to` | ✔   | State names. The canonical event key stays positional `edge:<from>--<to>`.                                                                                         |
| `name`      |     | Alias event key **and** the transition verb (`rack <alias> <name>` / `transition <ID> <name>`). **Must not equal any state name** (`EdgeNameStateCollisionError`). |
| `handlers`  |     | list[handler-ref] fired in-lock on this exact edge (e.g. a quorum gate).                                                                                           |
| `skip`      |     | list[str] — opt this edge out of a named `on_enter`/`on_exit` handler (the declarative reopen-exception form).                                                     |

## `links` → `kinds[]`

`kinds` must be a **non-empty** list (`LinksRegistryError` otherwise). A backlog
with no cross-links still declares one — a symmetric self-inverse kind
(`{ name: related, arity: many, inverse: related }`) is the minimal,
mirror-exempt default.

kind → `name / arity / inverse / derived / aliases / handlers / targets / read / read_docs`

| Key                | Meaning                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| `name`             | Kind name == the storage field on the card.                                                                    |
| `arity`            | `one` \| `many`.                                                                                               |
| `inverse`          | Name of the reverse kind.                                                                                      |
| `derived`          | bool — a computed reverse view (e.g. `children`); not stored, no mirror.                                       |
| `aliases`          | list[str] — alternate CLI tokens (e.g. `depends` → `depends_on`).                                              |
| `handlers`         | list[handler-ref] — in-lock link/unlink handlers. A **stored inverse pair requires `[mirror-link]`** (below).  |
| `targets`          | Sibling backlog `name` a cross-backlog kind points at (e.g. HYP `source_task` → `tasks`). Unset = own backlog. |
| `read`,`read_docs` | Read-phase enricher handlers + doc names surfaced on a `context` read (HATS-1064).                             |

**Stored-inverse rule (`MissingMirrorReactionError`):** a stored kind whose
`inverse` is another stored, non-symmetric kind **must** declare
`handlers: [mirror-link]`, or the reverse edge drifts undetected. Derived
inverses (`children`) and symmetric kinds (`related`, one-sided storage) are
exempt.

## `fields[]`

field → `name / type / default / required / choices / validator / emit`

| Key         | Meaning                                                                                                                                                  |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`      | Non-empty, unique across `fields`.                                                                                                                       |
| `type`      | `str` \| `int` \| `list` \| `any` (default `str`). A complex shape is `type: any` **plus a mandatory `validator`** — there is no inline item-schema DSL. |
| `default`   | Fill value; its presence marks the field as having a default (vs required/no-default).                                                                   |
| `required`  | bool — enforced strictly by create/update.                                                                                                               |
| `choices`   | list — enum; enforced by create/update.                                                                                                                  |
| `validator` | Bare name resolved against the open **validator** registry (never a check hidden in code).                                                               |
| `emit`      | `always` (default) \| `when-set` — `when-set` drops an empty value at write time.                                                                        |

## Handler reference

A handler-ref (in `on_enter`/`on_exit`/`edges[].handlers`/`kinds[].handlers`/
`extensions`) is a **bare name** or a `{name, ...}` mapping:

- Universal keys: `name`, `priority` (int — explicit order pin; unpinned refs
  take a positional band), `timeout` (seconds; default 60, for subprocess handlers).
- **Any other key is handler-specific config**, passed verbatim to the factory
  (e.g. `stamp-lifecycle` reads `field:`, `hyp-quorum-gate` reads
  `min_independent_sessions:`).
- The `name` must resolve in the stock **factory** registry. You cannot invent a
  new handler/validator/extension name in YAML alone — it needs a code-side
  factory registered at the composition root, else composition fails closed.

## Fail-closed errors (author-facing)

| Error                         | Trigger                                                                                                     |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `UnsupportedBacklogKeyError`  | An unknown key at any level (e.g. a stray `sections:`).                                                     |
| `BacklogDefinitionError`      | Missing `name`/`prefix`/`fsm`/`links`; bad `type`/`emit`/`choices`; duplicate field; malformed handler ref. |
| `LinksRegistryError`          | `links.kinds` empty or malformed — declare at least one kind.                                               |
| `EdgeNameStateCollisionError` | An edge `name` equals a state name.                                                                         |
| `MissingMirrorReactionError`  | A stored inverse pair without `mirror-link`.                                                                |
| `DuplicatePrefixError`        | Two backlogs claim the same `prefix` in one root (mount time).                                              |
| `DuplicateGroupNameError`     | Two backlogs resolve to the same CLI group (`cli_alias`/`name`).                                            |
| `LegacyLinksOverrideError`    | A retired project-root `links.yaml` exists — fold it into `backlog.yaml`.                                   |

**Read-tolerant / write-strict:** loading a card missing a declared field fills
its default; unknown keys ride `extras`; a stored type/choices violation is a
`context` **warning**, never a load failure (`ls`/`context` never brick on an old
or foreign backlog). Create/update enforce `required`/`choices`/`validator`
strictly; a transition validates only the fields it touches.

## Mounting

- Put the catalog dir **anywhere under `<ai_hats_dir>/tracker/`** except inside
  the tasks catalog (`tracker/backlog/tasks/`). Convention: a sibling of `tasks`,
  e.g. `tracker/backlog/<name>/` (that is where the shipped HYP/PROP live).
- `Workspace.discover` → `_scan_sibling_backlogs` walks `tracker/**`, loads every
  `backlog.yaml`, and treats each such dir as a **leaf catalog** (it does not
  descend into a catalog's card dirs). No registration — drop-in.
- The scan runs only when the tasks-dir keeps the conventional
  `tracker/backlog/tasks` tail; a non-conventional `--tasks-dir` override leaves
  the tasks instance standing alone (no siblings mounted).
- Prefix uniqueness is validated **within** a root. Across roots a duplicate is
  legal but routing then needs the `<root>:<id>` qualifier.

## Seeds

The shipped HYP / PROP definitions are the reference examples —
`packages/ai-hats-rack/src/ai_hats_rack/definitions/{hypotheses,proposals}/backlog.yaml`.
Copy one as a starting point and adapt `name`/`prefix`/`cli_alias`/`fsm`/`fields`.
