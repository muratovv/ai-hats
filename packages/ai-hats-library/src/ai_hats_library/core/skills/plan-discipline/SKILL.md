---
name: plan-discipline
description: Put a plan in its one canonical home, the tracker `plan.md`, never `.claude/plans`. Use on entering plan mode, on any plan draft including ExitPlanMode output, or when a task enters brainstorm→plan.
license: MIT
---

# Plan Discipline

A plan is always a task, authored into `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`. Nowhere else.

## When to Use

- **On `EnterPlanMode`, recall this skill** — so you treat the plan-mode draft as
  scratch and persist it into the tracker the moment you exit (see the two-phase
  flow below). Plan mode is for *designing + approval*; the plan of record still
  becomes a task in the tracker.
- Also whenever a plan must be persisted — a draft in chat or a plan-mode
  artifact — even if no task exists yet.
- This skill is the plan's **home and transport** (where the file lives, how the
  draft gets there). For filling each section *well*, hand off to **plan-gate**
  (it routes Requirements / Scope / Steps / Verification to their owners). Don't
  duplicate that here.
- Not an enforcement gate: the engine per-section gate blocks
  `rack transition <ID> execute` on an empty plan. This skill is the authoring discipline
  upstream of it; the gate is the backstop.

## Procedure

`rack` CLI commands (reads, transitions, logs) can be run from either the main repo or any linked worktree — root resolution automatically finds the main repo's `.agent/` tracker. (Teardown commands like `ai-hats wt merge` / `discard` or closing `transition done` should be run from the main repo so your shell cwd isn't removed underfoot — see skill **worktree-isolation**).

### Preferred — plan directly in the tracker (no plan mode)

When you control the flow, skip Claude Code plan mode and author straight into the
tracker — zero round-trip, no `.claude/plans` file.

1. **Make the task** (if none): `rack create "<title>" --description "<intent>" [--priority high|medium|low] [--id PROJ-NNN]` — starts in `brainstorm`; clarify scope there if fuzzy.
2. **Scaffold:** `rack transition <ID> plan` → empty `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`.
3. **Author into it** (Write/Edit), filling the required sections (route each via
   `plan-gate`). `rack transition <ID> execute` stays blocked until they're non-empty.

### In Claude Code plan mode — two phases

Plan mode is **read-only**: it blocks the `rack` CLI and every write except
`.claude/plans/<slug>.md`, so the tracker flow is impossible *until you exit*.
That's expected — don't fight it, and don't apologise for the draft.

- **Phase 1 — in plan mode:** design; draft into `.claude/plans/<slug>.md`;
  present via `ExitPlanMode`. Do **not** attempt `rack create` / `rack transition`
  / tracker writes — they are blocked. The draft is scratch, not the plan of record.
- **Phase 2 — immediately on approval / exit:** your **first** action, before any
  other execute work, is to persist into the tracker — `rack create` (if needed)
  → `rack transition <ID> plan` → Read the `.claude/plans` draft → Write it into
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md` → fill/confirm sections → `rack transition <ID> execute`. There
  is no auto-sync; the `.claude/plans` file is now inert, leave or delete.

## Completion

- `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md` holds the real plan; no task-bearing file remains in
  `.claude/plans`; `rack transition <ID> execute` passes the gate.
- Handoff: plan in tracker → `plan-gate` (section quality) → engine gate → execute.

## Anti-Patterns

- Treating the plan-mode `.claude/plans/<slug>.md` as the plan — in plan mode it is
  Phase-1 scratch; the plan isn't real until transferred to the tracker on exit.
- Skipping the Phase-2 transfer (or deferring it behind other execute work) —
  persisting into the tracker is the **first** post-approval action.
- Fighting plan mode by trying `rack` CLI / tracker writes while still in it — they
  are blocked; draft, exit, then persist.
- Drafting a plan without a task — if it's a plan, you made a task.
- Running worktree teardown commands (`ai-hats wt merge` / `wt discard` or terminal `transition done`) from inside the worktree being deleted — this orphans your shell. Return to the main repo first.

