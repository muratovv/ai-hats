---
name: ticket-id-gate
description: Pre-commit gate refusing a tracker id in staged library prose. Use when composing a library-authoring role, or when diagnosing why the ticket-ids hook blocked a commit.
ai_hats:
  # hook-carrier skill. The assembler installs the script below into
  # `.githooks/pre-commit.d/` at composition time. Over STAGED library
  # `.md`/`.yaml`/`.yml` (excluding `hooks/` and `git_hooks/`) it refuses a
  # `<PREFIX>-<digits>` tracker id, where the prefix is learned from the
  # project's own card ids. Per-commit override AI_HATS_TICKET_IDS_ACK=1.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-ticket-ids.sh
license: MIT
---

# Ticket ID Gate

Pure-infrastructure hook-carrier skill. It contributes one git pre-commit hook
and carries no agent-side decision logic — the value is delivered entirely
through composition.

## What it gates

A tracker id in prose the library **ships**. The library installs into other
people's projects, where `PROJ-1430` is a link into a tracker the reader does
not have, and it costs context on every load. `git log -S '<phrase>'` finds the
commit that introduced a line for anyone holding the repository; a tracker id
travels nowhere.

Scope is **staged files only**, so the gate never retro-blocks a backlog it did
not create.

## The prefix is learned, never written down here

This script is itself shipped library content. Hardcoding one project's prefix
in it would reproduce the exact leak it refuses. So it reads the prefix from the
project's own card ids (shortest `<prefix>-<digit>` run, so `MY-PROJ-42` yields
`MY-PROJ`, matching how rack routes). `AI_HATS_TICKET_PREFIX` overrides it. No
tracker and no override is a loud no-op: a project with no ids has none to leak.

## What it does not judge

| not judged                   | why                                                                      |
| ---------------------------- | ------------------------------------------------------------------------ |
| `<PREFIX>-NNN` placeholders  | digits are required, so a CLI template teaching the shape is not a match |
| `hooks/`, `git_hooks/`, code | a bare id there is often the whole comment — removing it is a rewrite    |
| anything unstaged            | changed-files scope; the gate fires only on what this commit touches     |
| another project's prefix     | only the ids this project mints are judged                               |

## When an id belongs

When prose names a string a machine **prints**, so the reader can recognise what
appears on screen. Prefer quoting the message itself — a reader can grep that,
and cannot grep a number. When the id really is the identifier, say so on the
same line:

```markdown
a wall of PROJ-1242 tripwire errors <!-- ticket-ids: allow the guard prints it -->
```

The reason is not decoration: the hook prints it back, and a marker without one
is reported as `(no reason given)`.

## Relationship to the `ticket-ids` CI stage

Same invariant, two ranges. The hook is per-commit and staged-scope, so it
catches an id at the keystroke. `scripts/check_no_ticket_ids.py` sweeps the whole
library and runs inside `merge-gate` and `push-gate` — that is the one nothing
lands on the base branch without. The hook shortens the loop; the stage is the
boundary.

## Override

After confirming the id belongs and the marker is wrong for the case:

```bash
AI_HATS_TICKET_IDS_ACK=1 git commit ...
```

The bypass is journalled, not merely printed to stderr.
