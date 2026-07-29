# ADR-0019: Declarative lifecycle-extension model for skills

## Status

Proposed (HATS-1139, 2026-07-24; last revised at rev 7 — HATS-1240, 2026-07-27).
Governs epic **HATS-1138** — the declarative mechanism (HATS-1152 → 1140 → 1241
→ 1141 → 1142 → 1143), its consumers (HATS-1137 merge-correctness gate,
HATS-1144 hunk-review) and the re-bindings (HATS-1145, HATS-1146). The substrate
underneath — retiring `lifecycle_hooks`, the hook-execution primitive, the
channel postures and the git_hooks orchestrator (epic **HATS-1266**, which runs
first per D8) — is **ADR-0020 [4]**, split out of this document's rev 7 during
review. Driver: HATS-1134 (incident HATS-1130).

**It stays `Proposed` on purpose.** This ADR replaces a channel with zero
declared consumers, and neither candidate consumer is live yet. It becomes
`Accepted` when one is bound and proven to refuse — not when the mechanism
merges. See *Consequences*.

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
the extension model only.

## Context

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

Needs two things the current model cannot express: a **named catalog of
attachment points**, and a **binding layer the composer owns**.

## Decision

### D1 — Unify the *lifecycle* domain only

Covers **rack FSM edges and worktree lifecycle**. Does **not** absorb
`git_hooks` (git's event domain, installed to `.githooks/`) or `runtime_hooks`
(provider domain, wired into `settings.json`) — different execution substrates
with different lifetimes; folding them in buys symmetry at the cost of coupling.

### D2 — The binding lives in the trait/role. Skills do not change

One new field on `Composition`; **no new `SKILL.md` frontmatter**:

```yaml
# library/usage/traits/hunk-review-trait/config.yaml
name: hunk-review-trait
composition:
  skills: [hunk-review-comments]
  checks:
    - skill: hunk-review-comments
      script: hooks/check-review-availability.sh
      on: [edge:review--done, wt:pre-merge]
      on_error: warn
```

The script stays where scripts already live — in the skill directory, resolved
skill-relative exactly like `git_hooks` / `runtime_hooks` / `worktree` entries.
The skill ships *capability*; the composition decides *policy*.

What this buys against the two consequences above:

- **Reuse without forking.** The same script binds to different points, with
  different failure policy, per trait/role/project.
- **Role control.** A role composing the trait is gated; one that does not, is
  not. Unexpressible in Rev 1.
- **One mechanism.** Bindings resolve through the composer that already handles
  traits/rules/skills — same overlay precedence, last-wins, dedup.
  `lifecycle_hooks`' union-scope special case disappears rather than being
  extended to a fifth channel.
- **Referential integrity.** `skill:` naming a skill nobody composed is a loud
  composition error (a skill an overlay *removed* is a warning — D6); `script:`
  is health-checked at composition for exists / non-empty / shebang / executable
  per **D6**. *(Rev 7: this bullet used to attribute that check to "the existing
  `_health_check`". Rev 4 recorded that the existing one never examined the exec
  bit, but corrected only its own section and left this sentence standing — the
  check D6 now specifies is new, not inherited.)* Binding does **not** implicitly
  pull the skill in — implicit composition is how you get surprise gates.

Deliberately **not** added: a skill-side `provides:` block naming each check.
It would be `{name, script}` — a rename of a path — after which the trait repeats
that name. Duplicate authoring for indirection no current consumer needs
(`design-minimalism`). A moved script is caught loudly by the health-check, not
silently.

### D3 — The point catalog

| point                                  | when                                                                                          | may veto           | replaces          |
| -------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------ | ----------------- |
| `edge:<from>--<to>`                    | rack FSM transition, in-lock, prio 15 — before ownership claim (20) and worktree effects (30) | yes                | `lifecycle_hooks` |
| `wt:pre-merge`                         | in `merge()`, before **any** mutation — beside `_check_clean` / `_check_drift` / consent      | yes                | **new**           |
| `wt:create`                            | after `git worktree add`                                                                      | no (warn-continue) | `worktree.wt_in`  |
| `wt:teardown[merge\|discard\|cleanup]` | before `_remove_worktree`                                                                     | yes (fail-closed)  | `worktree.wt_out` |

`edge:` names validate against the live rack topology (full state product —
forced transitions fire non-topology edges); the existing `_valid_event_names()`
rule carries over unchanged.

`wt:pre-merge` is a **precondition**, in the same class as the accepted
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
outcome — the policy layer `checks:` adds on top of the primitive.

`on_error: refuse | warn`, default **`refuse`** (preserves today's fail-closed
posture). A consumer opts into `warn` explicitly — but **`on_error: warn` is
rejected at composition for `wt:pre-merge` and `wt:teardown[*]`**: failure policy
at data-protection points belongs to the point catalog, not the binding author.
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
(`wt:teardown` harvest) keeps `refuse`.

### D5 — Check-point env vocabulary

The shared base every hook receives (project dir, point identifier, force
flag) is the primitive's contract — **ADR-0020 [4] D2**. Points under this
ADR add: `AI_HATS_HOOK_POINT` (fully-qualified, e.g. `edge:review--done`),
and — when resolvable — `AI_HATS_TASK_ID`, `AI_HATS_WORKTREE_PATH`.

`AI_HATS_WORKTREE_PATH` at `edge:` points is a fix, not a nicety: today
`HookRunnerExtension` passes `AI_HATS_HOOK_TASK_FILE` but not the worktree, so
every FSM guard must hand-roll `task.yaml` → task id →
`sessions/worktrees/task-<id>.json` → `jq -r .worktree_path`. Resolving it once,
centrally, is what makes one script bind to both `edge:` and `wt:` points — the
payoff of D2.

Existing point-specific variables keep their names; migrated scripts do not churn.

### D6 — Validation: loud on structure, forgiving on vocabulary

- Unknown namespace / point, malformed row, missing `skill`/`script` → fail loud
  at composition, naming the trait/role and the entry. A gate that silently fails
  to install is the HYP-078 / HATS-961 hole.
- Unknown *optional field* on a row → warn and ignore, so a newer trait does not
  hard-fail composition on an older engine (ADR-0012 [1] Revisions #3).
- Script health-check keeps `lifecycle_hooks._health_check`'s *posture* (missing
  / empty / shebang-less → loud, "a no-op gate is a broken gate") but moves to
  **composition time**, since D9 removes the materialization step it ran in.
- `script:` must resolve **inside** the declaring skill's directory. Today
  `collect_lifecycle_hooks` joins `skill_dir / script` unresolved
  (`lifecycle_hooks.py`), so `../../../x.sh` escapes; under D2 the binding is
  authored in a *trait* that need not own the skill, which makes it worse.

**Amended at rev 7 (HATS-1240):**

- **The exec bit is checked, at composition time.** Rev 5 deferred it to run time
  on the reasoning that "with no copy step there is no `0o755` rewrite". D9 now
  *does* copy into the session snapshot — via `copytree`, which preserves mode and
  never chmods — so a `644` script is dead on arrival at every point it is bound
  to. Checking it beside the shebang costs nothing and converts a runtime *broke*
  into an authoring-time error. (Note the rev-5 text also mis-stated the existing
  `_health_check` as validating executability; it never has.) Audited before
  adopting: hook scripts across `library/core`, `library/usage` and
  `ai-hats-custom` are `100755` in the git index with **one exception** —
  `core/skills/worktree-isolation/hooks/wt_entry_gate.py` is `100644`, and it is
  a live declared `runtime_hooks.PreToolUse` binding, not a data file. It works
  today only because the runtime-hook materializer rewrites the mode on copy
  (`hooks_manager.py`, `mode=0o755`) while the surface then executes the copied
  path bare. So this check would fail exactly one binding that works today — and
  that is the argument for it, not against: the exec bit is load-bearing and
  currently masked by the copy. Adopting the check means fixing that file's mode
  in the same change.
- **A binding whose `skill:` an overlay removed is a warning, not an error.**
  Rev 5 listed uncomposed `skill:` among the loud failures. That is right for a
  *typo* and wrong for a deliberate `--remove-skill`, which an overlay may legally
  apply to a trait-brought skill (HATS-1046). Hard-failing would force a user
  making a legitimate removal to author a new role; staying silent would drop a
  gate they expected. So: warn, naming the trait that bound it, drop the binding,
  and continue composing. A `skill:` that was never composed by anyone remains a
  loud error — the two cases are distinguishable, because one of them is a
  recorded removal (supervisor ruling, 2026-07-26).

One posture, replacing today's three.

### D7 — Scope: role-scoped, by composition

Bindings are collected **per-role over the `CompositionResult`**, like the other
three channels. The union special case retires.

The objection this raises — *an agent dodges a gate by switching roles* — does
not apply: **role selection is out-of-band and supervisor-driven; an agent does
not reassign its own role at runtime** (supervisor ruling, 2026-07-23). So no
project-level tier, no `scope:` field, no cross-role lint is needed.

Implementation consequence — see **D9**, which replaces the obvious-but-wrong
answer (make `materialize_lifecycle_hooks()` role-aware and add a role-aware
drift detector). That approach was rejected in rev 5.

### D9 — Composition is the truth; the check root is ai-hats's own, never a provider's

*Rewritten at rev 7 (HATS-1240). The principle below is rev 5's and survives
unchanged; what changed is where a binding resolves.*

**What rev 5–6 said, and why it was adopted.** Bindings are copied nowhere: the
composed skill tree is *already* materialized per session at
`<ai_hats_dir>/.cache/sessions/<sid>/plugin/skills/<skill>/…`, so a binding
`{skill, script}` resolves directly into it and the runner executes it in place.
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

1. **The resolution source is the composition.** Every skill in a
   `CompositionResult` already carries `ResolvedComponent.source_path`, the
   skill's own directory (`ai_hats_core/composition.py`). A binding resolves as
   `source_path / script`. Surface-independent by construction: no code consults
   a provider layout, so no surface can be forgotten.
2. **Inside a session, the root is ai-hats's own snapshot** at
   `<ai_hats_dir>/.cache/sessions/<sid>/checks/<skill>/` — the declaring skill's
   directory copied *whole*, through the existing `Materializer` port. Whole, not
   just the script, so sibling data files survive (ADR-0020 [4] D1, `bundle`). A
   session must execute the same bytes from start to finish and stay isolated
   from a library being edited concurrently; relying on worktree discipline for
   that isolation would be a policy, not a mechanism, and it lapses at merge.
3. **Outside a session the root is the live library.** `rack transition` from a
   bare terminal, from cron, or via the standalone `rack` binary has no
   `AI_HATS_SESSION_ID` (`_session_id()` returns `""` there) and no snapshot, so
   the runner composes the active role from config and resolves from the library.
   The two modes are not a blemish: the non-negotiable property is that the
   out-of-session mode is **live**, never **absent**. Fail-open there would leave
   HATS-1137 one `env -u AI_HATS_SESSION_ID` away from useless.
4. **A task worktree is never a resolution root.** `builtin_library_root()` is
   worktree-aware, so a naive resolve inside a worktree would run the branch's
   own half-written check — a gate judging the change it is part of.

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

**What the per-session snapshot does *not* bring back.** Every hazard rev 5
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
Nothing is owed to it later: a binding names `{skill, script}`, not a path, so
whoever adopts that shape moves only the resolution root.

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
3. **Then add `Composition.checks` + the `wt:pre-merge` point** (epic HATS-1138),
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
places: D9's `checks/` snapshot root is `in_process`, and D8's ordering leans
on the substrate epic running first.

## Consequences

**Gained.** One catalog of points, one exit contract, one validation posture;
binding becomes reviewable configuration rather than a skill-internal constant;
skills become reusable under differing policy; `wt:pre-merge` closes the
HATS-1134 gap without a fifth ad-hoc channel; the union special case is retired
rather than propagated. **Zero changes to any `SKILL.md` schema.**

**Cost.** One new `Composition` field, whose overlay-precedence semantics must be
defined (dedup by `(skill, script, point)`, strictest `on_error` winning). Two
concepts coexist during the deprecation window. Binding-site indirection means
reading `SKILL.md` alone no longer shows where a script fires — mitigated by
rendering the effective binding table, which nothing renders today. Resolution
has two modes (D9), in-session and out-of-session, and both must be exercised:
one of them being tested is how a gate ships half-armed.

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

**Deliberately unresolved.** This ADR stays `Proposed` until a live consumer is
bound and proven to refuse. The mechanism replaces a channel with zero declared
consumers, and its two candidate consumers (HATS-1137, HATS-1144) are not yet
live; shipping it unbound would leave a fifth channel in the accretion this ADR
is meant to end.

## Alternatives considered

**A — Fifth ad-hoc channel just for `wt:pre-merge`.** Cheapest; makes the
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

## Rev 4 — corrections from adversarial review

> **Reading note (rev 7).** This section is a dated record of a review held on
> 2026-07-23, kept because it is the evidence behind D4/D9 here and behind the
> exit-code contract now in ADR-0020 [4] D2. Its `file.py:NN`
> citations are **as-of that date and are not maintained** — an audit on
> 2026-07-26 confirmed the cited files and symbols still exist while several line
> numbers had moved. Chasing them each time the code shifts would give the record
> a precision it never claimed; use the file and symbol names, not the offsets.
> Two claims here were also overtaken by later revisions: the `0o755`-writing
> materializer (deleted by D9) and the "no in-repo consumer" survey (see
> *Consequences*, which now makes a live consumer a release condition).

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
- D2's "same overlay precedence, last-wins, dedup" describes **unimplemented
  work, not reuse**: `CompositionResult` has no `checks` field
  (`ai_hats_core/composition.py:41-59`), `OverlayConfig` has only
  `add_/remove_{traits,rules,skills}` (`config/overlay.py:31-53`), and the
  composer never reads `composition.checks`.

**Open question 3 is REVERSED.** `wt:pre-merge` must **not** fire on the FSM
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
becomes "resolve bindings from the session skill tree, with lazy compose as the
out-of-session fallback"; **HATS-1147**'s deletion set grows (manifest, sweep,
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
