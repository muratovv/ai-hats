---
name: requirements-interview
description: Structured Q&A to extract clear requirements before transitioning brainstorm → plan
---
# Requirements Interview

Structured Q&A to clarify requirements before a task moves out of brainstorm.

## When to Use
- During `brainstorm` state when the user describes a task in 1-2 sentences
- Before `task transition <ID> plan` — verify scope is fleshed out
- When you catch yourself making >2 independent assumptions about user intent

## Checklist

Walk through these with the user. Skip any that are already unambiguous from
the request. Ask in order; stop when you have enough to plan.

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
readers see what was confirmed vs left N/A.

## Completion
- Task description has answers (or explicit `N/A — <reason>`) for questions 1-3 minimum
- All user-mentioned constraints captured (cross-check with **scope-guard**)
- Plan can be drafted without material assumptions about user intent

## Anti-Patterns
- Asking all 7 questions when only 1-2 are unclear — wastes the user's time
- Accepting vague answers ("make it good") without a follow-up
- Skipping the interview on tasks described in one sentence — that's exactly
  when ambiguity is highest
- Deferring questions until plan/execute — a misaligned plan costs more to redo
  than a 2-minute interview
