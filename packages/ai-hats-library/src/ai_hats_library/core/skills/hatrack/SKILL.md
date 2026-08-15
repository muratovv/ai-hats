---
name: hatrack
description: Backlog lifecycle on the `rack` CLI — create / ls / context / transition / plan-extract, with field edits via `--set` and hypotheses/proposals on the `rack hyp` / `rack proposal` groups. Use for any task lifecycle transition, backlog read, field edit, document, link, hypothesis, or proposal.
ai_hats:
  # ADR-0016: this skill drives the ai-hats-rack `rack` CLI for the whole backlog
  # surface — lifecycle, fields, documents, links, hypotheses, proposals. One
  # DECLARED CLI need; verified-and-warned at compose time.
  requires:
    cli:
      - name: ai-hats-rack
        check: "rack --help"
        hint: "pip install ai-hats-rack"
    mcp: []
license: MIT
---

# Hatrack

Drive the **whole backlog** through the **rack** CLI (`ai-hats-rack`), the
minimal backlog kernel — lifecycle, field edits, documents, links, hypotheses,
and proposals. Same FSM, same `task.yaml` on disk, same per-task lock.

This skill has two sections: **Backlog operations** (the `rack` CLI surface —
how to read and write the backlog) and **Task lifecycle** (when to move a card
and what to do at each edge). Lifecycle is where transition reliability lives —
read it before advancing any card.

## When to Use

This is the **rack** backlog manager, delivered by `trait-agent`. The boundary
against its two siblings: **backlog-create** covers `rack create` alone, for
roles that may file a card but never drive a lifecycle; **rack-advanced** covers
authoring a new backlog and cross-project roots. Everything else — lifecycle,
fields, documents, links, hypotheses, proposals — is here.

## Backlog operations — the `rack` CLI

Five top-level verbs, each with `--json` (JSON-first):

```bash
rack create "Title" --id PROJ-042 --parent PROJ-014 --depends PROJ-041 --tag dx
rack ls                       # backlog scan: --tag/--state/--parent match exactly; --grep last
rack ls --grep id:PROJ-04     # --grep is substring over title+description; 'field:' narrows it
rack ls --backlog hyp         # scan another backlog (repeatable; --all-backlogs for all)
rack ls --backlog hyp --projects all  # sweep across projects (rack root add/ls/rm; --root <path>)
rack ls PROJ-042 --deep 1     # graph walk from a card
rack context PROJ-042         # THE read package: card + links + document paths
rack context PROJ-042 PROJ-043  # batch: ≥2 ids in one process → {"contexts":{id:…}}, skip-and-continue
rack transition PROJ-042 execute      # sugar for --state execute
rack plan-extract PROJ-042    # child cards from plan.md Subtasks/Steps sections
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

**Work Policy.** A card's `work_policy` field is the work policy for the task and
its children; `rack context <child>` delivers every ancestor's `work_policy` up
the parent chain into the child's read (parent-context, HATS-1064; structured
field since HATS-1067). Set it with `rack create --work-policy <text>` or update
an existing card with `rack transition <id> --set work_policy=<text>`. Only that
field travels — put per-stage child policy there, not in the whole card.

### Field edits — `transition --set` / `--append`

There is no `update` verb: field edits ride the one mutating `transition` as
`--set`/`--append` ops, schema-validated on the same lock as a state move (a bad
choice/type is a typed refusal). `--set` replaces a field, `--append` adds to a
list one; a payload is JSON when it parses and plain text otherwise:

```bash
rack transition PROJ-042 --set priority=high --set reviewer=@lead
rack transition PROJ-042 --set title="Sharper title" --set role=implementer
rack transition PROJ-042 --set description="$(cat body.md)"   # verbatim body
rack transition PROJ-042 --append tags=dx                     # add one tag
rack transition PROJ-042 --append 'tags=["dx","rack"]'        # add each entry
rack transition PROJ-042 --set 'tags=["dx"]'                  # replace the list
rack transition PROJ-042 --unlink parent_task:PROJ-010 --link parent_task:PROJ-014   # re-parent
```

A JSON array adds its **entries**, never itself — a list nested inside a list
field is not a card any reader can load (HATS-1299).

State and field ops compose in one call — `rack transition PROJ-042 --state
execute --set role=implementer` is one lock, one persist.

### Fast-close — forced terminal transition

The classic `close` (fast-close from brainstorm/plan) is a forced edge to a
terminal state; `--force` needs a `--reason` (journaled):

```bash
rack transition PROJ-042 --state done --force --reason "shipped on master"
```

`--force` relaxes the FSM **arrow**, never consent — `consent | op --force`.
Nothing you add to the command line switches the supervisor's question off, so
run the recipe bare and let the question happen; it still works, it just asks
once.

### Hypotheses & proposals — the `rack hyp` / `rack proposal` groups

Migrated HYP/PROP catalogs mount as sibling backlogs; `rack` grows a group per
catalog (visible in `rack --help` once mounted). Ids route by prefix, so reads
go through the same `rack context <ID>` / `rack ls <ID>`:

```bash
rack hyp create "Agents batch transitions" --hypothesis "…"
rack hyp append-verdict HYP-009 --verdict refuted --evidence "…" --recommendation keep
rack transition HYP-009 refute        # named edge (quorum-gated); or --state refuted
rack hyp autoclose --dry-run          # close HYPs past the refuted-verdict quorum

rack proposal create "…" --category rule --target rule_x --description "…" --rationale "…"
rack proposal vote PROP-025 --reasoning "…"
rack transition PROP-025 accept       # named edge; or --state accepted
```

`rack hyp --help` / `rack proposal --help` carry each group's verbs and field
contracts. Cross-backlog links (`related_hypotheses`, `source_task`) mirror
automatically; the derived read-views refresh on every write — no manual `sync`.

## Task lifecycle — edges, policy, cadence

### Legal transitions (this backlog's FSM)

`rack transition <ID> <state>` walks ONE edge under guard; an illegal edge is
refused with the legal set. The complete edge set for this backlog — rendered
live from the FSM, so it is always authoritative:

{{backlog_fsm_edges}}

`done` and `cancelled` are terminal (`done` = completed; `cancelled` =
administratively closed). A parenthesised name is that edge's own name, typeable
in place of the target state (`rack transition <ID> reclaim`); every named edge
above is legal but rare — do not walk one by reflex.

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

**Before `plan → execute`, re-validate the premise:** scan the card for a
retracted/superseded driver since the plan was authored; if the justification is
stale, bounce to `brainstorm` instead of building on a dead premise
(`rule_backlog_discipline §6`).

### Completion

- The whole backlog — lifecycle, fields, documents, links, hyp, proposal —
  went through `rack`; the card's `work_log` reflects the work (log as you go,
  not only at the end).
- On finishing execute work, advance through `document` to **`review`** and
  stop — the reviewer approves `review → done` first (see the policy table).
- **Validation scenario (RED → GREEN).** RED: asked to advance `PROJ-042` from
  plan to execute, the agent reaches for `ai-hats task transition` (classic
  muscle memory), bypassing the rack dispatcher, journal, and worktree effects.
  GREEN: it runs `rack transition PROJ-042 execute`, and keeps field edits on
  `--set` and `hyp` / `proposal` on the `rack hyp` / `rack proposal` groups.

## Anti-Patterns

- Leaving finished work in `execute`, or advancing to `review` then self-jumping
  to `done` — advance per phase, enter `review` to signal readiness, and **wait**
  (HATS-1047).
- Driving lifecycle through `ai-hats task transition` in a rack session — the
  rack dispatcher/journal/worktree path is what's being dogfooded; use `rack`.
- Reaching for a `rack update` verb — there is none; field edits are
  `rack transition <ID> --set <field>=<value>` (replace) / `--append` (add to a list).
- Reaching back to the classic tracker CLI for fields / hyp / proposal — the
  whole surface is on `rack` now (`--set`, `rack hyp`, `rack proposal`).
- Inlining a document's body from `context` output — read it by the printed path.
- `--force`-ing an edge the FSM refuses (e.g. `document → done` skipping `review`,
  or `review → plan`) instead of taking the legal path or escalating.
