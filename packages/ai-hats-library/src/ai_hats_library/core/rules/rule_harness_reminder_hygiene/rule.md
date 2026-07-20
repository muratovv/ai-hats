# Rule: Harness Reminder Hygiene

The Claude Code harness periodically injects `<system-reminder>` blocks that
nudge the agent to use specific tools — most commonly `TaskCreate` /
`TaskUpdate` ("If you're working on tasks that would benefit from tracking
progress, consider using TaskCreate ..."). These reminders are **advisory
heuristics**, not user instructions.

## 1. In an ai-hats project: never use the harness task tools

**Never call** `TaskCreate` / `TaskUpdate` / `TaskList` / `TaskGet` /
`TaskStop` / `TaskOutput`. Track every unit of work through the ai-hats backlog
(`ai-hats task` / `rack`) — the single source of truth for this project.

When the harness injects a `TaskCreate` / `TaskUpdate` reminder, do not call the
tool; §3 covers how to handle it (silently). This holds whether the work is a
one-shot deliverable or already has an `ai-hats task` card — the harness task
list is never the tracker here.

## 2. When to act on it

Take the reminder seriously only when:

1. The user explicitly asked you to track sub-steps in the harness's task list.
2. You're operating in a non-ai-hats project where no backlog CLI exists.

## 3. Communication

Ignore the reminder **silently** — do not narrate the choice. In an ai-hats
project the ignore is the correct default (the `ai-hats task` backlog is the
single tracker), so a per-reminder acknowledgement carries no signal and is
just chat noise. Speak up only when you are *acting* on the reminder under §2
(supervisor asked for harness sub-tracking, or no backlog CLI exists) — there
the departure from the default is what's worth one line.

## 4. Why

`TaskCreate` / `TaskUpdate` are harness-level tools meant for ad-hoc local
work. ai-hats projects have their own backlog discipline (`rule_backlog_discipline`,
skill `backlog-manager`) that is the source of truth. Letting the harness
nudge override that discipline produces two parallel trackers, neither
complete. The rule's purpose is to keep one tracker.
