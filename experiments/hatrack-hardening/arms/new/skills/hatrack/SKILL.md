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

This skill has two sections: **Backlog operations** (the `rack` CLI surface —
how to read and write the backlog) and **Task lifecycle** (when to move a card
and what to do at each edge). Lifecycle is where transition reliability lives —
read it before advancing any card.

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

## Backlog operations — the `rack` CLI

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

### Coexistence — what stays on `ai-hats task`

`rack` has no `update` verb and no hyp/proposal surface. Until generalization
(HATS-1044) these stay on the tracker CLI (same lock, safe to interleave):

| Edit                                                                | Command                           |
| ------------------------------------------------------------------- | --------------------------------- |
| state · work_log · documents · links · resolution · new card        | `rack transition` / `rack create` |
| title · description · priority · reviewer · role · tags · re-parent | `ai-hats task update <ID> --…`    |
| hypotheses · proposals · `close` · `plan-extract` · `sync`          | `ai-hats task …`                  |

`ai-hats task hyp --help` / `ai-hats task proposal --help` carry the field
contracts — they are identical to a classic session; only lifecycle moved.

## Task lifecycle — edges, policy, cadence

### Legal transitions (this backlog's FSM)

`rack transition <ID> <state>` walks ONE edge under guard; an illegal edge is
refused with the legal set. The complete edge set for this backlog — rendered
live from the FSM, so it is always authoritative:

{{backlog_fsm_edges}}

`done` and `cancelled` are terminal (`done` = completed; `cancelled` =
administratively closed). The self-loop `execute → execute` is a reclaim
(HATS-955, ownership-gated), and `done → execute` reopens for forgotten scope
(HATS-328) — both are legal but rare; do not walk them by reflex.

### Per-edge policy — trigger → action

For each edge you drive: the trigger that fires it and what to do. The
transition is a live signal to the supervisor, so move the card **as each phase
completes** — never batch every transition at the end. Finished work left in
`execute` reads as "still working".

| Edge                                         | Trigger                                            | Action (and gate skill)                                                                                                                                                                |
| -------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `brainstorm → plan`                          | requirements clear enough to plan                  | **plan-gate** fills every required `plan.md` section, then transition                                                                                                                  |
| `plan → execute`                             | plan approved                                      | re-validate the premise first (below); rack auto-creates the `task/<id>` worktree — `cd` into it (**worktree-isolation**)                                                              |
| `execute → document`                         | code + tests done **and committed**                | `git status` clean; log a one-line summary; then advance — do not stall in `execute`                                                                                                   |
| `document → review`                          | work finished — this **signals "awaiting review"** | attach `summary.md` (**task-summary**), then advance to `review` and **WAIT** — do **not** self-advance to `done`                                                                      |
| `review → done`                              | the card `reviewer` approved with no rework asked  | the reviewer drives this edge, not you; it **auto-merges the worktree** (subscriber, HATS-1019) — no `wt merge`                                                                        |
| `review → execute` (rework)                  | review returned **WITH comments** to address       | transition `execute` (fires **no** merge — the worktree survives for the rework, HATS-1052), address the comments, then `document` → `review` again; this is the loop, not a `--force` |
| `brainstorm/plan/execute/document → blocked` | an external dependency stalls progress             | log the blocker (**request-supervisor**), transition `blocked`; return to the prior state when unblocked — NB: `review` has no `blocked` edge (see the table above)                    |
| `execute/review → failed`                    | the task cannot be completed                       | **self-retrospective** (mandatory — why it failed), then `failed → brainstorm` to re-plan                                                                                              |
| any non-terminal `→ cancelled`               | won't-fix / duplicate / obsolete                   | requires `--resolution "<why>"` (the audit trail); the worktree is discarded — work is not preserved                                                                                   |

(The self-loop `execute → execute` and the `done → execute` reopen are covered
by the note above — legal but rare; not agent-driven by reflex.)

**Before `plan → execute`, re-validate the premise:** scan the card for a
retracted/superseded driver since the plan was authored; if the justification is
stale, bounce to `brainstorm` instead of building on a dead premise
(`rule_backlog_discipline §6`).

### Completion

- Lifecycle transitions, reads, documents, and links went through `rack`;
  fields / hyp / proposal went through `ai-hats task`; the card's `work_log`
  reflects the work (log as you go, not only at the end).
- On finishing execute work, advance through `document` to **`review`** and
  stop — the reviewer approves `review → done` first (see the policy table).
- **Validation scenario (RED → GREEN).** RED: asked to advance `PROJ-042` from
  plan to execute, the agent reaches for `ai-hats task transition` (classic
  muscle memory), bypassing the rack dispatcher, journal, and worktree effects.
  GREEN: it runs `rack transition PROJ-042 execute`, and keeps `--priority` /
  `hyp` / `proposal` on `ai-hats task`.

## Anti-Patterns

- Leaving finished work in `execute`, or advancing to `review` then self-jumping
  to `done` — advance per phase, enter `review` to signal readiness, and **wait**
  (HATS-1047).
- Driving lifecycle through `ai-hats task transition` in a rack session — the
  rack dispatcher/journal/worktree path is what's being dogfooded; use `rack`.
- Composing both `backlog-manager` and `hatrack-trait` — two lifecycle owners.
- Reaching for a `rack update` / `rack hyp` verb — they don't exist by design;
  those edits stay on `ai-hats task` (Coexistence table).
- Inlining a document's body from `context` output — read it by the printed path.
- `--force`-ing an edge the FSM refuses (e.g. `document → done` skipping `review`,
  or `review → plan`) instead of taking the legal path or escalating.
