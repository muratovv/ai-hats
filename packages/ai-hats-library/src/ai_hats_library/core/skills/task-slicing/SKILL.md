---
name: task-slicing
description: Tracer-bullet slicing heuristics for breaking work into tasks. Use when filling plan.md's Steps section (routed from plan-gate), decomposing an epic into child tasks before plan-extract, or carving a mid-execute remainder into successor tasks.
license: MIT
---

# Task Slicing

Break work into tracer-bullet slices: each slice cuts a narrow but complete
path to a verifiable result, sized to one session.

## When to Use

- Owns the `Steps` section of plan.md — plan-gate routes here.
- NOT the test-writing rule: "vertical slicing" in `trait-se-mindset` governs
  one-test-at-a-time TDD; this skill slices *work into tasks*.
- Mid-execute remainder ("tail doesn't fit the context"): shape the tail with
  these rules; `context-reset` step 3 carries it out via `plan-extract`.

## Slice rules

- **Narrow but complete.** A slice lands a thin end-to-end path through every
  layer it touches — verifiable or demoable on its own, never one layer of a
  future assembly. (Positive templates: HATS-526, HATS-873, HATS-604→605.)
- **One session per slice.** A fresh agent lands it green in a single session;
  each slice boundary is a premise re-contact point (HATS-795: 920 LoC over
  3 sessions on a collapsed premise).
- **Prefactoring first.** "Make the change easy, then make the easy change" —
  a preparatory slice is its own step or child task, named as such.
- **Declare blocking edges.** Each child task names its blockers via
  `depends_on` (`ai-hats task link`); a task whose blockers are all done is on
  the **frontier** — takeable in parallel without coordination.
- **Don't pre-slice the unknown.** A question you can't state sharply yet is
  not a task — leave it in the plan until an earlier slice clears it.

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

Breakdown shown to the supervisor — title, blockers, what it delivers;
granularity, edges, and merge/split questions answered — then published via
`backlog-manager` `plan-extract`. Validation scenario: HATS-1002 task card.
