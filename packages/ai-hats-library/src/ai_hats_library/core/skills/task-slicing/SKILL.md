---
name: task-slicing
description: Wire a work breakdown into the tracker — depends_on edges, frontier, expand–contract for wide refactors. Use when filling plan.md's Steps section (routed from plan-gate), decomposing an epic into child tasks, or carving a mid-execute remainder into successor tasks.
license: MIT
---

# Task Slicing

Slices are tracer bullets — each a narrow but complete path to a verifiable
result, sized to one session. This skill carries what that looks like in
*this* tracker, and the one sequence that must not be sliced naively.

## When to Use

- Owns the `Steps` section of plan.md — plan-gate routes here.
- NOT the test-writing rule: "vertical slicing" in `trait-se-mindset` governs
  one-test-at-a-time TDD; this skill slices *work into tasks*.
- Mid-execute remainder ("tail doesn't fit the context"): `context-reset`
  step 3 carries it out via `## Steps` items + `plan-extract`.

## Wiring the breakdown

- **Declare blocking edges.** Each child task names its blockers via
  `depends_on` (`ai-hats task link`); a task whose blockers are all done is on
  the **frontier** — takeable in parallel without coordination.
- **Gate before publishing.** Show the supervisor the breakdown — title,
  blockers, what it delivers; granularity, edges, merge/split answered — then
  publish via `backlog-manager` `plan-extract`.

## Wide refactors: expand–contract

One mechanical change whose blast radius fans across the codebase (rename,
retype, package split) can't land as one green tracer bullet. Sequence it:

1. **Expand** — add the new form beside the old; nothing breaks.
2. **Migrate** — convert call sites in batches sized by blast radius (per
   package / directory), one child task per batch, blocked by the expand,
   CI green batch to batch.
3. **Contract** — delete the old form when no caller remains; blocked by
   every migrate batch.

Batches that can't stay green alone share an integration branch plus a final
integrate-and-verify task — green is promised only there. (Retrofitted after
review in HATS-858; HATS-831's 470-line/88-importer mid-execute move is the
failure mode.)

## Completion

Steps section holds one-session slices with edges declared; any wide refactor
is sequenced expand–contract. Validation scenario: HATS-1002 task card.
