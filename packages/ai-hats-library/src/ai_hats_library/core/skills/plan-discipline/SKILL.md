---
name: plan-discipline
description: Get a plan into its one canonical home — the tracker plan.md — never .claude/plans. Run this skill when you enter plan mode (EnterPlanMode), when you have a plan draft (incl. plan-mode / ExitPlanMode output), or when a task enters brainstorm→plan. It walks task creation and draft→tracker authoring, including the plan-mode two-phase flow.
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
- Not an enforcement gate: the engine per-section gate (HATS-635) blocks
  `transition execute` on an empty plan. This skill is the authoring discipline
  upstream of it; the gate is the backstop.

## Procedure

Run all `task` / `wt` CLI from the **main repo** — the tracker lives under the
gitignored `.agent/`, so a linked worktree has no real tracker.

### Preferred — plan directly in the tracker (no plan mode)

When you control the flow, skip Claude Code plan mode and author straight into the
tracker — zero round-trip, no `.claude/plans` file.

1. **Make the task** (if none): `rack create "<title>" --description "<intent>" [--priority high|medium|low] [--id PROJ-NNN]` — starts in `brainstorm`; clarify scope there if fuzzy.
2. **Scaffold:** `rack transition <ID> plan` → empty `tasks/<ID>/plan.md`.
3. **Author into it** (Write/Edit), filling the required sections (route each via
   `plan-gate`). `rack transition <ID> execute` stays blocked until they're non-empty.

### In Claude Code plan mode — two phases

Plan mode is **read-only**: it blocks `task` CLI and every write except
`.claude/plans/<slug>.md`, so the tracker flow is impossible *until you exit*.
That's expected — don't fight it, and don't apologise for the draft.

- **Phase 1 — in plan mode:** design; draft into `.claude/plans/<slug>.md`;
  present via `ExitPlanMode`. Do **not** attempt `task create` / `transition` /
  tracker writes — they are blocked. The draft is scratch, not the plan of record.
- **Phase 2 — immediately on approval / exit:** your **first** action, before any
  other execute work, is to persist into the tracker — `task create` (if needed)
  → `transition <ID> plan` → Read the `.claude/plans` draft → Write it into
  `tasks/<ID>/plan.md` → fill/confirm sections → `transition <ID> execute`. There
  is no auto-sync (HATS-637); the `.claude/plans` file is now inert, leave or delete.

## Completion

- `tasks/<ID>/plan.md` holds the real plan; no task-bearing file remains in
  `.claude/plans`; `transition <ID> execute` passes the gate.
- Handoff: plan in tracker → `plan-gate` (section quality) → engine gate → execute.

## Anti-Patterns

- Treating the plan-mode `.claude/plans/<slug>.md` as the plan — in plan mode it is
  Phase-1 scratch; the plan isn't real until transferred to the tracker on exit.
- Skipping the Phase-2 transfer (or deferring it behind other execute work) —
  persisting into the tracker is the **first** post-approval action.
- Fighting plan mode by trying `task` CLI / tracker writes while still in it — they
  are blocked; draft, exit, then persist.
- Drafting a plan without a task — if it's a plan, you made a task.
- Running `task` / `transition` from inside a worktree — the gitignored tracker
  isn't there; ids and state desync. Use the main repo.
