---
name: plan-discipline
description: Get a plan into its one canonical home — the tracker plan.md — and never route it through .claude/plans. Use when you have a plan draft (including plan-mode / ExitPlanMode output), are about to persist a plan, or a task enters brainstorm→plan and the plan must be written down.
---
# Plan Discipline

A plan is always a task, authored directly into `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`. Nowhere else.

## When to Use
- Use the moment a plan needs to become real — a draft in chat, or a plan-mode
  artifact — and must be persisted. Fires even when no task exists yet.
- This skill is about the plan's **home and transport** (where the file lives,
  how the draft gets there). For filling each section *well*, hand off to
  **plan-gate** (it routes Requirements / Scope / Steps / Verification to their
  owning skills). Don't duplicate that here.
- Not an enforcement gate: the engine per-section gate (HATS-635) blocks
  `transition execute` on an empty plan. This skill is the authoring discipline
  upstream of it; the gate is the backstop.

## Procedure
1. **A plan is a task.** If none exists, `ai-hats task create …` first. Run all
   `task` / `wt` CLI from the **main repo** — the tracker lives under the
   gitignored `.agent/`, so a linked worktree has no real tracker.
2. **Scaffold the canonical file.** `ai-hats task transition <ID> plan` writes the
   empty `tasks/<ID>/plan.md`.
3. **Author straight into it** with Write/Edit. If you already have a draft
   (chat, or a `.claude/plans/<NN>-*.md` that plan-mode dropped), copy its content
   in: Read the draft → Write the tracker `plan.md`. There is **no sync** — the
   `.claude/plans` import path was removed (HATS-637); a file there is inert.
4. **Fill the required sections** (route via `plan-gate`). `transition <ID> execute`
   stays blocked until each is non-empty.
5. The `.claude/plans` draft is now scratch — leave or delete it; it is never the
   plan of record.

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
