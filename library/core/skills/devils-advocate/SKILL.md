---
name: devils-advocate
description: The Approach & counter stage of the plan-gate — a skeptic value-counter that steelmans the value, then challenges whether the work is worth doing (needed? missed anything? another way?) before scoping. Reached through plan-gate, not as an independent trigger.
---
# Devil's Advocate

The **Approach & counter stage of `plan-gate`**: a structured skeptic pass that
fills the `Approach & counter` section of `plan.md`. It takes the **value** the
plan claims (the `Requirements` section, Q1 "Goal & user value") and puts it
under attack **before** the plan is scoped — *is this value real? did we miss
something? is there a cheaper way to the same outcome?*

**The counter is verification-shaped, not attack-shaped:** steelman the value
first (state its strongest version), only then surface the strongest
counterargument. The point is to catch "right scope, wrong direction" — work
that is well-sized and well-built but **not worth doing as framed** — while
redirection is still cheap. It operationalises `trait-agent`'s **anti-anchoring**
principle with a concrete procedure.

## When to Use

Reach this stage through `plan-gate`, not as a standalone brainstorm→plan
trigger — `plan-gate` is the single entry point and routes here to fill the
`Approach & counter` section of `plan.md`.

- Run it **after** `requirements-interview` states the value and **before**
  `design-minimalism` scopes the means. Loop with the interview until the value
  settles, then hand off to scope.
- The section is **conditional** (`required=False`): the engine never blocks
  `execute` on it. A trivial task writes `N/A — <reason>`; a non-trivial one
  should carry a real counter.
- Sibling stages: `requirements-interview` owns WHAT + the value (it *states*
  it); `design-minimalism` owns HOW MUCH (it minimises means and takes the task
  as given); **this** stage is the only one that challenges *whether the value
  itself holds*.

## Checklist

Run the 4-step method on the plan's core value claim:

1. **Steelman the value.** State the strongest version of *why this is worth
   doing* and *what the user gets* — pulled from `Requirements`. No strawman: if
   the value can't be stated well, fix the Requirements first.
2. **Name the unstated assumption.** What must be true for that value to be
   real? (e.g. "users actually hit this case", "the cost of not doing it is
   high", "no existing path already covers it").
3. **Counter it — three probes:**
   - **Needed?** Do users really hit this? What concretely breaks if we **don't**
     do it? Is the pain hypothetical?
   - **Missed?** Is there a blind spot — an ignored case, stakeholder, or cost;
     a simpler problem underneath this one?
   - **Another way?** Is there a cheaper / smaller / already-built path to the
     same value? (Reuse over new code, config over feature, doc over tool.)
     **Make this probe empirical, not rhetorical:** a claimed consumer or
     capability is a *hypothesis* — before assuming new code is needed, run the
     cheapest disconfirming PoC: `grep` the engine / codebase for machinery that
     already covers it. ("Is the wt-close gate already enforced?" → one grep for
     `_check_clean` answered yes — but only at S6, after the build, not at S0.)
4. **Assess impact.** If a counter holds, what changes? Resolve explicitly —
   **proceed** (counter doesn't hold, say why), **descope**, **redirect**, or
   **drop**. Record the resolution so a reviewer sees the decision, not just the
   doubt.

Optional, when the value is contested: fallacy tags on the original rationale;
scope-testing (claimed vs assumed vs unsupported); second-order effects of
shipping it.

## Worked Example

**HATS-629 (the epic this stage belongs to).** The original plan proposed a
phased split of the unified plan-gate. A counter-pass steelmanned it
("phases isolate risk"), named the assumption ("each phase delivers value
alone"), countered it ("the section is useless without its method — M3 ships
both or neither"), and assessed impact (**redirect**: ship section + method
together). The split was refuted *before* execute — exactly the
"right scope, wrong direction" save this stage exists for.

## Rationalization red-flags

The skeptic pass is the easiest stage to talk yourself out of. These are the
rationalizations that precede a skipped or hollow counter:

| Rationalization (what you tell yourself) | Why it's wrong |
|---|---|
| "This is obviously worth doing — write N/A" | Reflex `N/A` on a non-trivial plan is the exact failure this stage prevents; a steelman + one real counter costs little |
| "I already know the answer is proceed" | Then *record* the counter you considered and why it fails — a resolution, not a skipped step |
| "Questioning it now will just slow us down" | Redirecting before execute is cheap; rebuilding after shipping the wrong thing is not |
| "This counter already ran at authoring — no need to re-run" (on a resumed / bounced / premise-changed plan) | The premise that changed is exactly what the prior counter never tested; a resumed plan gets a fresh counter scoped to what moved (see `rule_backlog_discipline §6` — a retracted premise bounces the task back here) |

**Red-flag words in your own reasoning:** "obviously", "clearly worth it", "no
point questioning", "we already decided", "already decided at authoring", "still
ready to implement". Any of these → write a real steelman → assumption → counter →
impact, even when the impact is *proceed*.
(Rationalization-table discipline adapted from obra/superpowers, MIT.)

## Anti-Patterns

- **Reflex `N/A` on a non-trivial plan.** The section is optional so the engine
  won't stop you — but skipping the skeptic pass on real work is the failure
  mode this stage exists to prevent.
- **Strawmanning your own approach.** Skipping step 1 makes the counter cheap
  and the conclusion foregone. Steelman first.
- **Challenging scope here.** "This abstraction is speculative" is
  `design-minimalism`'s job (HOW MUCH). This stage asks whether the *value* is
  real (WHY), not whether the means are minimal.
- **Reviewing output.** `audit-reviewer` / judge skills critique work already
  done; this challenges the **decision before** work starts.
- **Doubt without resolution.** A counter with no recorded impact/decision is
  noise. Every counter ends in proceed / descope / redirect / drop, with a why.

## Completion

- `Approach & counter` carries a real counter (steelman → assumption → counter →
  impact) **or** an explicit `N/A — <reason>`.
- Each surviving counter has a recorded resolution (proceed / descope / redirect
  / drop) with rationale.
- The value handed to `design-minimalism` for scoping is one that survived the
  pass — not the unexamined original.

## See also

- `requirements-interview` — the prior stage; *states* the value this challenges.
- `design-minimalism` — the next stage; minimises the means once the value settles.
- `trait-agent` "Anti-Anchoring" — the base principle this stage operationalises.
- `audit-reviewer` — reviews output *after* work; this challenges the decision *before*.
