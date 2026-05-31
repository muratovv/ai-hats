---
name: review-role
description: Audit a role composition for coherence — injection/skill/rule mismatches and composer gaps
---
# Review Role

Audit a role composition for internal coherence — find injection ↔ skill
contradictions, role-binding errors in skills, dead-code priorities,
double-injected ROLE blocks, and similar inconsistencies. Output is a
report listing findings with concrete fix proposals (rule edits, skill
edits, composer changes).

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You are auditing one role (e.g. `judge`, `reflect-session`, `assistant`)
and want to surface coherence problems before they cause runtime drift.
Triggered by `ai-hats reflect role <role-name>` or invoked manually.

## Procedure (skeleton — full body to follow)

1. **Compose the role** and capture the full materialized prompt.
2. **Enumerate components** — traits, rules, skills, injection blocks,
   priorities — with their source files.
3. **Run coherence checks** across these axes:
   - injection ↔ skill statements (do they agree?)
   - skill ↔ skill (any role-binding mismatch?)
   - rule ↔ rule (duplication of constraints?)
   - injection ordering (attention placement) — high-signal constraints
     (`## Guardrails`, critical rules) must sit early or late, not buried in
     the middle of a long injection where recall degrades ("lost-in-the-middle"
     / U-shaped attention, Liu et al. 2023, arXiv:2307.03172). Flag any
     guardrail or critical constraint stranded mid-prompt.
   - composer artifacts (double ROLE injection, missing priority injection,
     skip-routing gaps for the active role)
   - tool-call hygiene (any banned bash patterns in skill bodies?)
4. **Emit findings.** Each finding has: id, axis, verbatim quote(s), fix
   proposal. Group by sharpness (locally fixable vs requires composer
   change vs requires trait restructuring).
5. **Persist** the report at
   `<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ISO-ts>-<role>.md`.

## Output handoff

The report is the artifact. Filed proposals (via **review-proposal**) feed
into the proposal inbox; concrete fix tasks are filed via
`ai-hats task create` per **backlog-manager**.

> **Status: skeleton.** Full body — checklist of axes, finding format,
> rubric for sharpness — to be elaborated in a follow-up task.
