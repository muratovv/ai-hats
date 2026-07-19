---
name: hatrack
description: Backlog lifecycle on the rack CLI (hatrack) — create / ls / context / transition, with field edits and hyp/proposal staying on `ai-hats task`. Use when a session composes the `hatrack-trait` (rack is the selected backlog manager) for any task lifecycle transition, backlog read, document, or link; it replaces `backlog-manager` for that session.
ai_hats:
  # ADR-0016: this skill drives the ai-hats-rack `rack` CLI for lifecycle and the
  # ai-hats-tracker `ai-hats task` CLI for the coexistence surface (fields, hyp,
  # proposal). Both are DECLARED needs; verified-and-warned at compose time.
  requires:
    cli:
      - name: ai-hats-rack
        check: "rack --help"
        hint: "pip install ai-hats-rack"
      - name: ai-hats-tracker
        check: "ai-hats-tracker --version"
        hint: "pip install ai-hats-tracker"
    mcp: []
license: MIT
---

# Hatrack

Drive the task lifecycle through the **rack** CLI (`ai-hats-rack`), the minimal
backlog kernel — while field edits, hypotheses, and proposals stay on
`ai-hats task`. Same FSM, same `task.yaml` on disk, same per-task lock.

## When to Use

This is the **rack** backlog manager — composed via `hatrack-trait` in place of
`backlog-manager` (never both: they both own the lifecycle and would conflict).
Select it per session with:

```bash
ai-hats config customize <role> --remove-skill backlog-manager --add-trait hatrack-trait --project
# revert: ai-hats config customize <role> --reset
```

If a session composes the classic `backlog-manager` instead, use that skill —
`rack` and `ai-hats task` share the lock and the same `task.yaml`, so a mixed
backlog is safe, but one session should drive lifecycle through one manager.

## State Machine

Unchanged from the tracker: `brainstorm → plan → execute → document → review →
done` (+ `blocked`, `failed`, `cancelled`). `rack transition <ID> <state>` walks
one FSM edge under guard; illegal edges are refused with the legal set.

## Rack CLI — lifecycle, reads, documents, links

Four verbs, each with `--json` (JSON-first):

```bash
rack create "Title" --id PROJ-042 --parent PROJ-014 --depends PROJ-041 --tag dx
rack ls                       # backlog scan (--grep/--tag/--state/--parent)
rack ls PROJ-042 --deep 1     # graph walk from a card
rack context PROJ-042         # THE read package: card + links + document paths
rack transition PROJ-042 execute      # sugar for --state execute
```

`transition` is the one mutating verb: an ordered composite of ops run in argv
order under ONE lock with a single persist (any abort rolls the whole sequence
back). Compose ops on one line:

```bash
rack transition PROJ-042 --state execute --log "impl started" --link related:PROJ-040
rack transition PROJ-042 --attach /tmp/design.md:design.md   # copy a file in as a document
rack transition PROJ-042 --freeze design.md                  # pin {name,digest}
```

Command flags: `--force` (needs `--reason`) relaxes the FSM arrow only;
`--resolution` / `--final-state` stamp terminal metadata; `--ack-frozen` is the
tiered hatch for `--rm` / `--freeze` on a pinned document.

**Documents are fs-as-truth:** the only way to write one is to put a file into
`tasks/<ID>/`; `rack context` live-scans and digests on the fly (no `doc put`).
Read documents by the path `context` prints — never inline the body blind.

## Coexistence — what stays on `ai-hats task`

`rack` has no `update` verb and no hyp/proposal surface. Until generalization
(HATS-1044) these stay on the tracker CLI (same lock, safe to interleave):

| Edit                                                                | Command                           |
| ------------------------------------------------------------------- | --------------------------------- |
| state · work_log · documents · links · resolution · new card        | `rack transition` / `rack create` |
| title · description · priority · reviewer · role · tags · re-parent | `ai-hats task update <ID> --…`    |
| hypotheses · proposals · `close` · `plan-extract` · `sync`          | `ai-hats task …`                  |

`ai-hats task hyp --help` / `ai-hats task proposal --help` carry the field
contracts — they are identical to a classic session; only lifecycle moved.

## Completion

- Lifecycle transitions, reads, documents, and links for the task went through
  `rack`; fields / hyp / proposal went through `ai-hats task`; STATE.md and the
  journal reflect the work.
- **Validation scenario (RED → GREEN).** RED: on a rack-composed session an
  agent asked to advance `PROJ-042` from plan to execute reaches for
  `ai-hats task transition` (classic muscle memory), bypassing the rack
  dispatcher, journal, and worktree effects the pilot exists to exercise. GREEN:
  with hatrack the agent runs `rack transition PROJ-042 execute`, and knows to
  keep `--priority` / `hyp` / `proposal` on `ai-hats task`.

## Anti-Patterns

- Driving lifecycle through `ai-hats task transition` in a rack session — the
  rack dispatcher/journal/worktree path is what's being dogfooded; use `rack`.
- Composing both `backlog-manager` and `hatrack-trait` — two lifecycle owners.
- Reaching for a `rack update` / `rack hyp` verb — they don't exist by design;
  those edits stay on `ai-hats task` (table above).
- Inlining a document's body from `context` output — read it by the printed path.
