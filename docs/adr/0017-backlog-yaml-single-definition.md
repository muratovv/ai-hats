# ADR-0017: `backlog.yaml` — the single backlog definition + multi-backlog workspace

## Status

Accepted (ratified via HATS-1034 review 2026-07-18).

Design gate for T3 (HATS-1035, schema-driven card fields) and T4 (HATS-1036,
verb-builder CLI). Extends the epic HATS-1014 design record; resolves the
generalization trigger declared in the epic's FSM design note §5.1 [10].

## Context

The rack currently carries **three separate definition surfaces** plus code:

| Surface       | Home                                             | Declares                                          |
| ------------- | ------------------------------------------------ | ------------------------------------------------- |
| FSM topology  | packaged `fsm.yaml` [1], `load_topology()`       | states + adjacency edges                          |
| Link kinds    | packaged `links.yaml` [2], `load_registry_for()` | edge kinds (name == storage field), inverse pairs |
| Plan sections | `DEFAULT_PLAN_SECTIONS` in code [3]              | plan.md catalog (`load_sections()` — YAML         |
|               |                                                  | loader exists, no default file)                   |
| Card fields   | hardcoded `TaskCard` model + `extras`            | field set, defaults, requiredness                 |

There is no artifact that *is* "the definition of a backlog" — the concept
exists only as a convention across these four places. Supervisor hunk-review
(comments #9/#8а/#8б on HATS-1014) requires: **one `backlog.yaml`** carrying
states + named edges + events on edges + link kinds + the card field schema,
and **multi-backlog** operation — task, hypothesis, and proposal backlogs
living simultaneously and linked to each other.

The epic's FSM note §5.1 [10] decided "fsm.yaml = in-package SSOT, not user
config" to keep the event vocabulary stable, and named its own generalization
trigger: *"if HYP/PROP acquire gated transitions, a second internal consumer
appears and a generic table-driven interpreter over `{entity, states, edges}`
becomes justified."* HYP quorum-gated closing (`autoclose`, K-refuted quorum)
and PROP vote-gated acceptance are exactly such consumers. This ADR is that
trigger firing — the stability argument is *resolved*, not overturned (see
Consequences §"What survives of §5.1").

Interview decisions (supervisor, 2026-07-17, HATS-1034 plan R3):

- **A** — N kernel instances, one per backlog (catalog + definition), living
  simultaneously under a workspace resolver; backlogs are cross-linked.
- **B** — card field schema lives in `backlog.yaml`; engine anchor stays fixed.
- **C** — no inheritance between definitions in v1.
- **D** — each backlog declares exactly one id prefix; set-level uniqueness.

## Decision

### 1. `BacklogDefinition` — one file, five sections

A backlog is a **catalog directory + one `backlog.yaml`** at its root. The
file fully defines the backlog; the loader produces one immutable
`BacklogDefinition` value that every module is constructed from.

The packaged default `backlog.yaml` replaces `fsm.yaml` + `links.yaml` +
`DEFAULT_PLAN_SECTIONS` **losslessly** (mapping table in §6) and *is* the
tasks-backlog contract:

```yaml
# ai_hats_rack/backlog.yaml — packaged default: the tasks backlog (kernel CONTRACT)
name: tasks
prefix: HATS # instance override: ai-hats.yaml task_prefix (unchanged)

fsm:
  initial: brainstorm
  states:
    # A state carries the handlers of its ENTRY: on_enter expands to the full
    # edge:<src>--<state> product (§3), so a forced non-topology entry cannot
    # slip past a gate — HATS-518 semantics preserved.
    - { name: brainstorm }
    - { name: plan, on_enter: [plan-scaffold] }
    # on_exit mirrors on_enter over the full edge:<state>--<dst> product (§3):
    # release-on-leaving-execute fires even on a forced non-topology exit.
    # The integrator APPENDS ownership/worktree handlers via the code channel
    # (§4) — this packaged default stays the standalone kit.
    - { name: execute, on_enter: [plan-gate] }
    - { name: document }
    - { name: review }
    # completed_at/final_state stamping is a stock declared handler, not
    # kernel code (today's kernel hardcode moves out — kernel slimming, T5):
    - { name: done, on_enter: [stamp-lifecycle] }
    - { name: blocked }
    - { name: failed }
    - { name: cancelled, on_enter: [stamp-lifecycle] }
  edges:
    # ONE shape everywhere — every edge explicit, no adjacency lists.
    # Optional per edge: name (alias event key + T4 verb; canonical key
    # stays edge:<from>--<to>, §3), handlers (ordered, this exact edge),
    # skip (opt this edge out of an on_enter handler).
    - { from: brainstorm, to: plan }
    - { from: brainstorm, to: blocked }
    - { from: brainstorm, to: cancelled }
    - { from: plan, to: execute }
    - { from: plan, to: blocked }
    - { from: plan, to: cancelled }
    - { from: execute, to: execute, name: reclaim } # HATS-955
    - { from: execute, to: document }
    - { from: execute, to: blocked }
    - { from: execute, to: failed }
    - { from: execute, to: cancelled }
    - { from: document, to: review }
    - { from: document, to: blocked }
    - { from: document, to: cancelled }
    - { from: review, to: done }
    - { from: review, to: failed }
    - { from: review, to: cancelled }
    - { from: blocked, to: brainstorm }
    - { from: blocked, to: plan }
    - { from: blocked, to: execute }
    - { from: blocked, to: document }
    - { from: blocked, to: cancelled }
    - { from: failed, to: brainstorm }
    - { from: failed, to: cancelled }
    # reopen: gate skipped (HATS-328), lifecycle stamps cleared (declared,
    # not the kernel.py done→execute hardcode):
    - { from: done, to: execute, name: reopen, skip: [plan-gate], handlers: [clear-lifecycle] }

fields:
  # Engine anchor is NOT declared here: id, state, title, work_log, created,
  # updated (+ extras passthrough) are kernel-owned; title is the only
  # required create-input (T3). Everything below is this backlog's schema.
  # When a field outgrows type/choices, its check is declared HERE too — an
  # optional per-field `validator: <name>` resolved via the open registry
  # (§4); never a check hidden in extension code with no trace in the schema.
  - { name: description, type: str, default: "" }
  - { name: priority, type: str, default: medium, choices: [low, medium, high, critical] }
  - { name: assignee, type: str, default: "" }
  - { name: reviewer, type: str, default: user }
  - { name: role, type: str, default: "" }
  - { name: tags, type: list, default: [] }
  - { name: resolution, type: str, default: "" }
  # written by stamp-lifecycle/clear-lifecycle (declared handlers above) —
  # they leave the kernel anchor, becoming ordinary schema fields:
  - { name: completed_at, type: str, default: "" }
  - { name: final_state, type: str, default: "" }

links:
  kinds: # links.yaml content [2] (post-HATS-1032: kind name == storage field).
    # A kind is an edge DECLARATION exactly like fsm.edges — card-to-card
    # instead of state-to-state — and may carry the same `handlers:` slot,
    # fired on link/unlink of that kind (§3); e.g. a dep-cycle-check on
    # depends_on. Same model, both edge families.
    - { name: parent_task, arity: one, inverse: children }
    - { name: depends_on, arity: many, aliases: [depends] }
    - { name: related, arity: many, inverse: related }
    - { name: children, derived: true, inverse: parent_task }

extensions: # ambient SELF-SUBSCRIBING subscribers: all-edges guards and
  # non-edge reactions (epicify, pre-destroy) whose keys are not one
  # state/edge. Declaration-bound handlers live in fsm/links above.
  - frozen-integrity # all-edges in-lock drift guard + pre-destroy
  - derived-views # post-lock STATE.md refresh (all edges)
  # the integrator appends epic-automation, ownership, worktree via the
  # code channel (§4) — they are not part of the standalone contract.
```

Field grammar limits (review 2026-07-18): `type` is `str | int | list | any`
only — a complex shape (typed `validation_log` entries, nested
`exit_criteria`) is `type: any` plus a **mandatory** `validator: <name>`;
there is deliberately no inline item-schema DSL ("Bash-composable primitives
over DSLs"). A top-level `extras: allow | forbid` (default `allow` — today's
`TaskCard` passthrough [15]) declares the unknown-key policy per backlog. A
per-field `emit: always | when-set` (default `always`; added HATS-1035,
supervisor-approved for HYP/PROP) governs persistence of an empty value —
`when-set` drops it at write time, mirroring today's `resolution` /
`completed_at` / `final_state` serialization.

The plan-section catalog is **not** a `backlog.yaml` section (supervisor
review, 2026-07-17): it is config of one extension, not of the backlog. It
lives in the extension's own file — `plan-sections.yaml` next to
`backlog.yaml` (packaged default ships alongside; a catalog-level file
overrides). Scaffold and gate keep reading the *same* catalog, so the
HATS-635 never-drift invariant is untouched.

Instance resolution (per interview C — no inheritance): a catalog **with** a
`backlog.yaml` uses that file, whole; a catalog **without** one is the tasks
backlog on the packaged default — today's zero-config behavior, unchanged.
The project-root `links.yaml` override (`load_registry_for` [2]) is subsumed
and retired by `backlog.yaml`.

### 2. Multi-backlog: N kernels under one workspace

Per interview A: **one kernel per backlog** — `Kernel` already takes every
definition-derived input as injected config [4] and stays blind to the set it
is part of. The new object is the thin **workspace resolver** above:

```python
@dataclass(frozen=True)
class Workspace:
    # backlog identity is (root, name) — a workspace may mount SEVERAL
    # project roots (review 2026-07-18, claim д: uniform cross-project view).
    backlogs: Mapping[tuple[RootId, str], BacklogInstance]

    @classmethod
    def discover(cls, roots: Sequence[RackRoot]) -> Workspace:
        """Per root: scan <ai_hats_dir>/tracker/** for backlog.yaml files;
        the default tasks catalog (root.tasks_dir) is always an instance,
        packaged definition if no file. Prefix uniqueness is validated
        WITHIN a root (duplicate -> typed load error). ACROSS roots a
        duplicate prefix is legal — id routing then requires the qualified
        form (see kernel_for)."""

    def kernel_for(self, item_id: str, root: RootId | None = None) -> Kernel:
        """Route by id prefix (interview D): HYP-042 -> hypotheses kernel.
        Unknown prefix -> typed error naming the configured prefixes.
        A prefix mounted from several roots -> typed AmbiguousPrefixError
        demanding the qualifier (CLI: --root / <root>:HATS-42) — never a
        silent first-match."""

    def exists(self, item_id: str) -> bool:
        """Cross-backlog existence — the check link() does locally today [5]
        moves here so a link may target ANY configured backlog's cards."""

    def dispatch_mirror(self, event: LinkMirrorEvent) -> None:
        """Post-lock routing of link mirror events to the TARGET backlog's
        kernel (below). The only workspace-level write path."""
```

The `prefix` dual source is resolved: `backlog.yaml prefix:` is
authoritative; the ai-hats.yaml `task_prefix` remains a deprecated alias
applied only to the default tasks instance when it has no file.

**Validation policy (drift tolerance — claim д).** Reads are tolerant,
writes are strict. Loading a card missing a declared field fills its
default; unknown keys ride `extras` (under `extras: allow`); a type/choices
violation on a stored card is a *warning* surfaced by `context`, never a
load failure — `ls`/`context` over an old or foreign backlog must not brick
(the silent-skip of corrupt cards in listings [5] stays as-is). The
create/update verbs enforce `required`/`choices`/`validator` strictly; a
transition validates only the fields it touches. This is the minimum
backward-compatibility contract T3 builds against: schema evolution changes
what new writes must look like, never what old cards may look like.

- **Kernels stay mutually invisible.** No kernel holds a reference to another;
  cross-backlog anything goes through the workspace — mirroring the
  "subscribers get immutable state + delta, no store reference" rule.
- **Cross-backlog links** become declarable: a link kind gains an optional
  `targets: <backlog-name>` (default: own backlog). Existence checks route
  through `Workspace.exists`. This models today's real edges — HYP
  `source_task` → tasks, PROP `related_hypotheses` → hypotheses — as first-
  class kinds instead of untyped strings (sketches in §5).
- **Both sides observe a link** (review 2026-07-18, claim а). The owning
  side's `kinds[].handlers` fire **in-lock** inside the link mutation (may
  abort it). After the owning card persists and its lock is released, the
  workspace dispatches a **mirror event** `link-target:<kind>` to the target
  backlog's kernel; handlers declared on the target's *inverse* kind run
  there as **post-lock reactions** — sequential lock windows, never nested
  (the one-lock rule [6]). A mirror reaction cannot abort the origin (it
  already persisted); it reacts, repairs, or journals.
- **Stored inverse pairs are mirror-maintained.** A declared stored inverse
  (`supersedes`/`superseded_by`, §5) is written on the owning side only;
  the stock `mirror-link` reaction on the target side writes/repairs the
  reverse edge idempotently (convergent, journaled). Same-backlog and
  cross-backlog pairs use the same machinery; without it declared inverse
  pairs would drift undetected — so declaring a stored inverse REQUIRES the
  mirror reaction (loader validates, fail-closed). Derived kinds
  (`children`) stay computed; the reverse scan does not cross catalogs —
  a cross-backlog reverse view is assembled by the workspace read side.
- **Locking is unchanged**: locks are per-card; every lock window covers
  exactly one card. No lock ordering across kernels is introduced, so the
  structural deadlock-freedom argument holds.
- The CLI keeps its verbs; each verb resolves its kernel through the
  workspace (`rack context HYP-042` just works). Verb *schemas* per backlog
  are T4's job, built from `BacklogDefinition` (`on_user_schema`).

### 3. Edges: names, events, and declared handlers

**Handlers are declared where the edge is declared** (supervisor review,
2026-07-17): the definition says not only which edges exist but *what fires
on them* — one model for both edge families, card-state transitions
(`fsm.edges`) and card-to-card links (`links.kinds`). Three binding slots:

- `states[].on_enter` — entry gates/effects of a state. Expands to the
  **full** `edge:<src>--<state>` product, not just declared edges — forced
  transitions fire real non-topology keys, and a gate bound per-edge only
  would silently miss them (the `_edges_into` semantics [3], HATS-518:
  *force weakens the FSM arrow, not the machinery*).
- `states[].on_exit` — exit effects, symmetric: the full
  `edge:<state>--<dst>` product. Required for release semantics — ownership
  release fires on *leaving* execute, forced non-topology exits included
  [13]; an on_enter of the destination cannot own an exit effect of the
  source. `skip` on a declared edge opts out of on_exit handlers too.
- `edges[].handlers` — ordered handlers of one exact edge (e.g. a quorum
  gate on `active → refuted`, §5); `edges[].skip` opts a single edge out of
  an `on_enter`/`on_exit` handler — the declarative form of today's
  hardcoded `done--execute` reopen exception [3].
- `kinds[].handlers` — fired in-lock on link/unlink of that kind on the
  owning side; the target backlog observes via the post-lock mirror event
  (§2). Link mutations dispatch no events today [5]; this surface is
  additive.

Self-loops: the on_enter/on_exit product includes a self-loop key
(`edge:execute--execute`) only when that self-edge is **declared**
(reclaim) — an undeclared self-loop is not an event source, matching
today's special-casing [3][13].

**Execution order is one total order per event: the numeric subscription
priority** (lower first [6]) — declaration-bound and ambient subscribers
interleave in it; there are no separate ordering domains (review
2026-07-18). A declaration-bound handler reference defaults to priorities
assigned from list position (a spaced band, e.g. 100, 110, …) preserving
`on_enter` list → edge `handlers` list order; any reference may pin an
explicit `priority: N` to interleave with ambient subscribers. The
integrator's safety chain (single-slot < frozen < plan-gate < claim <
worktree < release < views [13]) is expressed with explicit priorities —
order is part of the contract, not an accident of wiring. Handler *phase*
(in-lock vs post-lock) is the handler's own property — semantics live in
code (§4), the file only says *where* it hangs.

The canonical event key of an edge **remains positional**:
`edge:<from>--<to>` [6]. A declared `name` adds a stable **alias key**
(`edge:reclaim`) the dispatcher matches in addition — one event, two match
keys. Names also become the human vocabulary for T4 (a named edge is a
natural transition verb) and for the audit journal.

Required-state anchors move out of the engine: `REQUIRED_STATES = (document,)`
in `fsm.py` [1] is a *tasks-discipline* fact (PROP-012 companion-HYP timing),
not a property of every FSM — an HYP topology has no `document`. It becomes an
**extension-declared requirement**, and the declaration covers the extension's
**full semantic state vocabulary, not just its subscriptions** (review
2026-07-18): epic-automation's subscriptions are built *from* the topology and
always match, but its decision tables (`resolved_states`, `active_states`, the
advance chain [16]) are what a modified topology breaks — a child parked in an
unknown state would strand silently (the HATS-692 class), and `topology.allows`
raises `UnknownStateError` for a vocabulary state missing from the FSM [1]. So:
each attached extension exposes `requires_states()`; composition validates it
against the loaded topology, fail-closed. Extensions whose vocabulary IS
policy take it as attachment config — epic-automation's tables become config
keys, adaptable per backlog without engine edits.

**Custom states are a per-backlog right** (claim б). Example — an explicit
agent quality-gate step between review and done:

```yaml
states:
  - { name: quality-gate, on_enter: [qa-checklist] } # new state + its gate
edges:
  - { from: review, to: quality-gate, name: qa }
  - { from: quality-gate, to: done }
  - { from: quality-gate, to: review, name: qa-reject }
extensions:
  - name: epic-automation # vocabulary is config, not hardcode:
    active_states: [plan, execute, document, review, quality-gate]
    advance_chain: [plan, execute, document, review, quality-gate]
```

No engine edits; extensions not told about the state refuse composition
(fail-closed) instead of stranding cards. An A/B-experiment backlog is the
same shape — `draft → running → analyzing → concluded` with
variant/metric/sample fields — no new mechanics beyond §1.

### 4. Extensions: how they work and how they attach

The extension contract keeps its shape [6] — an extension is a `Subscriber`:
`name`, `subscriptions() -> [Subscription(event_key, phase, priority)]`,
`on_event(ctx) -> Delta | None`; in-lock subscribers may `AbortOperation`
with an actionable reason, post-lock subscribers are reactions. Three
contract extensions (review 2026-07-18, claims б/в):

- **`Delta` grows a declared-fields surface**: today it carries `work_log`
  only [6], so "extension owns field X" is unimplementable inside the
  transaction. `Delta(fields={"validation_log": Append(entry)})` — set/append
  ops on *declared* fields, validated against `fields[]`, applied in-memory
  before the single persist. This is what lets `hyp-verdicts` append a
  verdict AND ride the same transition transaction (today's autoclose
  atomicity: entry + status flip under one lock [14]).
- **`bind(kernel)` lifecycle is part of the contract**: a subscriber that
  needs the kernel API post-lock (epic-automation: get/children_of/
  transition; worktree: publish pre-destroy) exposes `bind`; the composition
  root calls it after kernel construction — today's wiring protocol [13][16],
  codified. `DispatchContext` itself stays kernel-free; IN_LOCK handlers
  never get a kernel handle (one lock, never nested).
- **Extension-owned verbs**: a subscriber may expose `verbs()`; T4's builder
  composes the CLI from `BacklogDefinition` (fields/edges/kinds) PLUS the
  attached extensions' verbs — `hyp append-verdict`, `proposal vote`, the
  autoclose sweep are extension verbs, unreachable from the schema alone.
  The verbs surface is T4 design scope (HATS-1036); this ADR only fixes
  where they come from.

What changes is **attachment**. Today the kit is code-composed
(`standalone_extensions()` [7]; worktree/ownership adapters wired by the
integrator in `ai_hats.rack_wiring`) and each extension self-subscribes by
hardcoded event keys. With `backlog.yaml`, *which disciplines apply and
where* is part of the backlog's definition, via the §3 slots:

- **declaration-bound** — `on_enter` / `edges[].handlers` /
  `kinds[].handlers`: the loader *builds* the subscriptions from the
  definition; the handler no longer hardcodes its keys. A reference is a
  bare name or a mapping with config:
  `- { name: hyp-quorum-gate, min_independent_sessions: 3, timeout: 60 }`.
- **ambient** — top-level `extensions:`: subscribers of non-edge events
  (epicify, pre-destroy) that keep their own `subscriptions()`.
- **extension-owned config files** — e.g. `plan-sections.yaml` (§1): config
  that belongs to one extension stays out of `backlog.yaml`.

Structure in the file, semantics in code (the links-registry precedent [2]):
every referenced name — handler, validator, ambient extension — resolves
against **one open factory registry** at the composition root; stock
factories ship with the rack, the integrator registers its own (worktree,
ownership) before building the workspace. Unknown name → typed error,
fail-closed (the materialization rule [10] §4). The factory signature is the
current constructor shape, definition-first:

```python
ExtensionFactory = Callable[[BacklogDefinition, Path, Mapping[str, Any]], Subscriber]
#                            definition          catalog dir   config block

def build_extensions(defn, catalog, factories) -> list[Subscriber]:
    return [factories[e.name](defn, catalog, e.config) for e in defn.extensions]
```

**Project scope enters at factory registration, not in the signature**
(claim в): the worktree adapter needs the repo root, derived views need the
STATE.md path [13] — neither is derivable from (definition, catalog). An
integrator factory is a *closure* over its scope:
`register("worktree", lambda defn, catalog, cfg: WorktreeExtension(project_dir, ...))`.

**Two attachment channels, one registry.** The file declares the portable
kit (§3 slots + `extensions:`); the composition root may additionally
**append subscribers in code** — the integrator channel, today's mechanism
retained [13]. This is why interview C survives: the integrator attaches
worktree/ownership/epic-automation to the packaged tasks definition without
forking the file (a fork would drift from the SSOT on every upstream
change). Cost, accepted: for integrator-attached handlers the file alone
does not show the full picture — the journal's actor/outcome records [6]
and `rack doctor`-style introspection do.

**Handler execution budget (claim г).** The lock model already contains
every crash: `FileLock` is flock-backed — the OS releases it on process
death including SIGKILL; an in-lock handler exception unwinds `with lock:`
(released), aborts before persist, and is journaled; post-lock failures are
fail-soft and journaled; a *concurrent* process never hangs — it fails typed
after `LOCK_TIMEOUT` [4]. The one open hole is a **hung, alive** in-lock
handler holding the card lock forever — noted open since HATS-1015 [10].
Closed here: a handler reference accepts `timeout: <seconds>` (default 60);
the dispatcher enforces it for handlers that run subprocesses (K4 consumer
hooks, git operations) by killing the process group → treated as an in-lock
error → abort + journal. Pure in-process Python handlers are
trusted-unbounded — stated as the accepted risk (they are reviewed code from
the registry, not arbitrary consumer input). Lock-recovery guidance is
pid-based staleness detection; **never** delete a held lock file (fresh
inode → two holders) — the current `LockTimeoutError` hint [4] is amended.

### 5. Module initialization — end to end

```python
# per backlog (replaces today's cli._kernel() [8]):
defn = load_backlog(catalog / "backlog.yaml")     # or packaged default
subs = build_extensions(defn, catalog, factories) # §4: registry + closures
kernel = Kernel(
    catalog,
    prefix=defn.prefix,
    topology=defn.topology,                       # from defn.fsm
    registry=defn.links_registry,                 # from defn.links
    subscribers=subs,
    journal_sink=JsonlJournalSink(catalog),
)
for sub in subs:                                  # §4 bind lifecycle —
    if hasattr(sub, "bind"):                      # post-lock kernel API
        sub.bind(kernel)                          # (epic-automation, worktree)

# per workspace (the CLI chokepoint):
root = resolve_root(caller_cwd)                   # unchanged [9]
ws = Workspace.discover([root])                   # N definitions -> N kernels
ws.kernel_for("PROP-052").transition(...)
```

Proof of expressiveness — the two future backlogs. (HYP declares the *live*
schema; its legacy keys — `freshness_rule`, the revision dates,
`next_observation_blocked_by`, `change` — ride the extras passthrough
exactly as unknown keys do on task cards today, matching the model's
`extra="allow"` [11].)

```yaml
# tracker/hypotheses/backlog.yaml
name: hypotheses
prefix: HYP
fsm:
  initial: active
  states:
    - { name: active }
    # closed-date stamping = the same stock handler as tasks, configured:
    - { name: confirmed, on_enter: [{ name: stamp-lifecycle, field: closed }] }
    - { name: refuted, on_enter: [{ name: stamp-lifecycle, field: closed }] }
    - { name: stalled }
  edges:
    - { from: active, to: confirmed, name: confirm }
    # the quorum gate hangs on the exact edge it guards — the §5.1 "second
    # internal consumer", realized. SCOPE (review 2026-07-18): it gates the
    # AUTOMATION actor only (the autoclose sweep); a manual HITL refute
    # passes unconditionally — quorum licenses auto-closure, it never
    # blocks the human (ADR-0009 safe-direction [14]). Quorum counts
    # DISTINCT real session_ids, excluding the auto sentinel [14].
    - from: active
      to: refuted
      name: refute
      handlers: [{ name: hyp-quorum-gate, min_independent_sessions: 3 }]
    - { from: active, to: stalled, name: stall }
    - { from: stalled, to: active, name: revive }
fields: # from hypothesis/model.py [11]
  - { name: hypothesis, type: str, required: true }
  - { name: expected_outcome, type: list, default: [] }
  - { name: observation_window, type: str, default: "" }
  - { name: success_criterion, type: str, default: "" }
  - { name: rollback_condition, type: str, default: "" }
  # complex shapes: type any + mandatory validator (§1 grammar limits)
  - { name: validation_log, type: any, validator: hyp-validation-log, default: [] }
  - { name: exit_criteria, type: any, validator: hyp-exit-criteria }
  - { name: baseline, type: any } # union shape today [11] — validator optional
  - { name: min_sessions_per_bundle, type: int, default: 4 }
  - { name: closed, type: str, default: "" } # written by stamp-lifecycle above
links:
  kinds: # kind name == storage field (HATS-1032)
    - { name: source_task, arity: one, targets: tasks }
    - { name: supersedes, arity: one, inverse: superseded_by }
    - { name: superseded_by, arity: one, inverse: supersedes }
extensions:
  - hyp-verdicts # ambient: append-verdict verb + validation_log ownership
```

```yaml
# tracker/backlog/proposals/backlog.yaml
name: proposals
prefix: PROP
extras: forbid # Proposal is extra="forbid" today [12] — kept declaratively
fsm:
  initial: open
  states:
    - { name: open }
    - { name: accepted }
    - { name: rejected }
    - { name: deferred }
    - { name: duplicate }
  edges:
    # when acceptance is quorum-gated, the handler lands here:
    # handlers: [{ name: prop-vote-quorum, min_votes: 2 }]
    - { from: open, to: accepted, name: accept }
    - { from: open, to: rejected, name: reject }
    - { from: open, to: deferred, name: defer }
    - { from: open, to: duplicate, name: mark-duplicate }
    - { from: deferred, to: open, name: reopen }
fields: # from proposal.py [12]
  - { name: category, type: str, required: true, choices: [rule, skill, code, process, doc] }
  - { name: target, type: str, required: true }
  - { name: description, type: str, required: true }
  - { name: rationale, type: str, required: true }
  - { name: votes, type: any, validator: prop-vote-entries, default: [] } # appended by prop-votes via Delta.fields (§4)
  - { name: failed_session_id, type: str, default: "" } # model is extra="forbid" [12] — declared, not extras
links:
  kinds:
    - { name: related_hypotheses, arity: many, targets: hypotheses }
extensions:
  - prop-votes # vote verb + votes ownership; acceptance quorum when ratified
```

Declared non-decisions (defaults assumed, owned by the migration epic): HYP/
PROP storage layout (flat `HYP-NNN.yaml` → assumed dir-per-card) and the card
filename (assumed `task.yaml` everywhere — uniform globs, zero code forks —
despite the name reading oddly for a hypothesis).

### 6. Lossless mapping of the current surfaces

| Today                                       | In `backlog.yaml`                                  | Change                                                                    |
| ------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------- |
| `fsm.yaml` `initial` / `states`             | `fsm.initial` / `fsm.states`                       | none                                                                      |
| `fsm.yaml` `edges` (adjacency map)          | `fsm.edges` (edge objects)                         | shape only; same edge set                                                 |
| — (none)                                    | `fsm.edges[].name`                                 | additive, optional                                                        |
| `links.yaml` `kinds[]` (name == storage     | `links.kinds[]`                                    | verbatim (HATS-1032 shape); + optional                                    |
| field, arity, inverse, derived, aliases)    |                                                    | `targets` (cross-backlog), `handlers`                                     |
| `DEFAULT_PLAN_SECTIONS` (name, required)    | `plan-sections.yaml` (NOT in backlog.yaml)         | code table → the plan extension's own file                                |
| `TaskCard` typed fields                     | `fields[]` + engine anchor                         | T3: title-only-required core; `validator:` slot declared                  |
| `resolver` prefix (ai-hats.yaml)            | `prefix` (+ instance override)                     | tasks keeps ai-hats.yaml override                                         |
| `standalone_extensions()` code kit          | `on_enter`/`handlers` + ambient `extensions[]`     | binding moves into the definition; factories stay code                    |
| extension self-subscription (`_edges_into`) | loader-built subscriptions from §3 slots           | ambient extensions keep `subscriptions()`                                 |
| `done--execute` gate exception (code)       | `skip: [plan-gate]` on the reopen edge             | hardcoded exception → declared                                            |
| ownership release on LEAVING execute [13]   | `states[].on_exit`                                 | new slot; full exit product, forced exits covered                         |
| `_stamp_lifecycle` kernel hardcode [4]      | stock `stamp-lifecycle`/`clear-lifecycle` handlers | kernel slimming (T5); `completed_at`/`final_state` become declared fields |
| `REQUIRED_STATES` in `fsm.py`               | `requires_states()` + vocabulary-as-config         | owner moves; invariant survives                                           |
| event keys `edge:<from>--<to>`              | canonical, unchanged                               | named alias keys additive                                                 |

### 7. Migrating backlog-manager onto the rack

The full `ai-hats task` surface maps as follows. The cutover itself is already
decided — K6 (HATS-1026) closed with "rework → cutover", and the execution
(C1–C5 blockers, PROP-070 on-disk migration, skill/role re-pointing) is
HATS-1038; this section maps the *backlog-manager* surface onto the rack so
that work and the multi-backlog phases compose, not conflict:

| Tracker verb                  | Rack home                                                                                                                         |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `create`                      | `create` — required inputs from `fields` schema (T3)                                                                              |
| `show`                        | `context` (HATS-1031 parity)                                                                                                      |
| `list`                        | `ls`                                                                                                                              |
| `log`                         | `transition <ID> --log` (composite op, no state change)                                                                           |
| `transition`                  | `transition <ID> --state` (+ named-edge verbs via T4, optional)                                                                   |
| `update`                      | schema-driven update verb from `fields` (T4 verb-builder; today's gap)                                                            |
| `link` / `unlink`             | `transition --link/--unlink` (HATS-1030)                                                                                          |
| `close` (fast-close)          | forced `transition --state done` with reason — events FIRE (today's tracker                                                       |
|                               | bypass is silent [10] §3; the rack path is strictly more auditable)                                                               |
| `sync` (STATE.md)             | `DerivedViewsExtension` (post-lock reaction)                                                                                      |
| `plan-extract`                | port as a verb over the doc store (follow-up task)                                                                                |
| `attach add/list/show/remove` | `transition --attach/--rm`; list/show ride `context` documents                                                                    |
| `attach verify`               | doc-store integrity verb (K2); frozen drift already marks in `context`                                                            |
| `hyp create/list/show`        | hypotheses instance: `create` / `ls` / `context`                                                                                  |
| `hyp set-status`              | `transition` along named edges (confirm/refute/stall/revive) — gains an FSM                                                       |
| `hyp append-verdict`          | `hyp-verdicts` extension verb (owns `validation_log`)                                                                             |
| `hyp autoclose`               | extension sweep verb under the automation actor; the edge gate licenses it — manual HITL refute stays ungated (§5, ADR-0009 [14]) |
| `hyp migrate`                 | one-shot data migration (migration epic)                                                                                          |
| `proposal create/list/show`   | proposals instance: `create` / `ls` / `context`                                                                                   |
| `proposal status`             | `transition` along named edges (accept/reject/defer/…)                                                                            |
| `proposal vote`               | `prop-votes` extension verb (owns `votes`)                                                                                        |

Phasing (each phase lands independently; order = dependency order):

1. **T6** — this ADR ratified (HATS-1034).
2. **Unified loader** — `load_backlog()` → `BacklogDefinition`, the packaged
   default `backlog.yaml` ships, `fsm.yaml`/`links.yaml` fold in and are
   deleted, `extensions:` factory registry (single-backlog; new task).
3. **T3 + T4** — title-only core, schema-driven create/update (HATS-1035),
   verbs/ package with `on_user_schema` (HATS-1036) — both built against
   `BacklogDefinition` from phase 2.
4. **Cutover** (HATS-1038, per the K6 verdict): C1–C5, PROP-070, the
   backlog-manager *skill* and role wirings re-point to the rack CLI. Needs
   phases 2–3 only for the surface it re-points to — no multi-backlog.
5. **Multi-backlog** — `Workspace.discover`, cross-backlog `targets`, the
   HYP/PROP definitions above + their extensions (`hyp-verdicts`,
   `hyp-quorum-gate`, `prop-votes`) + one-shot data migration (flat →
   dir-per-card). The "separate epic after cutover" from HATS-1014's
   out-of-scope note; new task(s), scope-gated by the supervisor.

## Consequences

**What survives of §5.1 [10].** The stability argument was "a hook written
against `edge:plan--execute` must not break under a user-editable topology."
It holds: (a) canonical positional keys are untouched and names are additive
aliases; (b) the packaged tasks default remains the in-package SSOT — editing
it is still editing the kernel contract; (c) a per-backlog `backlog.yaml` is
a *versioned contract of its own event vocabulary* (a repo file under review,
like the composition configs), not runtime tuning. What §5.1 called "the
generic table-driven interpreter over `{entity, states, edges}`" is precisely
`BacklogDefinition` + the unchanged `fire(from, to, card)` dispatcher.

**T3 is unblocked**: requiredness/defaults/choices come from `fields`; the
kernel anchor shrinks to id/state/title/work_log/timestamps + extras.
**T4 is unblocked**: `on_user_schema` reads `BacklogDefinition` (fields for
create/update options, named edges for transition verbs, link kinds for
`--link` completion).

**Costs.** A new load-order step (definition before kernel) on every CLI call
— one small YAML per backlog, negligible against the existing per-card IO;
one more concept (workspace) in the mental model; extension factories become
a registry (one dict, but a second place to look). Extension attachment
moves from "read the code" to "read the file + the registry" — the file is
the more reviewable of the two, though the integrator code-channel (§4)
means the file alone is not the whole picture. New machinery carried by the
review round: the priority band for declaration-bound handlers, the link
mirror dispatch, `Delta.fields`, and the handler timeout — each bought by a
named requirement (ordering safety, both-sides links, transactional field
ownership, lock liveness), none speculative.

**Rejected alternatives.** One multi-tenant kernel (routing inside the kernel
— against kernel slimming; interview A). Fields declared by extensions
(schema scattered across code; T4 would need a collection pass; interview B).
`extends:`/includes between definitions (no second consumer of shared
fragments yet; interview C). Path-only backlog identity without declared
prefixes (id routing would need directory scans; collisions undetected;
interview D). A top-level `sections:` in backlog.yaml (extension config does
not belong to the backlog definition; review 2026-07-17). Purely edge-bound
gates without `on_enter` (a forced non-topology entry would bypass the gate —
the HATS-518 hole §3 closes). Extension self-subscription as the only binding
(the file would say which extensions exist but not where they act — the
review's inconsistency, resolved by declaration-side handlers). An
instance-overlay/`extends:` mechanism for integrator attachment (the code
channel covers it with zero merge semantics; review 2026-07-18). In-lock
both-sides link dispatch (nested card locks — forbidden by the one-lock
rule; the post-lock mirror event is the safe form).

## References

- [1] `packages/ai-hats-rack/src/ai_hats_rack/fsm.yaml`, `fsm.py` (`REQUIRED_STATES`, `load_topology`)
- [2] `packages/ai-hats-rack/src/ai_hats_rack/links.yaml`, `registry.py` (`load_registry_for`)
- [3] `packages/ai-hats-rack/src/ai_hats_rack/extensions/sections.py`, `extensions/plan.py`
- [4] `packages/ai-hats-rack/src/ai_hats_rack/kernel.py` (`Kernel.__init__`)
- [5] `packages/ai-hats-rack/src/ai_hats_rack/linked.py` (`link` target-existence check)
- [6] `packages/ai-hats-rack/src/ai_hats_rack/dispatch.py` (`Subscription`, `Subscriber`, `Delta`), `events.py` (`EdgeEvent.key`)
- [7] `packages/ai-hats-rack/src/ai_hats_rack/extensions/__init__.py` (`standalone_extensions`)
- [8] `packages/ai-hats-rack/src/ai_hats_rack/cli.py` (`_kernel`)
- [9] `packages/ai-hats-rack/src/ai_hats_rack/resolver.py` (`resolve_root`, `RackRoot`)
- [10] epic HATS-1014 attachment `hats-1014-fsm.md` §3 (transition bypasses), §4 (fail-closed materialization), §5.1 (YAML fork + generalization trigger)
- [11] `packages/ai-hats-tracker/src/ai_hats_tracker/hypothesis/model.py` (`HypothesisStatus`, field set)
- [12] `packages/ai-hats-tracker/src/ai_hats_tracker/hypothesis/proposal.py` (`ProposalStatus`, field set)
- [13] `src/ai_hats/rack_wiring.py` (worktree/ownership adapters, `_keys_leaving_execute_or_terminal`, priority chain, `bind`)
- [14] `packages/ai-hats-tracker/src/ai_hats_tracker/hypothesis/quorum.py` (independent-session quorum, auto sentinel) + `docs/adr/0009-quorum-autoclose-safe-direction.md` (safe closure direction)
- [15] `packages/ai-hats-rack/src/ai_hats_rack/models.py` (`_capture_extras` passthrough)
- [16] `packages/ai-hats-rack/src/ai_hats_rack/extensions/epic.py` (`decide`, `RESOLVED_STATES`/`ACTIVE_STATES`, advance chain, `bind`)
