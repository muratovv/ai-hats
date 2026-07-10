---
name: design-minimalism
description: The Scope & Out-of-scope stage of the plan-gate — every primitive (class, abstraction, option, new step) in plan.md must be justified by a concrete current-epic use case; speculative additions go to Out of scope. Reached through plan-gate, not as an independent trigger.
license: MIT
---

# Design Minimalism

The **Scope & Out-of-scope stage of `plan-gate`**: each primitive (class,
abstraction, option, new step, new dependency) in a design plan must be
justified by a **concrete use case in the current epic**. Speculative additions
("may be useful for X later", "central registry for future keys", "pre-listed
child tasks for unbuilt features") belong in **Out of scope** with an explicit
activation trigger, or are dropped.

This complements `trait-se-mindset`'s "Simplicity first" — that rule is about
**code**; this skill is about the **design phase** of a task.

## When to Use

Reach this stage through `plan-gate`, not as a standalone brainstorm→plan
trigger — `plan-gate` is the single entry point and routes here to fill the
`Scope & Out-of-scope` section of `plan.md`.

- Run it when that section is being written, or a plan proposes new abstractions, options, or sub-systems.
- Trigger inside it: noticing the urge to "future-proof" or "add for completeness".
- Sibling stage: `requirements-interview` owns the adjacent `Requirements` section — clarify *what* to build there, *how much* here.
- Prior stage: `devils-advocate` (`Approach & counter`) runs first and challenges *whether the value holds*; this stage takes the surviving value as given and minimises the **means**. Don't re-litigate the value here — challenge scope, not direction.

## Checklist

### For every primitive in the plan

Ask:

1. **What concrete use case in the current epic does this solve?**
   - Answer must point to an explicit acceptance criterion or a named user-facing capability in the current task.
2. If the answer is **"might be useful for X later"** / **"for a future feature"** / **"in case we need it"**:
   - Move to a `## Out of scope` section with explicit activation trigger ("re-evaluate when feature X is in scope"), OR
   - Drop it entirely.
3. If the answer is **"for symmetry / completeness"**:
   - Same — drop or move to Out of scope. Symmetry is not a use case.

### The `## Out of scope` section is load-bearing

- Prefer **explicit rejection** ("rejected because …") over silent omission.
- This section communicates **what we considered and chose not to do** — saves future review rounds where someone asks "why didn't you add Y?".
- A rich `Out of scope` is a sign of a well-reviewed plan, not bloat.

## Worked Example

**HATS-261 plan-design (2026-05-08), 7 iterations of rewrites.**

User explicitly rejected, across iterations:

| Proposed                                  | Rejected because                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `MutableStateEnvelope` class              | speculative — no current step needed mutation                          |
| Central state schema with enumerated keys | speculative — closed schema blocks user extension                      |
| `SaveArtifact` pipeline step              | speculative — no current pipeline declared persistence                 |
| YAML pipeline manifests                   | speculative — bash-compose covers all current use cases                |
| 15 pre-listed child task IDs              | speculative — child tasks file at start of phase when design is stable |

Each was framed as "future-proofing". Each cost user time to read and reject.
A `## Out of scope` section listing them once with rejection rationale would
have saved 5 iterations.

## Behavioral delivery: escalation ladder

Code-primitive minimalism (above) tells you "drop speculative classes".
**Behavioral-delivery** tasks — edits to skill text, rule text, prompt
shape, HYP data shape, role/trait injection — need a different default:
**data-only first, abstract only after signal**. The ladder lists six
rungs from lowest to highest cost. Ship at the lowest rung that covers
the observable behavior end-to-end; lift one rung only after a PoC
sweep produces signal that the lower rung misses.

### When this section applies

Plan-stage of any task that changes how agents observably behave
through:

- a skill / rule text edit;
- a prompt-shape change (handoff, intake, retro, judge);
- HYP data shape (a new field on a hypothesis);
- a role's composition (attached trait, attached skill, injection
  bullet).

For code-primitive changes (new class, new abstraction, new pipeline
step) — the rest of this skill governs.

### The ladder

| Rung | Layer                     | Cost        | Description                                                                                                              |
| ---- | ------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------ |
| 1    | **text-in-YAML**          | trivial     | New field round-trips via `extra="allow"` or a free-form string in an existing config. No schema change.                 |
| 2    | **skill text**            | low         | Edit an existing `SKILL.md` or add a new one under `library/`. No engine touch.                                          |
| 3    | **trait wiring**          | low         | Attach an existing skill to a trait's composition, or add an injection bullet to a trait. No engine touch.               |
| 4    | **handoff / runner code** | medium      | Minimal Python under `src/ai_hats/retro/` or `src/ai_hats/cli/` to surface or consume a YAML field. Unit-tested.         |
| 5    | **CLI flags**             | medium-high | Typed flags on an existing CLI command. Requires `dev_rule_e2e_gate` coverage.                                           |
| 6    | **typed schema**          | high        | Pydantic model fields, migrations, validators. Reserve for "shape is stable and we need rejection at the storage layer". |

### Don't skip rungs

If rung N suffices to test the hypothesis end-to-end, ship at rung N.
Lift to N+1 only when the next sweep's data shows rung N misses signal.
"We'll probably want flags later" is **not** signal — it's level 1–2
speculation in disguise.

### Plan-stage shape

Every behavioral-delivery `plan.md` states the chosen rung explicitly:

> *Shipping at level N — &lt;one-line why N is sufficient&gt;.
> Level N+1 not warranted because &lt;PoC signal absent / not yet
> measured&gt;.*

This sentence is the artefact the auditor sweep reads (see the companion
HYP authored at document-stage per `library-change-hypothesis-protocol`).

### Case study — HATS-527 / 528 / 534

Canonical retro:
`.agent/ai-hats/sessions/retros/2026-05-26-retro-hats-527-528-534-poc-verification-protocol.md`
(§3 root cause analysis).

- **Initial plan (rejected).** Extended `ValidationLogEntry` schema
  (rung 6) + four typed CLI flags (rung 5) + rewrote four downstream
  skills (rungs 2–3) + legacy-HYP migration. ~30 min of plan-mode
  iteration.
- **User redirect.** *"план сложный. Можем проще как-то сделать?"*
- **Shipped plan.** Five-step PoC: free-form `verification_protocol`
  string under `extra="allow"` in `HYP-*.yaml` (rung 1) + lightweight
  `library-change-hypothesis-protocol` skill (rung 2) + handoff
  formatter patch in HATS-534 (rung 4, escalated **only** after the
  rung-1 PoC showed the data wasn't reaching reviewers). Zero engine
  schema changes. Same observable behavior.

Lesson: rungs 5–6 looked "carefully abstract" because they encoded the
data shape into the model layer. The observable behavior (auditors emit
protocol-matching evidence) needed only rungs 1, 2, and 4 — and rung 4
only after rungs 1–2 produced PoC signal that justified the lift.
Anything higher would have locked in a shape we hadn't yet observed
working.

## Anti-Patterns

- **"While we're at it, let's add a `--verbose` flag"** — no concrete current use case.
- **"Central registry for future extension points"** — no current consumer.
- **"This class is symmetric with the other one"** — symmetry is not justification.
- **Pre-filing child task IDs** before the parent plan is approved — premature breakdown.
- **"In case we need this later" abstractions** — YAGNI applies to design, not just code.

## Completion

A plan passes the minimalism check when:

- Every primitive has a named current-epic use case (verifiable in acceptance).
- `## Out of scope` section is explicit and lists rejected ideas with rationale.
- No "future-proofing" rationale survives in the kept-scope section.

## See also

- `scope-guard` — runtime equivalent: prevents implementation-stage scope creep.
- `trait-se-mindset` "Simplicity first" — code-level KISS/YAGNI.
- `predictive-accounting` — for shrink/refactor tasks: predict delta + dependency cost before implementation.
