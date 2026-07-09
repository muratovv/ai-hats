---
name: scope-guard
description: "Enforce user-defined task boundaries to prevent scope creep and over-implementation. Use after receiving a task with explicit constraints (\"only X\", \"don't do Y\", \"focus on Z\"), before starting each new sub-step during execution, or when tempted to add helpful work not explicitly requested."
license: MIT
---

# Scope Guard

Enforce user-defined task boundaries. Prevent scope creep and over-implementation.

## When to Use

Execution-time guard against doing *more* than the user asked. Its sibling at
plan stage is **design-minimalism**, which strips speculative primitives from a
plan before any code exists; scope-guard instead holds the line on explicit user
constraints ("only X", "don't touch Y") *while* you execute. If you're weighing
whether an abstraction is justified, that's design-minimalism; if you're tempted
to bolt on unrequested "helpful" work, that's this skill.

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
   (`<ai_hats_dir>/tracker/backlog/tasks/*/plan.md` — the one canonical plan
   home): if more than 2 distinct sections of the
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

## Rationalization red-flags

Scope creep arrives as a thought you tell yourself *before* the action. Catch
the rationalization, not just the regret:

| Rationalization (what you tell yourself)            | Why it's wrong                                                                                                         |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| "I'll just add this helper while I'm here"          | Not requested — the user scopes, your convenience doesn't                                                              |
| "Skeleton/infra obviously means wire up everything" | Defaults to *only what was named*; ask with a concrete enumeration (HATS-265: switched 4 handlers, user rolled back 3) |
| "It won't compile without this extra bit"           | Maybe — but that is an escalation ("ASK first"), not a licence to expand silently                                      |
| "Faster to do it all now than to ask"               | The ask round-trip is cheaper than the rollback                                                                        |

**Red-flag words in your own reasoning:** "while I'm here", "just also", "might
as well", "obviously they'd want", "to be safe". Any of these → re-run the scope
check (step 4) and escalate (step 5) instead of proceeding.

**Forbidden workaround:** expanding scope and "flagging it" in the final summary
is *not* consent — flagging after the fact does not substitute for asking
before the action. (Rationalization-table discipline adapted from
obra/superpowers, MIT.)

## Anti-Patterns

- "I'll just add this helper since I'm here" — scope creep
- Justifying scope expansion internally without asking — the user decides, not you
- Writing full implementations when asked for signatures/interfaces
- Writing tests when asked for design only
- 30+ tool calls in a single response without a checkpoint — the user loses visibility
- Skipping "discuss approach" on tasks with multiple implementation options
- **"Skeleton / infrastructure / framework" signal misread.** When the user
  says "make a skeleton of X" or "set up the infrastructure for Y" or
  "wire up the framework call for Z" — interpret as **minimum viable demo +
  ask before expanding**, NOT as "wire up all related entry-points". A
  one-liner answer about scope without an explicit "everything" defaults to
  "only what was specifically named". Worked example: HATS-265 — user said
  "нужен скелет вызова ai-hats", agent switched 4 CLI handlers via
  pipeline-presets; user rolled back 3 of them. The correct ask is: "only X,
  X and Y, or all four?" with a concrete enumeration.
