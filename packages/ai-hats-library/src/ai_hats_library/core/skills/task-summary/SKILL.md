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

3. **Draft the summary** at a scratch path outside the backlog — e.g.
   `/tmp/<ID>-summary.md`. Do not write it under `tasks/<ID>/` yourself;
   step 4 puts it there:

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

4. **Attach it to the card:**

   ```bash
   ai-hats task attach add <ID> /tmp/<ID>-summary.md --name summary.md
   ```

   The file lands at `tasks/<ID>/attachments/summary.md` and the manifest entry
   (name + digest) goes into the card. `attach add` is the only sanctioned way in:
   `rule_backlog_discipline §1` guards the whole `tasks/<ID>/**` subtree, carving
   out `plan.md` alone. Re-running with identical content is a no-op; different
   content under the same name is a hard error — `attach remove` first.

## What NOT to Include

- Step-by-step log of actions (that's `work_log` in the task card)
- Routine operations (git commits, file reads, linting)
- Information already in the code or commit messages
- Full error traces (reference file paths instead)

## Completion

- Summary drafted outside the backlog tree, then attached via `task attach add`
- Contains at least: What Was Done, Key Decisions
- `ai-hats task attach list <ID>` shows `summary.md`

## Anti-Patterns

- Summarizing everything — the point is filtering, not transcription
- Missing the WHY — a list of facts without reasoning helps nobody
- Writing the summary without reading the task's work_log first — you'll miss context
- Writing the summary under `tasks/<ID>/` by hand — `summary.md` there, or a
  `summary_file:` key in task.yaml. Both break `rule_backlog_discipline §1`, and no
  CLI writes either. `attach add` is the door (HATS-1007)
