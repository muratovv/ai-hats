# ADR-0019: Declarative lifecycle-extension model for skills

## Status

Accepted (HATS-1139, 2026-07-24; last revised at rev 10 — HATS-1545, 2026-08-10;
the text was brought in line with rev 10 by HATS-1571, 2026-08-11).
Governs epic **HATS-1138** — the declarative mechanism (HATS-1152 → 1140 → 1241
→ 1141 → 1142 → 1143), its consumers (HATS-1137 merge-correctness gate,
HATS-1144 hunk-review) and the re-bindings (HATS-1145, HATS-1146). The substrate
underneath — retiring `lifecycle_hooks`, the hook-execution primitive, the
channel postures and the git_hooks orchestrator (epic **HATS-1266**, which runs
first per D8) — is **ADR-0020 [4]**, split out of this document's rev 7 during
review. Driver: HATS-1134 (incident HATS-1130).

**It became `Accepted` at rev 8 (HATS-1540), on the condition it set itself:** a
live consumer bound and proven to refuse, not merely a merged mechanism. The
`maintainer` role binds `maintainer-quality-gate/hooks/done-gate.sh` twice — once
under `apps.rack.tasks` at `edge:review--done`, once under `apps.wt` at
`pre-merge` (the rev-10 spelling) — and both refusals are asserted
against the real binary — each one also asserted to flip to a pass when the row
is removed (`tests/e2e/test_done_gate.py`).

It took two attempts. HATS-1137 bound the edge half; HATS-1538 withdrew it an
hour later, for two reasons this rev closes — see D9 clause 2 (a session that
predated a binding had no root to resolve from) and D7 (role scope is not
backlog scope — since the 2026-08-11 amendment the two are made to coincide,
because the role that scopes a binding is now the backlog's own).

**Renumbered from ADR-0018.** This decision was drafted as ADR-0018 in
`HATS-1139/design.md`. Cross-epic coordination (2026-07-24) assigned **ADR-0018**
to the provider **artifact-builder** (HATS-1170, epic HATS-1165) — materialization
is the foundational substrate — and this lifecycle-extension model follows it as
**ADR-0019**. (The 2026-07-24 wording said the lifecycle handlers "resolve from
that materialized per-session skill tree". **Rev 7 reverses exactly that** — see
D9.) Historical "ADR-0018" references in the epic HATS-1138 backlog
(work_log entries, closed cards such as HATS-1160 / HATS-1149) predate the
renumber and point here.

**Revision history.** Rev 1 put the attachment point in `SKILL.md` (kept as
*Alternative E*). Rev 2 added a named skill capability bound by the role (kept as
*Alternative F*). Rev 3 dropped the skill side entirely: **only traits/roles
change.** Rev 4 folded in an adversarial review (exit code, reversed open question
3, factual corrections). Rev 5 removes materialization altogether — see **D9**,
which supersedes rev 4's role-keyed-materialization mitigation. Rev 6 resolves the
last open item: the `plan_sections` consumer channel is **deleted** (HATS-1149,
option A; implementation HATS-1160). **Rev 7 (HATS-1240, 2026-07-27) rewrites
D9**: rev 5–6 resolved bindings out of the *provider's* per-session skill tree,
which by then existed only for claude — see D9 for what falsified it. Rev 7 also
amends D6, **inverts D8** (the retirement of `lifecycle_hooks` now leads the
migration instead of trailing it, and closes by tombstone rather than a
deprecation window), added **D10** (channel taxonomy) and corrects a stale Risk
paragraph in *Consequences* that rev 5 had already superseded. **During the
same review (2026-07-29, supervisor)** D10 — with its `detached` contract
re-cut to a fail-open dispatcher — and the mechanics halves of D4/D5 were
**split out to ADR-0020 [4]** before rev 7 merged, so this document carries
the extension model only. **Rev 9 (HATS-1541, 2026-08-10) splits ownership of a
point between the integrator and the application that owns the point** — see the
new **D11**, and the paragraphs D3 and D9 lost to it. Nothing about the DSL
changed at rev 9: the `checks:` mapping and every point name were exactly what
rev 8 shipped. **Rev 10 (HATS-1545, 2026-08-10) then replaced that DSL**: the flat
`checks:` list became `composition.apps.<app>`, with the application's own grammar
below the app key, `run: <skill>/<path>` in place of the `skill:`/`script:` pair,
and `at:` in place of `on:` (YAML 1.1 reads a bare `on` as `True`). A config still
carrying `checks:` now gets a typed refusal. **HATS-1571 (2026-08-11) carried no
decision**: it corrected the prose rev 10 left behind — the D3 table, D6, the
dedup key, *Consequences*, and the claim that ai-hats validates point names for
two namespaces. Where this document describes a behaviour a currently open card
will change, that card is named at the paragraph.

## Context

> **This section is the world of 2026-07-23, kept as the record of why the
> decision was taken; read its present tense as past.** What it describes has
> since been acted on: `lifecycle_hooks` is gone (HATS-1147), `Composition`
> carries `apps` (rev 10), and the missing precondition point on `merge` exists
> (HATS-1540). The *Decision* sections below describe today.

### The accretion

A skill declares hook wiring in `SKILL.md` frontmatter under `ai_hats:`. Four
channels accreted there, each with its own shape, its own validation posture —
and its own **collection scope**:

| channel           | shape                            | unknown key                | collected                               |
| ----------------- | -------------------------------- | -------------------------- | --------------------------------------- |
| `git_hooks`       | `{event: [script]}`              | silently skipped           | per-role, over a `CompositionResult`    |
| `runtime_hooks`   | `{event: [{matcher, script}]}`   | fail loud                  | per-role                                |
| `worktree`        | `{wt_in/wt_out: [{script, on}]}` | leaf loud / container warn | per-role                                |
| `lifecycle_hooks` | `{edge: [script]}`               | fail loud                  | **union over ALL skills of ALL layers** |

Three answers to "what do we do with a typo?" and two answers to "who is this
for?" is the tell: these grew one incident at a time, not from a model.

### The structural gap

```python
class Composition(_YamlModel):
    traits: list[str]
    rules:  list[str]
    skills: list[str]
```

Composition expresses *"attach this component"* and nothing else. There is **no
way for a trait or role to say where a script binds**. Every channel bakes the
attachment point into the skill — not because that is right, but because it was
the only expressible place.

Two live consequences:

1. **A skill cannot be reused under a different policy.** `hunk-review-comments`
   hardcodes its own attachment; a project wanting it on another edge, or
   advisory rather than blocking, must fork the skill.
2. **`lifecycle_hooks` escapes composition entirely.** Declaring an edge gate
   fires it for *every role in the project* — neither *whether* nor *where* is
   controllable.

### The trigger

HATS-1134 needs a guard refusing `merge` while un-addressed review notes exist,
on **both** the rack FSM path and the direct `ai-hats wt merge` path. The latter
has **no extension point at all** (`wt_in` fires at create, `wt_out` at teardown —
*after* the merge commit exists; there is no precondition point).

### The goal

> «пользователи смогут подключать свои скрипты в fsm и получать свое кастомное
> поведение»

Needs two things the 2026-07-23 model could not express: a **named set of
attachment points**, and a **binding layer the composer owns**. The second is
what shipped. The first was answered differently than this line expected: there
is no single catalog, because D11 and rev 10 put the point vocabulary with the
application that fires it — ai-hats knows the names of one app's points, its
own (`wt`), and carries every other app's verbatim.

## Decision

### D1 — Unify the *lifecycle* domain only

Covers **rack FSM edges and worktree lifecycle**. Does **not** absorb
`git_hooks` (git's event domain, installed to `.githooks/`) or `runtime_hooks`
(provider domain, wired into `settings.json`) — different execution substrates
with different lifetimes; folding them in buys symmetry at the cost of coupling.

### D2 — The binding lives in the trait/role. Skills do not change

One new field on `Composition`; **no new `SKILL.md` frontmatter**:

```yaml
# Illustrative — a trait in a user-scope library. The skill it binds lives
# outside this repo, so no in-tree path is shown; the one binding this
# repository ships is in `usage/roles/maintainer/config.yaml` (HATS-1540).
name: hunk-review-trait
composition:
  skills: [hunk-review-comments]
  apps:
    rack:                                                          # application
      tasks:                                                       # its backlog
        - run: hunk-review-comments/hooks/check-review-availability.sh
          at: [edge:review--done]                                  # rack's cargo
          on_error: warn
    wt:
      - run: hunk-review-comments/hooks/check-review-availability.sh
        at: [pre-merge]
        on_error: warn
```

*(HATS-1545 replaced the flat `checks:` list with this shape. The application is
a **key**, so ai-hats routes a row without knowing any application's namespaces.
It owns three keys — `run:`, `at:` and `on_error:` — and carries every other key
verbatim. `at:` is owned but never *interpreted*: ai-hats checks only that a row
names at least one point, since a row bound to nothing is a gate that never
fires and neither side would otherwise be looking (HATS-1545 F3); what each name
MEANS stays the owning application's question.
Depth below the app key belongs to the app — `rack` puts the backlog it gates
there, `wt` has one namespace and puts rows directly under its own key, and
ai-hats checks neither. `at:` rather than `on:` because YAML 1.1 resolves a bare
`on` to `True`; the old channel remapped that for one known field, which is
impossible under an opaque block. The old key is retired rather than translated,
and a config still carrying it gets a **typed refusal** naming where the rows
moved — the strip-unknown WARN would drop a declared gate and carry on, which is
the silence this channel exists to remove (supervisor ruling 2026-08-10).)*

The script stays where scripts already live — in the skill directory, resolved
skill-relative exactly like `git_hooks` / `runtime_hooks` / `worktree` entries.
The skill ships *capability*; the composition decides *policy*.

What this buys against the two consequences above:

- **Reuse without forking.** The same script binds to different points, with
  different failure policy, per trait/role/project.
- **Role control.** A role composing the trait is gated; one that does not, is
  not. Unexpressible in Rev 1.
- **One mechanism.** Bindings resolve through the composer that already handles
  traits/rules/skills — same overlay precedence, then this channel's own dedup:
  **first declaration keeps the slot, the strictest `on_error` wins, and a second
  declaration of the same row warns** rather than replacing it (not last-wins —
  a later relaxation must not disarm an earlier gate).
  `lifecycle_hooks`' union-scope special case disappears rather than being
  extended to a fifth channel.
- **Referential integrity.** `run:`'s first segment names the skill; naming a
  skill nobody composed is a loud composition error (a skill an overlay
  *removed* is a warning — D6). The rest of `run:` is the path inside that
  skill's directory, health-checked at composition for **containment** (it must
  resolve inside the declaring skill's directory), exists, non-empty, shebang
  and exec bit, per **D6**. *(Rev 7: this bullet used to attribute that check to "the existing
  `_health_check`". Rev 4 recorded that the existing one never examined the exec
  bit, but corrected only its own section and left this sentence standing — the
  check D6 now specifies is new, not inherited.)* Binding does **not** implicitly
  pull the skill in — implicit composition is how you get surprise gates.

Deliberately **not** added: a skill-side `provides:` block naming each check.
It would be `{name, script}` — a rename of a path — after which the trait repeats
that name. Duplicate authoring for indirection no current consumer needs
(`design-minimalism`). A moved script is caught loudly by the health-check, not
silently.

### D3 — The points, and who owns each one

**There is no single catalog.** Since rev 9 (D11) the point vocabulary belongs to
the application that fires the point, and since rev 10 the namespace prefix *is*
the `apps.<app>` key. So a point is now identified by two things — the app key it
sits under, and its `at:` name — and the three columns that matter are who
validates the name, who fires it, and whether a row there can veto.

| `apps.<app>` | `at:`               | when                                                                                          | may veto | name validated by                   | fired by                      |
| ------------ | ------------------- | --------------------------------------------------------------------------------------------- | -------- | ----------------------------------- | ----------------------------- |
| `rack`       | `edge:<from>--<to>` | rack FSM transition, in-lock, prio 15 — before ownership claim (20) and worktree effects (30) | yes      | rack, against the topology it runs  | `CheckSubscriber` (HATS-1141) |
| `rack`       | `card:pre-create`   | card creation (`rack create`) — **not** an FSM edge: the card does not exist yet              | —        | **nobody**                          | **nobody** (HATS-1578)        |
| `wt`         | `pre-merge`         | in `merge()`, before **any** mutation — **after** `_check_clean` / `_check_drift` / consent   | yes      | ai-hats, `check_points.wt_points()` | `wt_lifecycle.py` (HATS-1540) |
| `wt`         | `pre-reclaim`       | before a worktree is reclaimed                                                                | yes      | **nobody** — not in `wt_points()`   | **nobody** (HATS-1145)        |

`wt` `create` and `teardown[merge|discard|cleanup]` were rows of this table until
HATS-1577. Both were validated by `wt_points()` and fired by nobody, so a role
could arm one and be told nothing — the very defect the table is drawn to expose.
They are not recorded here as *planned*, because the lesson is that a name in the
catalog is a promise: HATS-1146 adds them back together with the call site.

Two independent facts the table separates, because conflating them is how a gate
goes silently missing:

**Who validates the name.** ai-hats validates `at:` names for the apps it fires
itself — `wt` via `check_points.wt_points()` and, since HATS-1581, `ai-hats` via
`ai_hats_points()` — where a typo is refused at composition and `on_error: warn`
is refused wherever the point protects data. Every other app's names are opaque
cargo: `edge:` and `card:pre-create` are the rack's, and ai-hats does not look at
them (`owns_app` is `app in _OWNED_POINTS`). The rack in turn
validates `edge:` against the topology it is running, but a name its grammar does
not parse — `card:pre-create` among them — is currently skipped in silence rather
than named; **HATS-1578 owns that gap.**

**Who fires it.** Of the four rows, **two have a caller**: `edge:` through
`CheckSubscriber`, subscribed at `Phase.IN_LOCK` priority 15 to every edge key of
the topology the kernel runs; and `wt` `pre-merge`, inside `merge()` after the
cheap local guards and before every mutation, which is where the `maintainer`
role binds. The other two are callerless in **different** ways, and telling those
ways apart is what the table is for. `pre-reclaim` is a planned name
`wt_points()` does not carry, so a row naming it is refused at composition: loud,
and armable by nobody. `card:pre-create` is the rack's — carried unread by
ai-hats, skipped in silence by the rack — so a role can arm it and be told
nothing. **That is exactly the silent no-op this ADR exists to remove**, and it
is live today, not hypothetical. It was live in a second and worse place until
HATS-1577: `apps.wt` `at: [create]` validated cleanly, composed, and never ran.
Owners: **HATS-1578** (the rack point whose name nobody rejects), **HATS-1145**
(`pre-reclaim`, which needs the point before it can have a caller).

Two roads publish to a base branch without firing `pre-merge`, both by
**recorded decision** rather than omission, because this ADR's *Risk* section
demands one or the other. The `merge()` short-circuits (HATS-596 already-merged,
HATS-1370 patch-integrated) tear a worktree down without merging: their content
reached the base by another route, so gating them would refuse a supported
recovery instead of protecting anything. And `cleanup(IsolationMode.SQUASH)` —
the sub-agent teardown — commits to the base and does **not** fire it: `cleanup`
suppresses a lifecycle veto by design (ADR-0013 [3] D8, so a sub-agent's own
error is not masked), so a refusal there would be swallowed and the gate would
look armed while passing everything. Making it non-suppressible is a change to
D8's contract, not to that call site; revisit behaviour and this paragraph
together — that half is carried forward as **Q1** under *Open questions*, which
is where the asymmetry between the two kinds of road is stated. Both decisions
are pinned in `packages/ai-hats-wt/tests/test_wt_pre_merge_point.py` — one test
per short-circuit flavour and one for the squash road
(`test_an_already_merged_branch_does_not_fire_the_point`,
`test_the_patch_integrated_short_circuit_does_not_fire_the_point`,
`test_the_squash_cleanup_path_does_not_fire_the_point`). The patch-integrated
flavour had none until HATS-1595: an audit read its bare `return` as an omission
and filed the unfired gate as a defect, which is what a missing pin costs.
`pre-reclaim` is the one `wt` name `check_points.wt_points()` does not know —
since HATS-1577 the catalog holds `pre-merge` and nothing else, so every other
`wt` name is refused rather than silently accepted. It is listed above, and in
`docs/glossary.md`, so the two agree about what is coming. (It is not the only
unvalidated name in the table: `card:pre-create` is unvalidated by ai-hats *by
design*, being another app's cargo.)

`card:pre-create` is not reachable through the FSM dispatcher: card creation is
not an event at all — the rack's event kinds carry no creation event, and
`Kernel.create` dispatches nothing. The only creation callback is `after_create`,
a provider hook that runs *after* the card exists and takes no subscribers. So
the point needs its own call site rather than a subscription (owner: HATS-1404
for the consumer; HATS-1578 for the question of who owns the name meanwhile).

`edge:` names validate against the topology **the kernel is running** (full state
product — forced transitions fire non-topology edges); the rack does that with
`all_edge_keys(topology)`, and ai-hats validates no edge name at all. *Rev 9
closed the gap that
stood here from HATS-1140 to HATS-1540* — the catalog resolved that topology from
the *packaged* tasks backlog while the kernel ran the catalog-local one
(`resolve_definition`), so a binding to an edge of a sibling backlog — this
repository ships two, for hypotheses and proposals — was refused at composition
as an unknown point, and a name the packaged catalog accepted that the running
topology had no edge for aborted the transition (`check_resolve._guard_topology`).
Both halves are gone: ai-hats no longer holds a topology at all, so the two can
no longer diverge (D11). A point that is no edge of **this** instance's topology
is now simply not this instance's business — it may be a sibling backlog's, and
mistaking one for the other is what made a typo on `edge:reviw--done` abort an
unrelated `edge:brainstorm--plan`.

`pre-merge` is a **precondition**, in the same class as the accepted
`WorktreeDirtyError` / `WorktreeDriftError` / `WorktreeMergeConsentError`
refusals — explicitly *not* the teardown-veto rejected by ADR-0012 [1] / HATS-775,
which fires after the merge commit exists and can only strand a worktree.

`discard` deliberately has no pre-op point: discard is an explicit throw-away.

### D4 — Outcome policy at the binding

The execution mechanics — the exit-code contract (`0` pass / `2` refuse with
the child's stdout tail as the reason / anything else broke, including
`126`/`127`, `>128` and timeouts), stdin, per-caller timeout, decoding and the
shared env base — moved to the execution primitive's contract, **ADR-0020 [4]
D2** (split 2026-07-29; the "refuse is 2, not 1" rationale and its rev-4
history travel with it). This section owns what a *binding* does with the
outcome — the policy layer a `composition.apps` row adds on top of the primitive.

`on_error: refuse | warn`, default **`refuse`** (preserves today's fail-closed
posture). A consumer opts into `warn` explicitly — but **`on_error: warn` is
rejected at composition for `apps.wt` at `pre-merge`**: failure
policy at a data-protection point belongs to the app that owns the point, not to
the binding author. That is enforceable only for ai-hats's own app: the authority
is `check_points.wt_points()`, which maps each `wt` name to whether `warn` is
legal there. For another app's points the equivalent lever, if any, is that app's.
ADR-0012 [1] deliberately put that lever in the engine (`wt_lifecycle.py`) so a
component could not opt out; moving it into user YAML would regress that.

`on_error` governs **only the check's own exit status**. A bound script that is
missing or not executable where the binding says it is, is *infrastructure
corruption* — not a verdict and not a check failure. It fails closed at every
point and **cannot be downgraded by `on_error: warn`**, otherwise every
warn-binding becomes a way to disarm a gate by deleting a file. *(Rev 7: this
paragraph formerly named the `_assert_manifest_intact` backstop as what
`on_error: warn` must not disarm. D9 deletes that backstop along with the
manifest; the rule it enforced is what survives, and D6 now catches the same
corruption earlier, at composition.)*

> **Card drift, recorded once.** HATS-1140's description carried the rev-3
> contract (`1` = refuse) for three days after rev 4 replaced it, and would have
> been implemented from that text. Where a card and this ADR disagree, the ADR is
> authority — the epic's `work_policy` says so, which is only safe because this
> document states the decisions rather than pointing at the cards.

Separating "I refuse" from "I broke" is load-bearing: a check that crashes on a
task with no worktree must not wedge the backlog. The hunk binding declares
`on_error: warn` — a *policy* gate fails open, while *data protection*
(`apps.wt` at `pre-merge`) keeps `refuse`.

### D5 — Check-point env vocabulary

The shared base every hook receives is the primitive's contract — **ADR-0020 [4]
D2** — and it is *implemented (HATS-1151)*, with the colour sanitisation from
HATS-1161. **The base is enumerated there and nowhere else.** This paragraph used
to carry its own list of six keys, and that list went stale the moment the
primitive gained a seventh — `AI_HATS_TASKS_DIR` (HATS-1540), which this very
section goes on to describe under **Backlog context** below while the list above
it never grew. A second copy of a contract is how a set drifts, so the copy is
gone and D2 is the pointer (HATS-1613).

What belongs to this ADR is the *reading*, not the roster: the set-vs-remove
semantics, the removal of an unresolved value from the inherited environment, and
the colour stripping are the **primitive's**, for every channel — rev 4 presented
several of them as this ADR's own additions.

The resolution behind that vocabulary is *implemented (HATS-1540)*, the remainder
of HATS-1142 after HATS-1151 took the shared primitive. Until then only
`AI_HATS_TASK_ID` was computed at `edge:` points, and every gate hand-rolled
`sessions/worktrees/task-<id>.json` → `jq -r .worktree_path`. The runner now
resolves the worktree **once per edge** and hands it to every binding, through a
pure read (`WorktreeManager.peek_worktree_path`) — `load_for_task` unlinks a
record whose tree is gone, and a *refused* transition must leave lifecycle state
as it found it. "Cannot read the record" is **not** "no worktree": the first
refuses, only the second answers. That single resolution is what lets one script
bind to both `edge:` and `wt:` points — the payoff of D2, and it is verified by
running the same file under each point's env, not by reading the code.

**Backlog context** — *implemented (HATS-1540)*. `AI_HATS_TASKS_DIR` names the
tasks dir the transition runs against. A binding fires on every backlog **of the
project that declared it**, and without this a script cannot tell the tasks
backlog from a sibling catalog of the same project, nor "this card is not mine"
from "`AI_HATS_DIR` leaked" — the ambiguity that turned master red in HATS-1538.
Since HATS-1573 the engine no longer hands a script another project's backlog at
all (D7, amended 2026-08-11); what it still states rather than decides is which
catalog *within* the project a card belongs to. No second variable carries
the prefix; it is derivable from `AI_HATS_TASK_ID`. At `apps.wt` `pre-merge` the
variable is absent — that point is not a backlog operation — and
`AI_HATS_BRANCH_NAME` rides instead, this channel's existing spelling.

Existing point-specific variables keep their names; migrated scripts do not churn.

### D6 — Validation: loud on structure, forgiving on vocabulary

- A malformed row, a missing or unparseable `run:`, an empty `at:` → fail loud at
  composition, naming the trait/role and the entry. A gate that silently fails to
  install is the HYP-078 / HATS-961 hole. **An unknown point name is loud only
  where ai-hats owns the app** (`wt`); another app's names are cargo it cannot
  judge — see D3 for where that leaves the rack's.
- **Every key that is not `run:` / `at:` / `on_error:` is carried verbatim as
  cargo, silently.** Rev 5 specified "unknown *optional field* → warn and ignore"
  as the forward-compatibility posture (ADR-0012 [1] Revisions #3); rev 10 got
  the same property structurally instead — ai-hats cannot warn about a key it has
  no opinion on, because the whole point of the cargo is that the grammar above
  `run:` is the application's. Forward compatibility now comes from opacity, not
  from a warning.
- Script health-check keeps `lifecycle_hooks._health_check`'s *posture* (missing
  / empty / shebang-less → loud, "a no-op gate is a broken gate") but moves to
  **composition time**, since D9 removes the materialization step it ran in.
- **Containment**: `run:`'s tail must resolve **inside** the declaring skill's
  directory, so `../../../x.sh` cannot escape. This matters more under D2 than it
  did before, because the binding is authored in a *trait* that need not own the
  skill. *(Rev 5 recorded this as an open hole in the channel it replaced, where
  `collect_lifecycle_hooks` joined `skill_dir / script` unresolved. That module
  is gone with `lifecycle_hooks` itself (HATS-1147), and the check is implemented
  here: both sides are resolved before the comparison.)*

**Amended at rev 7 (HATS-1240):**

- **The exec bit is checked, at composition time.** Rev 5 deferred it to run time
  on the reasoning that "with no copy step there is no `0o755` rewrite". D9 now
  resolves through a copy — the surface's skill mirror, written by `copytree`,
  which preserves mode and never chmods — so a `644` script is dead on arrival at
  every point it is bound
  to. Checking it beside the shebang costs nothing and converts a runtime *broke*
  into an authoring-time error. (Note the rev-5 text also mis-stated the existing
  `_health_check` as validating executability; it never has.) Audited before
  adopting: hook scripts across `library/core`, `library/usage` and
  `ai-hats-custom` are `100755` in the git index with **one exception** —
  `core/skills/worktree-isolation/hooks/wt_entry_gate.py` is `100644`, and it is
  a live declared `runtime_hooks.PreToolUse` binding, not a data file. It works
  today only because the runtime-hook materializer rewrites the mode on copy
  while the surface then executes the copied path bare. So this check would fail
  exactly one binding that works today — and that is the argument for it, not
  against: the exec bit is load-bearing and currently masked by the copy.
  Adopting the check means fixing that file's mode in the same change.
  **Discharged.** The check is implemented, and both halves of the audit above
  have since moved: `wt_entry_gate.py` is `100755` in the index, and the
  materializer no longer rewrites a mode (the one remaining `0o755` in
  `hooks_manager.py` is the git-hooks dispatcher, a different subject).
- **A binding whose skill an overlay removed is a warning, not an error.**
  Rev 5 listed an uncomposed skill among the loud failures. That is right for a
  *typo* and wrong for a deliberate `--remove-skill`, which an overlay may legally
  apply to a trait-brought skill (HATS-1046). Hard-failing would force a user
  making a legitimate removal to author a new role; staying silent would drop a
  gate they expected. So: warn, naming the trait that bound it, drop the binding,
  and continue composing. A skill (`run:`'s first segment) that was never composed
  by anyone remains a loud error — the two cases are distinguishable, because one
  of them is a recorded removal (supervisor ruling, 2026-07-26). *Whether one
  stderr line is enough for "you just disarmed a gate" is open — **HATS-1535**.*

**Added at rev 10 (HATS-1545)**, recorded here because they are part of the same
posture and were not written down when they shipped:

- **A `composition.apps.<app>` block no integration collects → WARN**, naming the
  declarers and the app keys this build does collect. Without it, `apps.rak:` is a
  gate that can never install and says nothing. The roster of collected apps is
  *not* an authority over point names — it only decides whether some integration
  will pick the block up.
- **The same row declared twice → WARN, not an error**, naming both declarers and
  the surviving `on_error`. Refusing would force a project to fork a shipped role
  to add a second declarer; see D2 for the dedup that follows.
- **A non-string key anywhere under `composition:` → typed refusal**, and so is a
  **duplicate key** in a component config. Both are structural: YAML resolves a
  duplicate key to the last value silently, and a bare `on` to `True`, so a
  declaration can be destroyed before any validator sees it. This is the one class
  that refuses rather than warns — you cannot warn about what is no longer in the
  parsed structure.

One posture, replacing today's three.

### D7 — Scope: role-scoped, by composition

Bindings are collected **per-role over the `CompositionResult`**, like the other
three channels. The union special case retires.

The objection this raises — *an agent dodges a gate by switching roles* — does
not apply: **role selection is out-of-band and supervisor-driven; an agent does
not reassign its own role at runtime** (supervisor ruling, 2026-07-23). So no
project-level tier, no `scope:` field, no cross-role lint is needed.

*Re-confirmed at HATS-1545 (supervisor ruling, 2026-08-10), when the DSL was
reshaped and `scope:` was reconsidered as a field. Two reasons beyond the
2026-07-23 one. First, it would open a **second merge axis with no join**:
`on_error` is strictest at `refuse` and a scope is strictest at `project`, and the
two orders run opposite — "the strictest wins" is undefined over the pair. Second,
no consumer wants a cross-project gate. What the reshape does buy is the narrower
half for free: a row now names the backlog it gates (`apps.rack.<backlog>`), so an
unqualified row is no longer writable. Whether a card belongs to **this project's**
tracker at all stays the script's call (`done-gate.sh` compares
`AI_HATS_TASKS_DIR`), because a scratch catalog can carry the same backlog name.
A field arrives when a real consumer does.*

*Amended at HATS-1573 (supervisor ruling, 2026-08-11): **which role** is
role-scoped is now settled by the backlog, not by the caller's checkout. The
premise of the 2026-08-08 P1 ruling — that the engine cannot tell whose backlog
it is, so each script must defend itself — no longer holds: `RackRoot` carries
the backlog's own project, and only that project's composition supplies the
rows. A backlog nobody owns therefore has no bindings and fires nothing, and the
channel says so rather than passing in silence. The script's comparison is NOT
retired: within one project it still separates the tasks backlog from a sibling
catalog (`hypotheses`, `proposals`), which the engine deliberately does not
decide. What it no longer has to catch is another project's backlog.*

*Owns, defined (ruling 2026-08-12): a project owns the catalogs **under its own
tracker** (`<project>/<ai_hats_dir>/tracker/**`) and no others —
`resolver.find_backlog_owner`. Containment, not proximity: answering "who owns
this backlog?" with "the nearest project marker above it", the way
`find_project_root` answers "which project is this cwd in?", made a scratch
catalog anywhere under a home directory that carries a tracker the property of
that home — its role composing gates onto a backlog it never declared, this same
defect one directory further out. A backlog no project owns is a legitimate
state, not a broken one, and is answered with no bindings plus a notice.*

Implementation consequence — see **D9**, which replaces the obvious-but-wrong
answer (make `materialize_lifecycle_hooks()` role-aware and add a role-aware
drift detector). That approach was rejected in rev 5.

### D9 — Composition is the truth; the check root is ai-hats's own, never a provider's

*Rewritten at rev 7 (HATS-1240). The principle below is rev 5's and survives
unchanged; what changed is where a binding resolves.* **D9 answers one question
only — from which bytes a bound script runs.** Who reads the name, who decides
that a point fires here, and who spawns the process is **D11** (rev 9); until
rev 9 both questions were answered by the same module, which is why the catalog
and the running topology could disagree.

**What rev 5–6 said, and why it was adopted.** Bindings are copied nowhere: the
composed skill tree is *already* materialized per session at
`<ai_hats_dir>/.cache/sessions/<sid>/plugin/skills/<skill>/…`, so a binding
the path `run:` resolves to sits directly in it and the runner executes it in place.
That was correct when it was written — claude was then the **only** surface with
a per-session tree.

**What falsified it.** ADR-0018 [2] (epic HATS-1165) landed the artifact-builder and
two further surfaces, each materializing its skills where its own third-party
binary scans:

| surface | per-session skill tree                |
| ------- | ------------------------------------- |
| claude  | `<sid>/plugin/skills/<skill>/`        |
| agy     | `<sid>/rules/.agents/skills/<skill>/` |
| cline   | `<sid>/skills/<skill>/`               |

A resolver keyed on the claude path therefore **silently does nothing under agy
and cline** — the HYP-078 fail-open this ADR exists to remove, reintroduced by
its own design. The error was not the principle "the tree is a cache". It was
borrowing the *provider's* cache instead of owning one.

**D9, restated.**

1. **The resolution source is the composition** — *implemented (HATS-1140)*. Every skill in a
   `CompositionResult` already carries `ResolvedComponent.source_path`, the
   skill's own directory (`ai_hats_core/composition.py`). A binding resolves as
   `source_path` joined with `run:`'s tail. Surface-independent by construction: no code consults
   a provider layout, so no surface can be forgotten.
2. **Inside a session, the root is the surface's own skill mirror** — *implemented
   (HATS-1540)*, asked of the provider through `Provider.session_skills_root`
   (via `composition_seam`, since the check channel is a brick and the
   composition layer is integrator-only — HATS-865). The mirror is the same one
   the agent's own skills and runtime hooks come from: written unconditionally
   at session entry, whole-directory, so sibling data files survive (ADR-0020 [4]
   D1, `bundle`). A session executes bytes frozen at launch and stays isolated
   from a library being edited concurrently; relying on worktree discipline for
   that isolation would be a policy, not a mechanism, and it lapses at merge.

   *This replaces the channel's private `<sid>/checks/` copy (HATS-1241, and its
   relocation HATS-1398).* That copy was adopted because the surfaces' mirrors
   sat at three different paths and a resolver keyed on one did nothing under
   the other two. The fix for that is the accessor above, not a fourth tree —
   and the private copy carried a defect of its own: it held only the **bound**
   skills, so a session started before a binding existed had no root at all,
   `run_hook` returned `CORRUPT`, and every transition in that session was
   refused until restart (the second reason HATS-1538 withdrew the shipped row).
   The mirror holds every composed skill whose `source_path` is a directory on disk — both writers skip one that is not (`skills_dir.py`, `plugin_dir.py`) — so a session that predates a binding resolves, while a binding on a directory-less skill still reaches `run_hook` as CORRUPT.

   Two properties moved, and both are asserted in `tests/test_check_mirror.py`.
   The leaf name is now the composed skill's raw `name` — what every surface
   writes — where this module used to re-derive it with `resolve_namespace`, so a
   namespaced skill (`dev::python` against `dev/python`) resolved to a directory
   no surface had written. And the private copy was first-writer-wins while every
   mirror is wipe-and-rebuild (HATS-1248): a rebuild for a live sid re-copies the
   source, so a check runs exactly the bytes that session's own runtime hooks run.
   A surface that mirrors no skills returns `None` and the channel **refuses** —
   `check_snapshot.legacy_launch_notices` announces that at launch.
3. **Outside a session the root is the live library** — *implemented (HATS-1141)*. `rack transition` from a
   bare terminal, from cron, or via the standalone `rack` binary has no
   `AI_HATS_SESSION_ID` (`session_id()` returns `""` there) and no snapshot, so
   the runner composes the active role from config and resolves from the library.
   The two modes are not a blemish: the non-negotiable property is that the
   out-of-session mode is **live**, never **absent**. Fail-open there would leave
   HATS-1137 one `env -u AI_HATS_SESSION_ID` away from useless.
4. **A task worktree is never a resolution root** — *implemented (HATS-1141)*.
   `builtin_library_root()` is worktree-aware, so a naive resolve inside a
   worktree would run the branch's own half-written check — a gate judging the
   change it is part of. Note that clause 2 does **not** discharge this on its
   own: a snapshot copies whatever `source_path` points at, so a session started
   inside a worktree would freeze that branch's bytes. The guard belongs to the
   resolver, and sits there: `check_resolve.reject_worktree_root` walks up from
   the path the *composition* resolved (`source_path` joined with `run:`'s tail) to the nearest
   `.git`. A `.git` directory is the main checkout and resolves. A `.git` *file*
   is either a linked worktree or a submodule working tree, told apart by the
   `gitdir:` it carries — `…/worktrees/<id>` is refused, `…/modules/<path>` is a
   vendored dependency and resolves. The guard runs in both modes and **before**
   the root is picked, which is the only place it can bite: rebased first, it
   would inspect the mirror copy under `<cache_root>` — outside every checkout,
   so inside nothing — and clause 2 would smuggle the branch's bytes past it.
   HATS-1540 moved it ahead of the mirror lookup for every binding, so the
   message that refuses names the worktree rather than whatever the surface
   lookup happened to say.

**The out-of-session cost, measured rather than feared.** Rev 5 priced this path
off the `~99 SKILL.md parses` figure from the HATS-1149 research. That is the
cost of the **union scan over the whole library**, which **D7 abolishes**: a
per-role composition touches only the composed set. Measured on this repository:
**42 ms** for a 35-skill, 13-rule role, against a `HOOK_TIMEOUT` and a rack
`LOCK_TIMEOUT` of 30 s. No new machinery is owed either — `composition_seam`
already composes a role from config for `--dry-run`.

**What is still deleted** — unchanged from rev 5: the copy into `<event>.d/`; the
flattened `<skill>-<basename>` managed name and its collision rule; the
`.manifest`; the `previous - new_names` sweep; `_assert_manifest_intact`; the
role-stamped manifest and the role-aware drift detector.

**What a per-session copy does *not* bring back.** Every hazard rev 5
dissolved — the sweep-vs-manifest wedge, last-writer-wins mis-gating between
concurrent roles, silent disarmament via `materialize(result=None)`, the
`config set-role` window, version-skew freezing another role's gates — was an
artifact of a **persistent, shared, project-level** managed directory. A
per-session directory has one writer, does not outlive the session, and cannot
desynchronise from the composition that produced it. Copying is not what made
those hazards; sharing was.

Rev 4's role-keyed materialization stays rejected, and for a sharper reason than
rev 5 gave: **one session composes exactly one role *and* launches exactly one
provider** — HITL and every sub-agent each mint their own session id — so the
session already is the key, and a session never holds two provider trees at once.

**Rejected here, and why it belongs elsewhere:** a single ai-hats-owned skill
tree that all surfaces *deliver from* rather than each materializing its own.
Its only real argument is layering — "materialization is core, delivery is
surface" — which is not this ADR's question. It currently has **no owner**:
HATS-1217 is `done` having refuted its own premise (it delivered the ADR-0018
§2.2 movability test, not a tree consolidation), and the convergence it split
out, HATS-1271, is `done` too — it unified the copier *function* across agy and
cline but left the three destination paths intact. The layering question wants a
fresh card under epic HATS-1092. It would also be a cross-package refactor of
three surfaces behind the published `ai_hats.providers` entry point, and it
would rest on undocumented symlink behaviour of three third-party scanners.
Nothing is owed to it later: a binding names `run: <skill>/<path>`, not an absolute path, so
whoever adopts that shape moves only the resolution root.

### D11 — ai-hats carries the declaration; the application that owns the point parses it

*New at rev 9 (HATS-1541).* D9 fixed **which bytes** run. This fixes **who
decides that they run at all**, and it moves a responsibility rather than adding
one.

**What was wrong.** *(Past tense throughout: both symbols named here were deleted
by this rev.)* ai-hats held the grammar of a point it does not own.
`check_points.known_points()` built the `edge:` half of its catalog from the
**packaged** tasks backlog while the kernel ran the resolved one, and
`check_resolve._guard_topology` existed only to *name* that divergence — it could
not tell a typo from a point addressed to a sibling backlog, because from
ai-hats's side the two are the same fact: a name its catalog does not hold.
Three silences shared that root, and all three were measured on 2026-08-09
before this rev was written: a binding on `wt:`/`card:` validates and never
fires; the catalog validates against a topology nobody runs; and one typo
(`edge:reviw--done`) aborted an unrelated edge (`edge:brainstorm--plan`), which
is the "every transition refused" symptom that made HATS-1538 withdraw the
shipped row an hour after HATS-1137 landed it.

**The split.**

1. **ai-hats is the carrier.** It composes the role, tags every row with the
   component that declared it, resolves `run:` to an absolute path and
   proves that path can run (containment, exists, non-empty, shebang, exec bit —
   D6), applies D9's root rule, and hands the row over. It validates the `at:`
   names of the **apps it fires itself** — `wt` and, since HATS-1581, `ai-hats`,
   whose call sites are its own code — against `check_points.wt_points()` and
   `ai_hats_points()` respectively. Every other app's cargo it carries
   verbatim: what the name means is not its question. *(Rev 9 wrote this clause as
   "`card:`, `wt:`". That was already wrong when rev 10 moved cards under
   `apps.rack`: `card:pre-create` is the rack's, and nothing validates it — see
   D3.)*
2. **The rack parses, filters and subscribes.** `edge:<from>--<to>` is the
   rack's grammar; the topology it validates against is the one
   `resolve_definition` gave the kernel, the same object the dispatcher runs.
   Two different misses, two different answers, and rev 10 added the loud half:
   - **The backlog address is loud — on the edge the row names** (HATS-1576). A
     row at the wrong depth under `apps.rack`, or naming a backlog no mounted
     instance answers to, is a typed refusal that names what *is* mounted and how
     to fix it (`cli_alias`). Asked ahead of the point filter, as rev 10 did, that
     refusal fired on *every* edge: a project whose backlog is `blog` mounts no
     `tasks`, every shipped row is unaddressable, and the tracker stops whole —
     `--force` included, since it travels inside the request built afterwards. A
     backlog is addressed by its `name` **or** its `cli_alias` (ADR-0017 §3).
   - **The point name is quiet.** A point that is not an edge of **this**
     instance's topology is skipped, not aborted, because from the carrier's side
     a sibling backlog's row and a typo are the same fact. That was the whole of
     the third silence, and telling the two apart is what the only holder of all
     the topologies can do — which since HATS-1584 **is done, in `rack doctor`**:
     `checks.classify_bindings` judges every carried row's points against every
     mounted topology and calls the miss `foreign` or `dead`, and `dead` is a
     finding. What is still quiet is the *transition*: the subscriber holds one
     topology and goes on skipping. Refusing there is **HATS-1578**, and it now
     has the distinction it was blocked on.

   Two gaps stood behind this clause and are **both closed**: a row addressed to
   a **sibling** backlog reached no subscriber on either road until HATS-1575
   made the rack build the subscriber from the definition it runs, and a project
   renaming its tasks backlog without `cli_alias: tasks` turned the loud half
   into a refusal on every transition until HATS-1576 moved the address check
   behind the point filter.
3. **The rack does not execute.** `subprocess` is forbidden in it by an
   AST-level import pin (`packages/ai-hats-rack/tests/test_import_hygiene.py`),
   not merely by a docstring. So the row travels as a declaration and the
   *executor* travels as a port: the rack calls back through
   `ai_hats_rack.checks.CheckPort`, whose implementation is ai-hats's
   `run_hook` (ADR-0020 [4] D2). The rack owns the deadline — `LOCK_TIMEOUT` is
   its constant — and passes the per-check budget across the port instead of the
   two sides each keeping a copy that can drift.
4. **"Subscribed, but no executor" is decided per row, not per process.**
   Probing is `getattr` on the port, and a port that is absent or older than the
   Protocol must not raise `AttributeError` inside the lock. A missing executor
   refuses the rows that said `on_error: refuse` and warns past the rows that
   said `on_error: warn`. Refusing everything would brick the bare rack; passing
   everything is the exact silence this ADR exists to remove, so neither answer
   is applied uniformly. The other half of the same skew — a port that cannot be
   asked for the declarations at all — has no rows to consult, so it cannot be
   decided per row; it writes the reason to the work log and lets the transition
   through. Answering "nothing was declared" there would delete every gate with
   nothing written anywhere, which is the same silence one level up.
5. **A bare rack cannot see a declaration at all, and that is recorded, not
   fixed.** Both delivery roads run through the integrator — the port is loaded
   from an entry point the integrator registers, and the composition that
   produces the rows is integrator-only (HATS-865). A project-level artifact
   would change that and is forbidden by ADR-0021 [5] M5 (the content of a
   project may not depend on which role was launched). For a bare rack the state
   is therefore "nothing was declared", not "an executor is missing"; it is
   indistinguishable from the truth in the only case that can arise, since with
   no integrator there is nothing to declare with.

**The trade this makes, named rather than masked.** `check_points.py` opened
with "Loud by construction … **at composition**", and for `edge:` that is no
longer true: a name the rack does not recognise surfaces the next time the rack
runs, which for an unbound point may be never. Validation moved from
**compose-time to consume-time**, and it bought the three silences above. The
compensation is introspection — a command that reports what was picked up and in
what state. **Both halves have since landed.** HATS-1548: `describe_checks`
resolves every binding the way the session will and reports where each one runs
from, wired into the launch report and `ai-hats --dry-run`. HATS-1584: `rack
doctor` grew a binding section — one line per point of every carried row, with
the roster read through the same `CheckPort` a transition runs, and `dead` /
`unaddressed` as findings. It is a *section of the existing verb*, not the
`rack --doctor` this rev imagined: one character apart from `rack doctor` and a
different subject. The two statuses this rev asked for and the doctor does not
carry are `foreign-project` and `stale-session`, and deliberately: it runs
**outside** a session, where there is no mirror to be stale against — that is
what `describe_checks` answers at launch.

Honestly stated, what remains: the doctor is a surface an operator must *run*.
Firing it at session start (**HATS-1583**) is what makes a typo unmissable rather
than merely findable, and refusing the transition itself is **HATS-1578**. Until
the first of those, a typo in an `edge:` point is silent on every transition and
loud only when asked.

**Rejected: teach `known_points()` to resolve the project's topology** *(the
function no longer exists — this rev deleted it; kept as the rejected shape)*
(this was
card HATS-1534, cancelled into this one). It closes one silence of three — the
catalog would stop validating against a topology nobody runs — and leaves
`_guard_topology` unresolvable, because a point outside the resolved topology
still cannot be told from a sibling backlog's point without holding every
mounted topology at once. It also keeps the grammar in the wrong package, so the
next point namespace pays the same cost again.

**Not moved, and deliberately.** The worktree-root guard stays where rev 8 put
it, on the composition-resolved path before the root is picked (D9 clause 4):
bound to the rebased path it would inspect the cache root, which is outside every
checkout and therefore inside nothing, and would always pass. Dedup and
provenance stay on the ai-hats side too — rows are tagged with their declaring
component **at collection** (`composer.py`), before any merge. The dedup key
follows from this rev: ai-hats no longer knows what a point *is*, so the key it
can compute is the row's **structural identity** — app, path in the tree, `run:`,
and the cargo minus `on_error`. `check_log_token` derives one log name per
binding from that same identity, which is what keeps the HATS-1137 invariant (two
rows, two logs, no collision). The rack sees rows that are already unique.

### D8 — Migration: contract *first*, then expand

*Revised at rev 7 (HATS-1240). Rev 5–6 had the expansion lead and the retirement
trail it; the order is inverted, and the deprecation window is replaced by a
tombstone.*

Expand–contract exists to protect consumers of the thing being removed.
`lifecycle_hooks` has none — zero declarations across `library/core`,
`library/usage` and `ai-hats-custom`, and two importers. With no consumers,
the window between removing it and shipping `checks` is empty, and the ordering
is free to be chosen on other grounds. It is chosen thus:

1. **Retire `lifecycle_hooks` first**, machinery included: the `<event>.d/` tree,
   the `.manifest`, the sweep, `_assert_manifest_intact`, the managed-name
   flattening, the unresolved `skill_dir / script` join. Everything the following
   steps would otherwise have to interoperate with, for the length of an epic, is
   gone before they start. Epic **HATS-1266**, card HATS-1147.
2. **A declaration of `lifecycle_hooks:` fails composition loudly** — a tombstone,
   not a deprecation warning. Rev 5–6 called for "parsing with a deprecation
   warning for one release"; a warning is the wrong instrument when the failure
   mode is *a gate that does not install*, and the zero-declaration survey covers
   only the layers we can see. Precedent: HATS-1160 tombstoned `plan_sections`
   the same way.
3. **Then add the binding field on `Composition` + the `wt` `pre-merge` point** (epic HATS-1138; the field shipped as `checks:` and was replaced by `apps:` at rev 10),
   on the cleaned substrate and calling the execution primitive step 1's epic
   leaves behind.
4. **Defer** folding `worktree.wt_in/wt_out` into `checks:`. Its carry is
   **persisted into worktree state JSON at create and replayed at teardown**
   (`wt_hooks`, ADR-0013 [3] D5), so live worktrees would replay the old shape. Needs
   a state-compat shim; its own card (HATS-1146). Note this is the *declaration*
   channel only — moving that channel's *scripts* to `in_process` per ADR-0020
   [4] D4 is independent of `checks` and rides step 1's epic (HATS-1269).

**The cost of leading with the retirement, stated plainly.** Between step 1 and
step 3 the project has no FSM edge extension point at all. Today that window is
empty; if an edge gate becomes necessary inside it, the answer is to wait rather
than to build a temporary fourth channel whose only purpose is deletion.

### D10 — Channel taxonomy (moved to ADR-0020)

*Split out 2026-07-29, during this rev's review (HATS-1240), before rev 7
merged.* The channel taxonomy (`in_process` vs `detached`), the
`bundle`/`selection` attributes, the git_hooks orchestrator contract (the
fail-open dispatcher) and the substrate migration table are epic HATS-1266's
design of record and live in **ADR-0020 [4]**. This ADR consumes them in two
places: D9's resolution root — the surface's skill mirror since HATS-1540 — is
`in_process`, and D8's ordering leans on the substrate epic running first.

## Consequences

**Gained.** One exit contract and one validation posture (there is no single
catalog of points, and by D11 there deliberately is not — each application owns
its own vocabulary); binding becomes reviewable configuration rather than a
skill-internal constant; skills become reusable under differing policy; the `wt`
`pre-merge` point closes the HATS-1134 gap without a fifth ad-hoc channel; the
union special case is retired rather than propagated. **Zero changes to any
`SKILL.md` schema.**

**Cost.** One new `Composition` field, whose overlay-precedence semantics must be
defined: dedup is by the row's **structural identity** — the app, its path in the
tree, `run:`, and the cargo excluding `on_error` — with the strictest `on_error`
winning and the first declaration keeping the slot. (Rev 5 wrote that key as
`(skill, script, point)`; under rev 10 ai-hats does not know what a point is, so
the key it can compute is structural.) The retired `checks:` key does not coexist
with `apps:` — it is a typed refusal, not a deprecation window. Binding-site
indirection means reading `SKILL.md` alone no longer shows where a script fires —
mitigated by rendering the effective binding table, which `describe_checks` does
for a launch and `ai-hats --dry-run` (HATS-1548); a user-facing surface for it is
still owed by HATS-1153 / HATS-1539. Resolution
has two modes (D9), in-session and out-of-session, and both must be exercised:
one of them being tested is how a gate ships half-armed. **Added at rev 9:** a
typo in a point ai-hats does not own is no longer refused at composition — it
surfaces when the owning application next runs, or never (D11). That is the
price of the three silences D11 removes, and the compensation is owed by
HATS-1546.

**Risk.** *(Rewritten at rev 7. The former text named "role-aware materialization

- drift detection" as the delicate part — that mitigation had already been
  removed by rev 5, and the paragraph was left behind.)* The delicate part is now
  **coverage of the paths that reach a bound point**. The failure this ADR exists
  to remove is a gate that silently does not run, and every such failure so far has
  been a path nobody enumerated: a second CLI verb that bypasses the kernel
  (HATS-1150), a surface whose tree lives elsewhere (what rev 7 fixes), an
  invocation with no session. The mitigation is enumeration, not cleverness: every
  path that can reach a point carries either a test that the check fires or a
  recorded decision that it must not.

**Resolved at rev 8 (HATS-1540).** The condition was a live consumer bound and
proven to refuse, because shipping the mechanism unbound would have left a fifth
channel in the accretion this ADR exists to end. `maintainer` now binds
`done-gate.sh` to both `edge:review--done` and `apps.wt` `pre-merge`, and each refusal
is asserted together with its flip-to-pass when the row is removed. HATS-1144
(hunk-review) remains a candidate and is no longer load-bearing for this status.

## Open questions

**Q1 — of the three roads D3 leaves ungated, the squash one is the only one that
publishes.** `cleanup(IsolationMode.SQUASH)` — the sub-agent teardown — runs
`git merge --squash` plus a `feat(agent): <branch>` commit onto the recorded base
branch in the main checkout (`_squash_merge`) and fires no `pre-merge`. That is a
recorded decision, stated at the call site and in D3, pinned by
`test_the_squash_cleanup_path_does_not_fire_the_point`, and its reasoning holds
for the mechanics it addresses: `cleanup` suppresses a lifecycle veto by design
(ADR-0013 [3] D8, so a sub-agent's own error is not masked), so a refusal there
would be swallowed and the gate would look armed while passing everything.

What it does not settle is the asymmetry it shares a paragraph with. The two
`merge()` short-circuits publish **nothing**: `git merge` does not run, their
content reached the base by another route, and `pre-merge` is a *precondition of
publishing* (D3) — so gating them would protect nothing and would refuse a
supported recovery, which is why they are ungated. Re-derived from the sources
under HATS-1595 (2026-08-12) after an audit read the same two `return`s as an
omission. The squash road is not in that class: content enters a base branch
through a path no row can see, and the only remaining contour is
`pre-push-e2e-master.sh`, which keys on `refs/heads/master` lines of the push
protocol — a different gate, on a different trigger, silent for any base that is
not master and for anything never pushed.

Three shapes, none costed: make the veto non-suppressible at this one call site
(a change to D8's contract, not to the call site); give the road its own point,
whose refusal semantics need not inherit D8; or rule it out of scope on the
ground that the branch a sub-agent squashes into still has to cross `pre-merge`
on its own way into master. **No owner and no card as of 2026-08-12.**

## Alternatives considered

**A — Fifth ad-hoc channel just for a pre-merge point.** Cheapest; makes the
accretion permanent and leaves `lifecycle_hooks` unproven. Rejected.

**B — Unify all five channels including `git_hooks` / `runtime_hooks`.** Couples
three unrelated substrates and migrates channels with live consumers and no
complaint against them. Rejected as over-reach.

**C — Hardcode the hunk check into the engine merge path.** The engine would
depend on a *user-library* skill in a separate repo (`ai-hats-custom`) and break
on any project lacking it. Rejected in HATS-1134 planning.

**D — Keep `lifecycle_hooks`, add a `wt:` namespace to it.** Retains
`{event: [script]}`, which cannot express one script on several points, nor
per-entry `on_error`, nor any role control. Rejected on expressiveness.

**E — Rev 1: skill-side `checks:` with an inline `on:`.** The skill dictates
where it fires, so a project cannot rebind or soften it without forking, and role
control stays unexpressible. Rejected by supervisor review.

**F — Rev 2: skill-side `provides.checks` (named capability) + role-side `use:`.**
Better encapsulation (bindings survive a script move), but the skill and the
trait each author the same fact. Rejected as duplicate work for indirection with
no current consumer.

**G — Rev 9: widen the point catalog instead of moving it.** Teach
`known_points()` to resolve the *project's* backlog topology the way the kernel
does, so the catalog and the runner stop disagreeing. Was card HATS-1534,
cancelled into HATS-1541. *(`known_points()` was deleted by that rev; the shape
is kept here as the rejected alternative, not as a live symbol.)* Rejected: it fixes the divergence and leaves
`_guard_topology` in place with nothing it can decide — a point outside the
resolved topology is still indistinguishable from a sibling backlog's point
unless the validator holds every mounted topology at once, which only the rack
does. See **D11**.

## Rev 4 — corrections from adversarial review

> **Reading note (rev 7).** This section is a dated record of a review held on
> 2026-07-23, kept because it is the evidence behind D4/D9 here and behind the
> exit-code contract now in ADR-0020 [4] D2. Its `file.py:NN`
> citations are **as-of that date and are not maintained**. An audit on
> 2026-07-26 confirmed the cited files and symbols still existed then, with
> several line numbers moved; that is **no longer true** — HATS-1147 deleted
> `src/ai_hats/lifecycle_hooks.py` outright and emptied `rack_consumers.py` of
> `HookRunnerExtension`, so parts of the record below cite code that does not
> exist. Read it as testimony about a design, not as a map of the tree. Chasing
> the offsets each time the code shifts would give the record a precision it
> never claimed; use the file and symbol names, not the offsets.
> Two claims here were also overtaken by later revisions: the `0o755`-writing
> materializer (deleted by D9) and the "no in-repo consumer" survey (see
> *Consequences*, which now makes a live consumer a release condition).
> **The DSL below is pre-rev-10 throughout** — `skill:` / `script:` / `on:` as
> keys, `wt:`-prefixed point names, and `(skill, script, point)` as the dedup key.
> Rev 10 replaced all of it (see *Revision history*); the spellings are left as
> written because the record is testimony, not instruction.

Three independent reviewers (completeness / backward-compat / corner-cases)
returned **INCOMPLETE** on the plan and found defects in rev 3. Corrections that
supersede the body above:

**Factual errors in rev 3.**

- D2 claimed `_health_check` validates "exists, non-empty, shebang, executable".
  It does **not** check the exec bit (`lifecycle_hooks.py:137-155`). Nor should
  it: the materializer writes `0o755` (`:217`), so a `644` source in git is
  legitimate. Non-executability is only reachable by post-materialize tampering.
- D3's "discard deliberately has no pre-op point: discard is an explicit
  throw-away" is **false for two of three callers** — the FSM fires discard on
  `failed`/`cancelled` (`rack_wiring.py:261-262` → `teardown(merge=False)`), and
  `reclaim_if_clean` calls `discard(force=False)` (`manager.py:1042`).
- D2's "same overlay precedence, last-wins, dedup" described **unimplemented
  work, not reuse**: as of 2026-07-23 `CompositionResult` had no `checks` field,
  `OverlayConfig` had only `add_/remove_{traits,rules,skills}`, and the composer
  read no binding cargo at all. All three have since shipped; the review's point
  — that the sentence claimed reuse of machinery that did not exist — stands as
  a record of that moment. (D2's "last-wins" was separately wrong and is
  corrected there.)

**Open question 3 was REVERSED at rev 4 and is REVERSED BACK at rev 8
(HATS-1540) — its premise does not hold.** The rev-4 argument is kept verbatim
below because the correction is the interesting part.

Its load-bearing claim is that firing at worktree-effects priority 30 lands
*after the ownership claim at 20*. On `edge:review--done` the claim never runs:
`OwnershipClaim.subscriptions` uses `_keys_into(topology, "execute")`, so it
subscribes only to edges INTO `execute` (`rack_wiring.py`). The in-lock ladder
this edge actually walks is single-slot(5) → plan-gate(10) → **checks(15)** →
worktree teardown-merge(30) → ownership release(40); a refusal at 30 precedes
the release, and nothing at 20 fired. So the property rev 4 was protecting is
not at risk here, and the code fires the point regardless of caller —
suppressing it for one caller would be exactly the caller-aware coupling D2
exists to avoid.

The consequence, stated rather than discovered: on the FSM road the same script
runs **twice** for one transition — at `edge:review--done` and again inside the
teardown-merge at `wt:pre-merge`. The second run is a marker lookup on the same
commit, so it is cheap and its verdict cannot disagree with the first. One
asymmetry survives and is deliberate: `AI_HATS_TASKS_DIR` is absent at
`wt:pre-merge` (D5 — that point resolves no backlog), so a script's
backlog-scope policy does not apply on the second leg. That matters only for a
card living in a foreign backlog that ALSO has a worktree recorded under this
project keyed by the same id; the first leg passes it as foreign and the second
gates its tree. Narrow, and preferable to teaching the wt engine which caller it
serves.

*Rev 4's text, superseded:* `wt:pre-merge` must **not** fire on the FSM
auto-merge path: there it runs at worktree-effects priority 30, i.e. *after* the
ownership claim at 20, discarding the "an abort leaves zero resource side
effects" property that priority 15 exists for (`rack_consumers.py:47-55`, fix

# 1), and the dispatcher has no compensation. `edge:review--done` already gated

that path at 15. Related: `wt:pre-merge` is a precondition of the **merge
operation**, not an invariant of reaching `done` — `merge()` early-returns with
no worktree, and `teardown` has an already-merged path that never calls `merge()`
at all (`wt_effects.py:152-174`).

**Open question 1 answered, and worse than stated.** No in-repo gate is a natural
consumer: every one is per-commit (git substrate) or per-tool-call (provider
substrate). `maintainer-quality-gate` is specifically the **wrong** nominee — it
is SHA-keyed and decoupled from the push connection by HATS-686, and re-binding
would re-import the coupling that incident removed. **HATS-1137 is the only
possible proving consumer**, and it is `blocked` on this epic.

**New decisions the ADR must state** (each now carries review evidence):
`--force` never bypasses a check, it is passed as information the check may honor
(`dispatch.py:112-113`); `--skip-hooks` covers teardown harvest only and must not
reach `wt:pre-merge`; `script:` must resolve inside its skill dir (a live
traversal hole — `lifecycle_hooks.py:111` joins unresolved, so `../../../x.sh`
gets copied out, chmod 0755, and executed in-lock); dedup is by
`(skill, script, point)` with **strictest `on_error` winning** (the
`collect_plan_sections` OR-on-`required` precedent); ordering moves from
materialized-filename lexicographic to **composition order**; `stdin=DEVNULL` at
every point; `AI_HATS_WORKTREE_PATH` is **explicitly unset** when unresolvable,
never inherited (a stale value is a wrong-answer pass, worse than a crash), and
is unset at `edge:*--execute` by construction (15 < 30); a check must not invoke
a mutating `rack`/`ai-hats wt` command on its own task (it would re-enter the
per-task lock from a subprocess and deadlock — export `AI_HATS_IN_HOOK=1`);
`on_error` needs a materialized **binding catalog** (`bindings.yaml`, the
`plan-sections.yaml` precedent) because the managed filename carries no policy.

**Two hazards that could wedge or disarm the backlog — both DISSOLVED by D9.**
Recorded because they are what forced rev 5, and because they are the evidence
that a persistent shared managed directory was the wrong substrate:

1. **Wedge (would-have-been).** Worktree sessions resolve `project_dir` to the
   *main* checkout (`cli/_helpers.py:191-197`), so parallel sessions share one
   `lifecycle-hooks/.manifest`. Role-independence is what makes them converge on
   identical bytes today — the absence of a lock in `materialize_lifecycle_hooks`
   is *safe because of* that property, not an oversight. Role-awareness would
   make the expected sets **divergent**, and since the materializer's three
   phases (write → sweep → write manifest) are separate unlocked steps, one
   process's sweep landing between another's sweep and manifest write leaves the
   manifest naming a deleted file. `_assert_manifest_intact` then aborts **every**
   transition on that event, with **no `--force` bypass** — verified: `force`
   appears in `rack_consumers.py` only as the `AI_HATS_HOOK_FORCE` env var
   (`:137`), never as a bypass, and the assert runs first in `on_event` (`:92`).
   Recoverable only by `ai-hats self init`.
2. **Silent disarmament (would-have-been).** `materialize_lifecycle_hooks()` is
   called *unconditionally* (`hooks_manager.py:199`) — unlike git hooks on the
   next line, which guard on `result is not None`. Role-aware + `result=None`
   (reachable from `self init` / `self bump`) would sweep every gate and rewrite
   the manifest empty.

Under D9 neither can occur: there is no shared managed directory, no sweep, and
no manifest. Note that the *guaranteed* problem was never the race — it was
last-writer-wins mis-gating between concurrent roles, which needed no
interleaving at all and produced no error.

**Resolved (rev 6, supervisor 2026-07-23):** `plan_sections`' fate (HATS-1149) —
the consumer channel is **deleted**. Grounds: zero producers across all library
layers (core 29 + usage 70 skills, installed tree, ai-hats-custom);
behavior-neutral (with zero declarers no `plan-sections.yaml` is even written);
the epic's own zero-consumers retirement precedent. The rack-side surface
(`Section`, `DEFAULT_PLAN_SECTIONS`, `merge_sections`, `load_sections`,
`build_rack_kernel(sections=)`) is kept; a loud tombstone rejects future
`plan_sections:` declarations — a silent no-op would weaken the plan-gate; a
future re-introduction should be live-collect in the D9 style (integrator-side
union scan at kernel build, no materialized file — packaging allows it:
`consumer_plan_sections` is integrator-side, the file read was a choice, not an
import constraint). The original "own module + manifest" idea died on evidence:
`_assert_manifest_intact` never covered `plan-sections.yaml`
(`rack_consumers.py:117`). Evidence: HATS-1149 `research.md`; implementation:
HATS-1160. Resolved since rev 4: the wedge
mitigation (superseded by D9 — nothing to mitigate) and pulling HATS-1137 inside
the epic (done; the epic now carries two product deliverables, hunk review and
the quality gate).

**Cards that shrink or change under D9** — to be re-scoped: **HATS-1141** loses
role-aware materialization, the role stamp and the role-aware drift detector, and
becomes "resolve bindings from the composition, with the session snapshot as the
in-session root and a live compose out of session" — the wording first recorded
here said "from the session skill tree", which is the *provider* tree rev 7
falsified (that re-scope has since happened: HATS-1141 landed in exactly this
shape, see D3 and D9); **HATS-1147**'s deletion set grows (manifest, sweep,
`_assert_manifest_intact`, managed-name helpers); the "binding catalog"
(`bindings.yaml`) named above is no longer needed as a *materialized* artifact —
`on_error` travels with the binding in the composition itself.

## References

- [1] `docs/adr/0012-worktree-data-transfer.md` — the worktree data-transfer
  contract: the teardown-veto this ADR's exit codes deliberately do *not*
  reintroduce (HATS-775), the engine-side lever in `wt_lifecycle.py`, and
  Revisions #3 on hard-failing composition against an older engine.
- [2] `docs/adr/0018-unified-artifact-builder.md` — the unified artifact builder
  (epic HATS-1165) whose landing falsified rev 5's materialization premise, and
  whose §2.2 movability test is what HATS-1217 actually delivered.
- [3] `docs/adr/0013-wt-core-extraction-boundary.md` D5 — `wt_hooks` carry
  persisted into worktree state JSON at create and replayed at teardown, the
  reason the declaration fold needs a state-compat shim.
- [4] `docs/adr/0020-hook-execution-and-materialization-substrate.md` — the
  substrate this model binds to (epic HATS-1266): channel taxonomy, the
  execution primitive's mechanics and shared env base, the git_hooks
  orchestrator, and the per-channel migration. Split out of this document's
  rev 7 on 2026-07-29, pre-merge.
- [5] `docs/adr/0021-surface-materialization.md` — the materialization guide:
  which artifact each surface writes and where, why a project-level artifact
  carrying the declared rows is forbidden (M5), and the session tree this
  channel resolves from.
