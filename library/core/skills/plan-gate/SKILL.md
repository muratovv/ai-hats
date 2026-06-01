---
name: plan-gate
description: The single entry point for the brainstorm→plan quality gate — a table of contents mapping each required plan section to the skill that fills it. Use when a task enters brainstorm→plan or when drafting/revising plan.md, to route each section to its owning skill before requesting plan→execute.
---
# Plan Gate

The one named gate for `brainstorm → plan`. The engine writes a plan scaffold
whose required sections are enforced at plan→execute by the per-section gate
(HATS-635) — it blocks the transition and names any section left empty. This
skill is the table of contents for filling them: it says which skill owns which
section. It does **not** re-implement those skills.

## When to Use
- Read this FIRST at `brainstorm → plan`, then invoke the per-section skills below — it is the single entry point that replaces the rival triggers the stage skills used to carry.
- Prerequisite: the plan already lives in the tracker `plan.md`. Getting it there (home + draft→tracker transport, never `.claude/plans`) is skill **plan-discipline**; this skill only fills the sections.
- Not a quality oracle: it routes, the owning skill judges. Don't put requirements/scope logic here.
- Trivial task: a section may be filled directly or marked `N/A — <reason>`; the engine gate enforces non-emptiness, not depth.

## Workflow

Fill each required section of `plan.md` via its owning skill, top to bottom. The
left column is the engine's `PLAN_SECTIONS` (a sync-test asserts this table
stays identical to it — see `tests/test_plan_gate.py`):

| Plan section | Owning skill / how to fill |
|---|---|
| Requirements | `requirements-interview` — per question: collect context → propose a cited best-guess → supervisor reviews. |
| Scope & Out-of-scope | `design-minimalism` — every primitive justified by a current-epic use case; speculative ideas → Out of scope. |
| Steps | Self-authored ordered list; `backlog-manager` `plan-extract` to split into child tasks once headings stabilise. |
| Verification Protocol | Self-authored — the concrete checks that prove the work (tests, in-process composition, lint). |

The gate fires on plan→execute and reopens nothing already passed. This skill's
job is upstream: ensure each section has an owner so none is filled by guesswork
or left blank for the gate to reject.

## Completion
- Every required section in `plan.md` is filled by (or explicitly `N/A`'d through) its owning skill.
- The plan→execute transition passes the per-section gate (no `EmptyPlanError`).

## Anti-Patterns
- Treating plan-gate as the judge — it is a router; the owning skill does the work.
- Copying requirements-interview / design-minimalism logic into this file — keep it a table of contents.
- Editing the section table without matching the engine's `PLAN_SECTIONS` — the sync-test fails loudly on drift.
