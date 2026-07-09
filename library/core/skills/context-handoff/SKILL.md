---
name: context-handoff
description: Summarize critical context (decisions, forks, pitfalls) into a handoff file for the next agent. Use before a context reset (invoked by context-reset), when explicitly asked to save current context, or before delegating work to a sub-agent that needs background.
license: MIT
---

# Context Handoff

Summarize critical context and write a handoff file for the next agent or session.

## When to Use

Don't reach for this standalone during a full context reset — **context-reset**
calls it as its handoff-writing step. Invoke directly only when you need a
handoff *without* a reset: briefing a sub-agent you're about to delegate to, or
an explicit "save where we are" with no intent to clear context. For the
backward-looking "what was done" record of a *finished* task (not a forward
brief), use **task-summary** instead.

## Procedure

1. **Identify the handoff path:**
   - Task-scoped: `<ai_hats_dir>/tracker/backlog/tasks/<ID>/handoff.md`
   - Session-scoped: `<ai_hats_dir>/sessions/handoffs/YYYY-MM-DD-<title>.md`

2. **Collect only what matters.** Ignore routine actions. Focus on:
   - **Architectural decisions** — what was chosen and WHY (not just what)
   - **Decision forks** — alternatives considered, why rejected
   - **Pitfalls & gotchas** — traps the next agent will hit without warning
   - **Current state** — what is done, what is in progress, what is blocked
   - **Open questions** — unresolved ambiguities that need supervisor input

3. **Write the handoff file** using the format below.

4. **Verify** the file is written and readable.

## Format

```markdown
# Handoff: <task or session title>

Date: YYYY-MM-DD
Previous agent context: <brief identifier>

## Current State

<What is done. What is in progress. What is blocked.>

## Key Decisions

| Decision | Alternatives Considered | Why This Way |
| -------- | ----------------------- | ------------ |
| ...      | ...                     | ...          |

## Pitfalls

- <Trap 1> — <why it's dangerous>
- <Trap 2> — <why it's dangerous>

## Open Questions

- <Question that needs supervisor or further research>

## Next Steps

1. <Concrete next action>
2. ...
```

## What NOT to Include

- Play-by-play of every command run
- Routine git operations or file reads
- Information derivable from code or git history
- Full error logs (reference the file path instead)

## Completion

- Handoff file written to the correct path
- File contains at least: Current State, Key Decisions, Next Steps
- No routine noise — only decision-critical context

## Anti-Patterns

- Dumping full conversation history — the point is compression, not transcription
- Omitting the WHY behind decisions — facts without reasoning are useless for handoff
- Including information the next agent can get from `git log` or reading code
