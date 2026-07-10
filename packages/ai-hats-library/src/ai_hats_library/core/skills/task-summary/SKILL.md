---
name: task-summary
description: Focused post-task summary covering architectural decisions, decision forks, pitfalls, and plan deviations. Use after backlog-manager transitions a task to done or failed, when handing off a completed task for review, or when the supervisor asks what happened with a task.
license: MIT
---

# Task Summary

Produce a focused summary of a completed task, capturing only decision-critical knowledge.

## When to Use

The backward-looking *factual record* of a finished task — what was decided and
why. Two siblings to keep distinct:

- **self-retrospective** is the *improvement* analysis (5-whys, systemic fixes)
  of how the work went — run that when there were failures or backtracks; this
  skill just records the outcome.
- **context-handoff** is the *forward* brief for whoever continues the work —
  task-summary looks back, handoff looks ahead.

## Procedure

1. **Identify the task:** Locate the task card in `<ai_hats_dir>/tracker/backlog/tasks/<ID>/`.

2. **Extract decision-critical information:**
   - **Architectural decisions** — what structural choices were made and WHY
   - **Decision forks** — where were there multiple viable options?
     Document: what options existed, which was chosen, and the key reason
   - **Pitfalls discovered** — non-obvious traps that cost time or could bite again
   - **Deviations from plan** — where did execution diverge from the original plan and why

3. **Write the summary** to `<ai_hats_dir>/tracker/backlog/tasks/<ID>/summary.md`:

```markdown
# Summary: <task title>

Completed: YYYY-MM-DD
Result: <done | failed>

## What Was Done

<2-5 sentences. Outcome, not play-by-play.>

## Key Decisions

| Decision | Why This Way | Alternative Rejected |
| -------- | ------------ | -------------------- |
| ...      | ...          | ...                  |

## Pitfalls

- <Non-obvious trap> — <impact or how to avoid>

## Deviations from Plan

- <What changed> — <why>
```

4. **Update task card:** Add `summary_file: summary.md` to task.yaml metadata.

## What NOT to Include

- Step-by-step log of actions (that's `work_log` in the task card)
- Routine operations (git commits, file reads, linting)
- Information already in the code or commit messages
- Full error traces (reference file paths instead)

## Completion

- Summary file written to task directory
- Contains at least: What Was Done, Key Decisions
- Task card updated with summary reference

## Anti-Patterns

- Summarizing everything — the point is filtering, not transcription
- Missing the WHY — a list of facts without reasoning helps nobody
- Writing the summary without reading the task's work_log first — you'll miss context
