---
name: requirements-interview
description: The Requirements stage of the plan-gate — fills plan.md's Requirements section via structured Q&A where the agent proposes a cited best-guess per question and the supervisor reviews. Reached through plan-gate (which owns the brainstorm→plan entry), not as an independent trigger.
license: MIT
---

# Requirements Interview

The **Requirements stage of `plan-gate`**: structured Q&A that fills the
`Requirements` section of `plan.md` before a task leaves brainstorm.
**The interview is review-shaped, not interrogation-shaped:** for every
question, the agent proposes a best-guess answer grounded in the
codebase/docs and the supervisor confirms or overrides.

## When to Use

Reach this stage through `plan-gate`, not as a standalone brainstorm→plan
trigger — `plan-gate` is the single entry point and routes here to fill the
`Requirements` section.

- Run it when that section is empty and the request leaves >2 independent assumptions about user intent.
- Skip when requirements are already unambiguous from the request — fill the section directly.
- Sibling stage: `design-minimalism` owns the adjacent `Scope & Out-of-scope` section — don't do scope-justification here.
- Next stage: once you've stated the value (Q1 "Goal & user value"), `devils-advocate` challenges it in `Approach & counter` — *is it needed? did we miss anything? another way?* Loop with it until the value settles, then hand off to scope.

## Checklist

Walk through these with the user. Skip any that are already unambiguous from
the request. Ask in order; stop when you have enough to plan.

### Procedure for each question — collect, propose, review

Before voicing any question from the list below:

1. **Collect context.** Read / Glob / Grep relevant files (existing
   patterns, similar tasks in the backlog, docs/, ADRs, task.yaml of
   related cards). One targeted pass — not a full codebase tour.
2. **Propose a best-guess answer with a source citation.** Format:
   *"My current best guess: <answer>, because `<path>:<line>` shows
   `<verbatim or paraphrase>`."* If the source is a prior task or HYP,
   cite the ID. If context is genuinely silent, say *"context silent
   on this — please supply"* and only then ask the open question.
3. **Supervisor reviews.** Supervisor confirms, corrects, or overrides.
   The interview becomes a one-turn review per question, not a
   multi-round interrogation.

**Without step 1, the recommended answer is hallucination.** A
best-guess pulled from training-data prior is exactly what we are
trying to replace with grounded review.

### Questions

1. **Goal & user value** — Who is this for, and what changes for them when it's done?
2. **Acceptance criteria** — How will we know it's done? List 2-4 concrete checks.
3. **Edge cases & failure modes** — What should happen on errors, empty inputs,
   concurrency, or partial state?
4. **Non-functional requirements** — Performance, reliability, compatibility,
   observability needs?
5. **Constraints & non-goals** — What is explicitly out of scope? What must NOT change?
6. **Dependencies & blockers** — Anything we're waiting on (other tasks, systems, people)?
7. **Kill criteria** — Under what condition should we abandon the task instead of
   shipping a partial version?

## Output

After the interview, update the task description so a fresh agent can pick it
up without re-asking:

```bash
ai-hats task update <ID> --description "<answers organized by section>"
```

Mirror the question headings (Goal, Acceptance, Edge cases, etc.) so future
readers see what was confirmed vs left N/A. **Each non-N/A answer carries
either a source citation (`path:line`, task ID, ADR ID) or an explicit
"supervisor override: <reason>"** — so the next agent can audit the
provenance of each line.

## Completion

- Task description has answers (or explicit `N/A — <reason>`) for questions 1-3 minimum
- Every non-N/A answer carries a source citation OR a "supervisor override" tag
- All user-mentioned constraints captured (cross-check with **scope-guard**)
- Plan can be drafted without material assumptions about user intent

## Anti-Patterns

- **Asking a question whose answer is visible in the code/docs.** Read
  first; ask only when context is silent or ambiguous. Cheap exploration
  costs less than a supervisor turn.
- **Recommended answer without a source citation.** A best-guess
  unanchored to `path:line` / task-ID / ADR is a hallucination dressed
  as review. The whole point of the citation is that supervisor can
  jump to the source in one click.
- **Citing a source that doesn't actually say what you claim.** Worse
  than no citation — it's a false anchor. Read the file you're citing.
- Asking all 7 questions when only 1-2 are unclear — wastes the user's time
- Accepting vague answers ("make it good") without a follow-up
- Skipping the interview on tasks described in one sentence — that's exactly
  when ambiguity is highest
- Deferring questions until plan/execute — a misaligned plan costs more to redo
  than a 2-minute interview
