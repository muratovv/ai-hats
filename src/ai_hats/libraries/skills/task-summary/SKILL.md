# Task Summary

Produce a focused summary of a completed task, capturing only decision-critical knowledge.

## When to Use
- After backlog-manager transitions a task to `done` or `failed`
- When handing off a completed task for review
- When supervisor asks "what happened with task X?"

## Procedure

1. **Identify the task:** Locate the task card in `.agent/backlog/tasks/<ID>/`.

2. **Extract decision-critical information:**
   - **Architectural decisions** — what structural choices were made and WHY
   - **Decision forks** — where were there multiple viable options?
     Document: what options existed, which was chosen, and the key reason
   - **Pitfalls discovered** — non-obvious traps that cost time or could bite again
   - **Deviations from plan** — where did execution diverge from the original plan and why

3. **Write the summary** to `.agent/backlog/tasks/<ID>/summary.md`:

```markdown
# Summary: <task title>

Completed: YYYY-MM-DD
Result: <done | failed>

## What Was Done
<2-5 sentences. Outcome, not play-by-play.>

## Key Decisions
| Decision | Why This Way | Alternative Rejected |
|----------|-------------|---------------------|
| ... | ... | ... |

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
