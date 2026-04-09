---
name: scope-guard
description: Enforce user-defined task boundaries, prevent scope creep and over-implementation
---
# Scope Guard

Enforce user-defined task boundaries. Prevent scope creep and over-implementation.

## When to Use
- After receiving a task with explicit constraints ("only X", "don't do Y", "focus on Z")
- Before starting each new sub-step during execution
- When tempted to add "helpful" work not explicitly requested

## Checklist

### Before Starting Execution
1. **Extract constraints:** List every explicit limitation from the user's request
   (e.g., "signatures only", "no tests", "just the plan")
2. **Record constraints** in plan.md under a `## Scope Constraints` section
3. **Define "done":** What is the minimum deliverable that satisfies the request?

### Before Each Action
4. **Scope check:** Is this action within the recorded constraints?
   - YES → proceed
   - NO → go to step 5
5. **Escalate, don't decide:** If you believe a constraint should be violated
   (e.g., minimal implementation needed to compile), ASK the user first.
   Never silently expand scope.

### Execution Checkpoints
6. **Checkpoint rule:** After every **5 significant tool calls** (Edit, Write, Bash that changes state),
   pause and deliver a brief status:
   - What was done so far (1-2 lines)
   - What's next (1-2 lines)
   - Any deviations from plan
7. **Large task threshold:** If executing 10+ tool calls in a single response,
   STOP after 5 and checkpoint. Do not continue silently.
8. **Plan revision: prefer Write over many Edits.** When revising a plan file
   after a rejected `ExitPlanMode` (in `~/.claude/plans/*.md` or
   `.agent/backlog/tasks/*/plan.md`): if more than 2 distinct sections of the
   plan need to change, use a single `Write` to rewrite the whole file rather
   than 3+ sequential `Edit` calls. Plan files are short enough that a full
   rewrite costs the same as the diffs but avoids per-edit permission and
   round-trip overhead for the supervisor.
9. **"discuss before implement" gate:** On non-trivial tasks, present the approach
   before executing. One paragraph, not a wall of text. Wait for confirmation.

### After Completing Work
6. **Scope audit:** Compare what you delivered against the original constraints.
   Did you do more than asked? Flag it.

## Completion
- Constraints recorded in plan.md
- Every action within recorded constraints, or user approved the deviation
- No unrequested work delivered without explicit approval

## Anti-Patterns
- "I'll just add this helper since I'm here" — scope creep
- Justifying scope expansion internally without asking — the user decides, not you
- Writing full implementations when asked for signatures/interfaces
- Writing tests when asked for design only
- 30+ tool calls in a single response without a checkpoint — the user loses visibility
- Skipping "discuss approach" on tasks with multiple implementation options
