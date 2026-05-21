---
name: design-minimalism
description: At plan stage, every primitive (class, abstraction, option, new step) must be justified by a concrete use case in the current epic — speculative additions go to Out of scope
---
# Design Minimalism

Plan-stage discipline: each primitive (class, abstraction, option, new step,
new dependency) in a design plan must be justified by a **concrete use case
in the current epic**. Speculative additions ("may be useful for X later",
"central registry for future keys", "pre-listed child tasks for unbuilt
features") belong in **Out of scope** with an explicit activation trigger,
or are dropped.

This complements `trait-se-mindset`'s "Simplicity first" — that rule is about
**code**; this skill is about the **design phase** of a task.

## When to Use

- Transitioning `brainstorm → plan` for a non-trivial task.
- Writing or revising `plan.md` for any task.
- Reviewing a plan that proposes new abstractions, options, or sub-systems.
- Noticing the urge to "future-proof" or "add for completeness".

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

| Proposed | Rejected because |
|----------|------------------|
| `MutableStateEnvelope` class | speculative — no current step needed mutation |
| Central state schema with enumerated keys | speculative — closed schema blocks user extension |
| `SaveArtifact` pipeline step | speculative — no current pipeline declared persistence |
| YAML pipeline manifests | speculative — bash-compose covers all current use cases |
| 15 pre-listed child task IDs | speculative — child tasks file at start of phase when design is stable |

Each was framed as "future-proofing". Each cost user time to read and reject.
A `## Out of scope` section listing them once with rejection rationale would
have saved 5 iterations.

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
