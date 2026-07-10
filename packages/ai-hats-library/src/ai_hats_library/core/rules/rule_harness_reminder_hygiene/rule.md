# Rule: Harness Reminder Hygiene

The Claude Code harness periodically injects `<system-reminder>` blocks that
nudge the agent to use specific tools — most commonly `TaskCreate` /
`TaskUpdate` ("If you're working on tasks that would benefit from tracking
progress, consider using TaskCreate ..."). These reminders are **advisory
heuristics**, not user instructions.

## 1. When to ignore

Ignore the reminder — and do NOT call the suggested tool — when:

1. The current work has a single deliverable that does not split into trackable
   sub-tasks (review, audit, one-shot question, single-file edit).
2. The user-visible task is already tracked through the `ai-hats task` CLI
   (the canonical backlog for this project) — duplicating into the harness's
   own task tool fragments tracking.
3. The reminder fires mid-flow during a task whose state is already covered
   by the active `ai-hats task` card.

## 2. When to act on it

Take the reminder seriously only when:

1. The user explicitly asked you to track sub-steps in the harness's task list.
2. You're operating in a non-ai-hats project where no backlog CLI exists.

## 3. Communication

When you ignore a reminder mid-conversation, acknowledge it briefly in your
reply ("ignoring the harness TaskCreate reminder — current work is a single
audit pass, no sub-tracking value") so the user knows you saw it and made an
intentional choice. Do not silently drop it.

## 4. Why

`TaskCreate` / `TaskUpdate` are harness-level tools meant for ad-hoc local
work. ai-hats projects have their own backlog discipline (`rule_backlog_discipline`,
skill `backlog-manager`) that is the source of truth. Letting the harness
nudge override that discipline produces two parallel trackers, neither
complete. The rule's purpose is to keep one tracker.
