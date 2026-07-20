---
name: backlog-create
description: "Narrow shim for filing tasks via `rack create` from roles that mutate the backlog only at L1 (task-create only). Use when your role's mutation policy permits filing new tasks but forbids state transitions, hypothesis/proposal mutations, or direct backlog edits, and you need to file a fix task or follow-up from a finding and nothing more."
license: MIT
---

# Backlog Create

Narrow companion to **hatrack** for roles authorized to file tasks
but not to drive the full lifecycle (transitions, hyp/proposal mutations).
Used by L1 analyst roles like `judge-for-role` whose mutation policy
whitelists exactly `rack create` + `ai-hats list …`.

For the full backlog lifecycle (state machine, hyp / proposal verbs,
`plan-extract`, work-log cadence) see **hatrack**.

## When to Use

**Prefer the sibling hatrack for anything past `rack create`** —
a state transition, a work-log entry, a hyp/proposal verb, `plan-extract`. This
skill is the file-only subset for L1 roles whose mutation policy whitelists
task-create + read-only listing and nothing more (e.g. `judge-for-role`). The
moment you want to *move* the task you just filed, you've left this skill's remit
for hatrack's.

## CLI Interface

**Invocation in a harness shell.** Harness-spawned bash does not inherit an
activated venv. Resolve the `rack` console script once per session (host launcher
on PATH, else the project venv's interpreter):

```bash
rk() { if command -v rack >/dev/null 2>&1; then rack "$@"; else ./.venv/bin/python -m ai_hats_rack.cli "$@"; fi; }
rk create "Title" --description "Description" --priority medium --tag <tag>
```

### `rack create`

```bash
rack create "Short title" \
  --description "Description with context, motivation, and acceptance criteria" \
  --priority <low|medium|high|critical> \
  --tag <tag> [--tag <tag> ...] \
  [--parent <PARENT-ID>] \
  [--depends <DEP-ID>]
```

- ID is auto-generated from the project's `task_prefix` (set in
  `ai-hats.yaml`). Do **not** pass `--id` unless the supervisor explicitly
  asks you to mint a specific number.
- Reference the originating finding's source component(s) in the
  description so the fix author can locate the relevant code without
  re-running the audit.
- Default state on creation is `brainstorm`. **Do not transition** the
  state from this role — that is the fix author's job, governed by
  **hatrack**.

### Read-only inspections

```bash
rack ls                          # open tasks (--grep/--tag/--state/--parent)
rack context <ID>                # full task card + links + document paths
ai-hats list …                   # library inspections (skills, rules, traits, tokens)
```

## Scope

This skill **only** documents task filing. Anything beyond `rack create`
(transitions, work-log entries, hyp / proposal verbs) is out of
scope and belongs to **hatrack**. If your role's protocol skill
permits a wider set of mutations, compose **hatrack-trait** instead.
