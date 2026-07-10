---
name: backlog-create
description: "Narrow shim for filing tasks via `ai-hats task create` from roles that mutate the backlog only at L1 (task-create only). Use when your role's mutation policy permits filing new tasks but forbids state transitions, hypothesis/proposal mutations, or direct backlog edits, and you need to file a fix task or follow-up from a finding and nothing more."
license: MIT
---

# Backlog Create

Narrow companion to **backlog-manager** for roles authorized to file tasks
but not to drive the full lifecycle (transitions, hyp/proposal mutations).
Used by L1 analyst roles like `judge-for-role` whose mutation policy
whitelists exactly `ai-hats task create` + `ai-hats list …`.

For the full backlog lifecycle (state machine, hyp / proposal verbs,
`plan-extract`, work-log cadence) see **backlog-manager**.

## When to Use

**Prefer the sibling backlog-manager for anything past `ai-hats task create`** —
a state transition, a work-log entry, a hyp/proposal verb, `plan-extract`. This
skill is the file-only subset for L1 roles whose mutation policy whitelists
task-create + read-only listing and nothing more (e.g. `judge-for-role`). The
moment you want to *move* the task you just filed, you've left this skill's remit
for backlog-manager's.

## CLI Interface

**Invocation in a harness shell.** Harness-spawned bash does not inherit an
activated venv. Define a resolver once per session (host launcher on PATH, else
the project venv's interpreter — no `bin/ai-hats` console script since HATS-790):

```bash
ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
ah task create "Title" -d "Description" -p medium --tag <tag>
```

If neither works, the project's venv interpreter lives at `./.venv/bin/python` (invoke the package as `./.venv/bin/python -m ai_hats …`).

### `ai-hats task create`

```bash
ai-hats task create "Short title" \
  -d "Description with context, motivation, and acceptance criteria" \
  -p <low|medium|high|critical> \
  --tag <tag> [--tag <tag> ...] \
  [--parent <PARENT-ID>] \
  [--depends-on <DEP-ID>]
```

- ID is auto-generated from the project's `task_prefix` (set in
  `ai-hats.yaml`). Do **not** pass `--id` unless the supervisor explicitly
  asks you to mint a specific number.
- Reference the originating finding's source component(s) in the
  description so the fix author can locate the relevant code without
  re-running the audit.
- Default state on creation is `brainstorm`. **Do not transition** the
  state from this role — that is the fix author's job, governed by
  **backlog-manager**.

### Read-only inspections

```bash
ai-hats task list                # open tasks
ai-hats task show <ID>           # full task card
ai-hats list …                   # library inspections (skills, rules, traits, tokens)
```

## Scope

This skill **only** documents task filing. Anything beyond `ai-hats task
create` (transitions, work-log entries, hyp / proposal verbs) is out of
scope and belongs to **backlog-manager**. If your role's protocol skill
permits a wider set of mutations, compose **backlog-manager** instead.
