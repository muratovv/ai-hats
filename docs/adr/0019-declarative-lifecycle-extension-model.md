# ADR-0019: Declarative lifecycle-extension model for skills

## Status

Proposed (HATS-1139, 2026-07-24). Gates the epic HATS-1138 mechanism chain
(HATS-1140–1143), its consumers (HATS-1137 merge-correctness gate, HATS-1144
hunk-review) and the re-bindings (HATS-1145–1147). Driver: HATS-1134 (incident
HATS-1130).

**Renumbered from ADR-0018.** This decision was drafted as ADR-0018 in
`HATS-1139/design.md`. Cross-epic coordination (2026-07-24) assigned **ADR-0018**
to the provider **artifact-builder** (HATS-1170, epic HATS-1165) — materialization
is the foundational substrate — and this lifecycle-extension model, the handlers
that resolve from that materialized per-session skill tree, follows it as
**ADR-0019**. Historical "ADR-0018" references in the epic HATS-1138 backlog
(work_log entries, closed cards such as HATS-1160 / HATS-1149) predate the
renumber and point here.

**Revision history.** Rev 1 put the attachment point in `SKILL.md` (kept as
*Alternative E*). Rev 2 added a named skill capability bound by the role (kept as
*Alternative F*). Rev 3 dropped the skill side entirely: **only traits/roles
change.** Rev 4 folded in an adversarial review (exit code, reversed open question
3, factual corrections). Rev 5 removes materialization altogether — see **D9**,
which supersedes rev 4's role-keyed-materialization mitigation. Rev 6 resolves the
last open item: the `plan_sections` consumer channel is **deleted** (HATS-1149,
option A; implementation HATS-1160).

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
- **Referential integrity.** `skill:` naming a skill that is not composed is a
  loud composition error; `script:` is health-checked (exists, non-empty,
  shebang, executable) by the existing `_health_check`. Binding does **not**
  implicitly pull the skill in — implicit composition is how you get surprise
  gates.

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
refusals — explicitly *not* the teardown-veto rejected by ADR-0012 / HATS-775,
which fires after the merge commit exists and can only strand a worktree.

`discard` deliberately has no pre-op point: discard is an explicit throw-away.

### D4 — Uniform exit-code contract

| exit          | meaning                          | effect                                  |
| ------------- | -------------------------------- | --------------------------------------- |
| `0`           | pass                             | operation proceeds                      |
| `2`           | **refuse** — the check's verdict | refused; stdout tail becomes the reason |
| `1` and other | the check itself broke           | governed by `on_error:`                 |

**Refuse is exit 2, deliberately not 1** (corrected in rev 4 — rev 3 had `1`).
Under `set -e`, a false `[[ ... ]]`, a `grep` with no match and a failing `jq`
all abort a bash check with **exit 1**; exit 1 is the status a shell check
produces *by accident*. It must therefore route to the `on_error` safety valve,
not be read as a considered verdict. The inverse is live in-repo today:
`drain-review.sh` does `exit "$rc"` propagating `hunk-notes.sh`'s status (2, 127,
…) to fail **closed** — under a naive "other = broke" plus `on_error: warn` that
data-protection script would fail **open**, silently reproducing HATS-1130.
Precedent for not signalling a verdict by exit status: the runtime-hook channel
returns 0 and emits `permissionDecision: deny` on stdout (`safety_gate.py`).

Statuses `126`/`127` (not found / not executable) and anything `>128` (signal
death: 130 SIGINT, 143 SIGTERM) are **broke**, and must be named as such in the
message. SIGINT aborts the whole operation regardless of `on_error`
(`worktree_hooks.py:21-22` — SIGINT is never swallowed). A **timeout is broke,
not refuse**: a hung check formed no verdict.

`on_error: refuse | warn`, default **`refuse`** (preserves today's fail-closed
posture). A consumer opts into `warn` explicitly — but **`on_error: warn` is
rejected at composition for `wt:pre-merge` and `wt:teardown[*]`**: failure policy
at data-protection points belongs to the point catalog, not the binding author.
ADR-0012 deliberately put that lever in the engine (`wt_lifecycle.py`) so a
component could not opt out; moving it into user YAML would regress that.

`on_error` governs **only the check's own exit status**. A missing,
non-executable, or manifest-unexpected managed script is *infrastructure
corruption*, fails closed at every point, and **cannot be downgraded by
`on_error: warn`** — otherwise every warn-binding becomes a way to silently
disarm the `_assert_manifest_intact` backstop.

Separating "I refuse" from "I broke" is load-bearing: a check that crashes on a
task with no worktree must not wedge the backlog. The hunk binding declares
`on_error: warn` — a *policy* gate fails open, while *data protection*
(`wt:teardown` harvest) keeps `refuse`.

### D5 — Unified env contract

Common to every point: `AI_HATS_HOOK_POINT` (fully-qualified, e.g.
`edge:review--done`), `AI_HATS_PROJECT_DIR`, `AI_HATS_FORCE`, and — when
resolvable — `AI_HATS_TASK_ID`, `AI_HATS_WORKTREE_PATH`.

`AI_HATS_WORKTREE_PATH` at `edge:` points is a fix, not a nicety: today
`HookRunnerExtension` passes `AI_HATS_HOOK_TASK_FILE` but not the worktree, so
every FSM guard must hand-roll `task.yaml` → task id →
`sessions/worktrees/task-<id>.json` → `jq -r .worktree_path`. Resolving it once,
centrally, is what makes one script bind to both `edge:` and `wt:` points — the
payoff of D2.

Existing point-specific variables keep their names; migrated scripts do not churn.

### D6 — Validation: loud on structure, forgiving on vocabulary

- Unknown namespace / point, malformed row, missing `skill`/`script`, uncomposed
  `skill:` → fail loud at composition, naming the trait/role and the entry. A
  gate that silently fails to install is the HYP-078 / HATS-961 hole.
- Unknown *optional field* on a row → warn and ignore, so a newer trait does not
  hard-fail composition on an older engine (ADR-0012 Revisions #3).
- Script health-check keeps `lifecycle_hooks._health_check`'s *posture* (missing
  / empty / shebang-less → loud, "a no-op gate is a broken gate") but moves to
  **composition time**, since D9 removes the materialization step it ran in. It
  does **not** examine the exec bit — with no copy step there is no `0o755`
  rewrite, so the script must already be executable in the skill dir, and that is
  checked at run time (a non-executable script is *broke*, not *refuse*).
- `script:` must resolve **inside** the declaring skill's directory. Today
  `collect_lifecycle_hooks` joins `skill_dir / script` unresolved
  (`lifecycle_hooks.py:111`), so `../../../x.sh` escapes; under D2 the binding is
  authored in a *trait* that need not own the skill, which makes it worse.

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

### D9 — No materialization: composition is the truth, the session tree is a cache

**Bindings are not copied anywhere.** The composed skill tree is *already*
materialized per session at
`<ai_hats_dir>/.cache/sessions/<sid>/plugin/skills/<skill>/…`, scripts included
and with the exec bit preserved (verified: 35 skills, `hooks/*.py`,
`git_hooks/*.sh`, mode `0755`). A binding names `{skill, script}`, which resolves
directly to a path in that tree. The runner executes it **in place**.

The session id already reaches the environment (`AI_HATS_SESSION_ID`, set at
`ai_hats_observe/session.py:308`) and the rack already reads it
(`rack_wiring.py:88`).

This **deletes**, rather than mitigates: the copy into `<event>.d/`; the
flattened `<skill>-<basename>` managed name and its collision rule; the
`.manifest`; the `previous - new_names` sweep; `_assert_manifest_intact`; the
role-stamped manifest and the role-aware drift detector.

With them go five of the ranked backward-compat hazards, **by construction**:
the sweep-vs-manifest wedge; last-writer-wins mis-gating between concurrent
roles; silent disarmament via `materialize(result=None)`; the `config set-role`
mis-gating window; and version-skew freezing another role's gates. Every one was
an artifact of a persistent shared managed directory. There is no such directory.

Rev 4 proposed role-keyed materialization instead. That was worse: it invented a
new key and a new role-resolution path for the runner, when **the session already
is the role key** (one session composes exactly one role).

**The cost, stated plainly: invocation outside a session.** `rack transition`
from a bare terminal, from cron, from a script, or via the standalone `rack`
binary has neither `AI_HATS_SESSION_ID` nor a session tree, so bindings would not
resolve and **no gate would run — fail open**, the HYP-078 hole. This is not
hypothetical: `_session_id()` already returns `""` for that path today. It bites
HATS-1137 precisely — a ruff/test gate on `wt:pre-merge` would be one
`env -u AI_HATS_SESSION_ID` away from useless, the escape-habituation failure
mode this epic exists to avoid.

**Resolution — composition is the source of truth; the session tree is only a
cache.** When no session tree is resolvable, the runner composes the active role
from config and resolves bindings straight from the library. No materialization
in either path.

The prize is bigger than closing the hole. Today's bug class is "materialized
state drifts from composition" — the drift detector exists *only* because
materialized state is authoritative. Demoting it to a cache removes the entire
drift category instead of adding another watchdog. Cost: one compose per
out-of-session transition (a rare, human-in-a-terminal path), cacheable by a hash
of the composition inputs.

### D8 — Migration: expand–contract

1. Add `Composition.checks` + the `wt:pre-merge` point. Absorb `lifecycle_hooks`
   outright — **zero consumers, no compat shim owed**; keep it parsing with a
   deprecation warning for one release.
2. **Defer** folding `worktree.wt_in/wt_out` in. Its carry is **persisted into
   worktree state JSON at create and replayed at teardown** (`wt_hooks`,
   ADR-0013 D5), so live worktrees would replay the old shape. Needs a
   state-compat shim; its own card.

## Consequences

**Gained.** One catalog of points, one exit contract, one validation posture;
binding becomes reviewable configuration rather than a skill-internal constant;
skills become reusable under differing policy; `wt:pre-merge` closes the
HATS-1134 gap without a fifth ad-hoc channel; the union special case is retired
rather than propagated. **Zero changes to any `SKILL.md` schema.**

**Cost.** One new `Composition` field, whose overlay-precedence semantics must be
defined (last-wins per `(skill, script, point)`?). Two concepts coexist during
the deprecation window. Binding-site indirection means reading `SKILL.md` alone
no longer shows where a script fires — mitigated by rendering the effective
binding table (the `routing.md` precedent).

**Risk.** Role-aware materialization + drift detection (D7) is the delicate part:
a stale materialization after a role change would leave the wrong gates armed.

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
