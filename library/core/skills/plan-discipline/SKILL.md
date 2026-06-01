---
name: plan-discipline
description: Get a plan into its one canonical home — the tracker plan.md — never .claude/plans. Run this skill the moment you enter plan mode (EnterPlanMode); also when you have a plan draft (incl. plan-mode / ExitPlanMode output) or a task enters brainstorm→plan. It walks task creation and draft→tracker authoring.
---
# Plan Discipline

A plan is always a task, authored directly into `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`. Nowhere else.

## When to Use
- **On `EnterPlanMode`, run this skill first** — before drafting. Plan mode is for
  *designing*; the plan of record must still become a task in the tracker, not
  loose plan-mode text or a `.claude/plans` file.
- Also whenever a plan must be persisted — a draft in chat or a plan-mode
  artifact — even if no task exists yet.
- This skill is the plan's **home and transport** (where the file lives, how the
  draft gets there). For filling each section *well*, hand off to **plan-gate**
  (it routes Requirements / Scope / Steps / Verification to their owners). Don't
  duplicate that here.
- Not an enforcement gate: the engine per-section gate (HATS-635) blocks
  `transition execute` on an empty plan. This skill is the authoring discipline
  upstream of it; the gate is the backstop.

## Procedure

Run all `task` / `wt` CLI from the **main repo** — the tracker lives under the
gitignored `.agent/`, so a linked worktree has no real tracker.

1. **Enter plan mode → start here.** Design freely, but treat any plan-mode chat
   or `.claude/plans/<NN>-*.md` as throwaway scratch — not the plan of record.
2. **Make the task.** If one doesn't exist yet, create it:
   ```
   ai-hats task create "<title>" -d "<one-line intent>" [-p high|medium|low] [--id PROJ-NNN]
   ```
   New tasks start in `brainstorm`; clarify scope there if it's fuzzy, then move on.
3. **Scaffold the canonical file.** `ai-hats task transition <ID> plan` writes the
   empty `tasks/<ID>/plan.md`.
4. **Author the plan straight into it** with Write/Edit. If plan mode produced a
   draft (chat, or a `.claude/plans/<NN>-*.md`), copy its content in — Read the
   draft → Write the tracker `plan.md`. There is **no sync**: the `.claude/plans`
   import path was removed (HATS-637), so a file there is inert.
5. **Fill the required sections** (route each via `plan-gate`).
   `ai-hats task transition <ID> execute` stays blocked until each is non-empty.
6. **The `.claude/plans` draft is now scratch** — leave or delete it; it is never
   the plan of record.

## Completion
- `tasks/<ID>/plan.md` holds the real plan; no task-bearing file remains in
  `.claude/plans`; `transition <ID> execute` passes the gate.
- Handoff: plan in tracker → `plan-gate` (section quality) → engine gate → execute.

## Anti-Patterns
- Treating a plan-mode `.claude/plans/<NN>-*.md` as the plan — it is inert scratch;
  ai-hats does not read it.
- Drafting a plan without a task — if it's a plan, you made a task.
- Expecting `plan-sync` / any auto-import — removed in HATS-637; author directly.
- Running `task` / `transition` from inside a worktree — the gitignored tracker
  isn't there; ids and state desync. Use the main repo.
