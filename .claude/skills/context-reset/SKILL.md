---
name: context-reset
description: Clean context reset protocol — commit work, write handoff, update task card, inform supervisor
---
# Context Reset

Protocol for cleanly resetting agent context when the context window fills up or a fresh start is needed.

## When to Use
- Context window approaching limit (system signals compression)
- Switching to a fundamentally different task domain
- Explicitly requested by supervisor

## Procedure

1. **Assess current state:**
   - Is there uncommitted work? → commit or stash first
   - Is there an active task card? → note current state in `work_log`
   - Are there open files or running processes? → clean up

2. **Run context-handoff:**
   - Execute **context-handoff** to write a handoff file
   - For active tasks: write to `.agent/backlog/tasks/<ID>/handoff.md`
   - For general sessions: write to `.agent/handoffs/YYYY-MM-DD-<title>.md`

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
