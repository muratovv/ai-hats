---
name: context-reset
description: Clean context reset protocol covering commit work, write handoff, update task card, and inform supervisor. Use when the context window is approaching its limit (system signals compression), when switching to a fundamentally different task domain, or when explicitly requested by the supervisor.
---
# Context Reset

Protocol for cleanly resetting agent context when the context window fills up or a fresh start is needed.

## When to Use
The full *commit → handoff → card-update → clear* protocol, triggered by
**context pressure or a domain switch** — not by finishing a task. For
end-of-task wrap-up where context is still healthy, use **task-summary** (the
record) or just transition the card; you don't need a reset. The handoff-file
step itself is **context-handoff**, which this protocol invokes — call that one
directly if you want a handoff without clearing context.

## Procedure

1. **Assess current state:**
   - Is there uncommitted work? → commit or stash first
   - Is there an active task card? → note current state in `work_log`
   - Are there open files or running processes? → clean up

2. **Run context-handoff:**
   - Execute **context-handoff** to write a handoff file
   - For active tasks: write to `<ai_hats_dir>/tracker/backlog/tasks/<ID>/handoff.md`
   - For general sessions: write to `<ai_hats_dir>/sessions/handoffs/YYYY-MM-DD-<title>.md`

3. **Update task card** (if exists):
   - Set state to `blocked` with reason: `context-reset`
   - Add `handoff_file` path to `work_log`

4. **Report to supervisor:**
   - Print the handoff file path
   - Print brief summary: what was done, what remains
   - Suggest the command to resume (e.g., "continue task HATS-XXX using handoff at <path>")

## Completion
- All work committed or stashed
- Handoff file written via **context-handoff**
- Task card updated (if applicable)
- Supervisor informed with resume instructions

## Anti-Patterns
- Resetting without saving context — the next agent starts blind
- Leaving uncommitted changes — work is lost on context switch
- Writing the handoff AFTER reset — too late, context is already gone
