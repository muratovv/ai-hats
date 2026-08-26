---
name: judge-protocol
description: HITL protocol for the judge role (Phase 2 of the two-phase judge split) — discuss the Phase 1 draft with the supervisor, ack and execute proposed mutations via CLI, write the final report.
ai_hats:
  requires:
    cli:
      - name: ai-hats-rack
        check: "rack --help"
        hint: "pip install ai-hats-rack"
    mcp: []
license: MIT
---

# Judge Protocol

HITL protocol for the **judge** role: discuss the Phase 1 draft with
the supervisor, ack proposed mutations, execute them via CLI, write
the final report. Phase 2 of the two-phase judge split
(ADR-0007).

Runs via `ai-hats reflect hypothesis` (after Phase 1 `judge-auditor`
produces a draft) or `ai-hats execute --role judge` (standalone — see
Edge Cases).

> **Harness shell prelude.** Before any `ai-hats` invocation:
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }  # no bin/ai-hats console script
> ```

## When to Use

You were launched as **judge**. The first user message contains the
Phase 1 draft body inline (or, in standalone mode, a free-form
supervisor prompt). Apply this protocol end-to-end, mutate state ONLY
through CLI (per **rule_backlog_discipline**), and write a report
before the session ends.

You operate at **L1** (`base-judge` baseline): HITL dialogue with the
supervisor, ack'd CLI mutations from a whitelist (see §CLI whitelist),
no source-file edits without L2 activation, single report artifact at
exit.

## Step 0 — Inventory the PROP inbox (unconditional)

Before Step 1, in every shape, whatever the kickoff scoped you to:

```bash
rack ls --backlog proposal --state open --all
```

`--all` is load-bearing — the default caps at 30 id-sorted rows.

**Why unconditional.** The inbox is the other half of the reflex loop, and no
launch shape guarantees you see it: Shapes A/B hand you a Phase 1 draft whose
`## Proposals` section may be thin, and Shape C hands you nothing at all. In
one measured session the kickoff scoped the work to the HYP contour, no one listed the
inbox, and 136 open proposals went unread — among them PROP-107 and PROP-118,
findings the same epic then rediscovered from scratch at full cost. A scope
that omits the inbox is a scope you widen, not an instruction to skip this.

Carry three numbers into Step 3 and into the report: total open, how many are
auto-filed noise (**review-proposal** Step 3b), and the leaders by votes **and**
by age. Both axes matter: vote counts on cards older than the fix are
systematically depressed — `session-reviewer` was emitting `n/a` for months —
so ranking on votes alone buries precisely the pre-fix cards.

Under `reflect hypothesis` the Phase 2 preamble already carries this digest.
Run the command anyway: it costs one call, and it is the only thing that would
catch a digest disagreeing with the catalog.

## Step 1 — Read the first user message

The first user message is one of three shapes. Detect which and adapt:

### Shape A — Phase 1 draft from `judge-auditor` (default for `reflect hypothesis`)

The body contains a `## Proposed mutations` section. This is your
starting analysis from the headless auditor pass:

- Read the draft cover-to-cover before mutating anything.
- The `## Proposed mutations` section is a CLI checklist. Each line is
  a verbatim invocation Phase 1 recommends; you decide which ones to
  execute after supervisor dialogue.
- If you disagree with a proposed verdict (e.g. draft says
  `keep` but recent evidence indicates `extend_window`), say so and
  ask the supervisor.

### Shape B — Active-HYP / open-PROP inventory (legacy `reflect all`)

The body has `## Active hypotheses` / `## Open proposals` sections but
NO `## Proposed mutations` section. This is the legacy single-phase
invocation that has not yet migrated to `reflect hypothesis`. Run the
audit yourself before dialoguing — walk the inventory, draft verdicts
mentally, then proceed to Step 2 with the supervisor. There is no
auditor draft to validate; you are doing both phases in one session.

> The legacy `reflect all` pipeline is deprecated but kept
> for one bake cycle. Don't refuse — supervisor may invoke it
> intentionally during transition. Apply the same Steps 2 → 3 → 3.5 →
> 4 flow; just skip the "validate auditor proposals" framing.

### Shape C — Free-form supervisor prompt (`ai-hats execute --role judge`)

The body is an ad-hoc question, not a structured handoff. Glob
`<ai_hats_dir>/sessions/retros/judge/*-draft.md`, read the
lexicographically latest if present, and proceed — or ask the
supervisor for context up front.

## Step 2 — Walk hypotheses with supervisor

For each HYP in the draft's `## Hypotheses` section, surface the
proposed verdict + recommendation to the supervisor, dialogue if
needed, and on ack execute via CLI:

```bash
rack hyp append-verdict HYP-NNN \
  --verdict <confirmed|refuted|inconclusive> \
  --recommendation <close_confirmed|close_refuted|keep|extend_window> \
  --evidence "<reason>"
```

When a HYP's window closes (`close_confirmed` / `close_refuted` /
`stalled`):

```bash
rack transition HYP-NNN <confirmed|refuted|stalled>
```

Follow **review-hypothesis** for verdict-choice rules. The draft
already applied them; your job is to validate and adjust based on
dialogue.

## Step 3 — Walk proposals with supervisor

Walk the inbox from Step 0 — not only the draft's `## Proposals`
section, which is a Phase 1 selection and may be thin. For each PROP
surface the proposed decision (`accept | reject | defer | duplicate`)
to the supervisor. On ack for the full batch, run **one** bulk commit:

```bash
ah reflect commit \
  --accept PROP-001 --accept PROP-007 \
  --reject PROP-003 \
  --defer PROP-009 \
  --duplicate PROP-012
```

For each accepted PROP, spawn the follow-up task as recommended in the
draft's `## Proposed mutations` section:

```bash
rack create "<title>" --description "<from PROP body>"
rack transition PROP-NNN --link related_tasks:HATS-NNN
```

Record that second command for every PROP whose outcome is a task —
spawned by an `accept`, or already covered by an existing card on a
`reject`. The edge is the machine-readable half of the triage; a
mapping that lives only in this report or a work-log line cannot be
resolved by anything downstream.

Follow **review-proposal** for decision rules + the cost-citation
heuristic (cost-cited PROPs get patience; uncited pain claims default
to `defer` or `reject` after ≥1 sweep cycle), and its Step 3b for the
auto-filed harness/reviewer cards — those are a single batch decision
under a shared criterion, never N individual judgements.

## Step 3.5 — Counter-claims pass (devil's advocate)

The draft already ran a counter-pass; its `## Counter-claims` section
records each negative observation that was kept / downgraded /
dropped. Use it as input, but **re-run the pass yourself** on any
NEW negative observations that emerge from supervisor dialogue
(things the auditor didn't see).

For each new draft negative claim, ask:

1. **Count check.** Is the number measured or assumed?
2. **Variance vs failure.** Is this a failure mode or expected
   variance for the event class?
3. **Shipped vs in-flight.** Production contract regression or
   in-flight dev work?
4. **Survivor bias.** Are you weighting N problem tickets against zero
   acknowledgment of M shipped items?

Append the new claims to the report's `## Counter-claims` section
alongside the auditor's entries (keep the auditor's entries as-is so
the audit trail is visible).

## Step 4 — Write the final judge report

Before exiting the session write a markdown report to:

```
<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md
```

Use the `Write` tool (not Bash). Filename example:
`2026-05-07T14-30-00Z-report.md`. This is the L0 carve-out for this
role (per `base-judge` §Contract); everything else goes through CLI.

Wrap the body between `BEGIN_JUDGE` / `END_JUDGE` markers so the
pipeline's `extract_marker` step can capture it for audit (the
pipeline's `save_artifact` then writes the same content to the
canonical path — your Write tool call is the L0-audit-trail copy
under the role's declared write path).

Template (section order is load-bearing — Deliverables must precede
Hypotheses; Counter-claims must precede Notes):

```markdown
BEGIN_JUDGE

# Judge report — <UTC ts>

## Mode

Phase 2 (HITL) — from draft <UTC ts of Phase 1 draft>

## Deliverables since prior report

- <HATS-NNN> — <title> (done, <date>)
- git: <sha> <subject>
  (or `(none)`)

## Hypotheses

- HYP-NNN — <verdict>: <one-line rationale>

## Proposals

inbox: <N> open · <M> triaged this pass · <K> swept as auto-noise

- PROP-NNN — <decision>: <one-line rationale> (→ HATS-NNN)

## New tasks

- HATS-NNN — <title> (created from PROP-NNN)

## Counter-claims

- "<claim>" → kept (verified: <cite>) | downgraded to observation | dropped (<reason>)
  (or `(none)`)

## Notes

<dialogue highlights, mid-session changes of mind, items the supervisor
deferred. Claims of regress/pain must cite concrete cost.>
END_JUDGE
```

Empty sections are fine (use a `(none)` line) — the next sweep needs
this file to track history.

## CLI whitelist (L1 per base-judge)

Allowed:

- `rack hyp append-verdict ...`
- `rack transition <HYP-ID> confirm|refute|stall|revive ...` (HYP status is an FSM edge)
- `rack transition <PROP-ID> --link related_tasks:<TASK-ID>` (Step 3 triage outcome)
- `ai-hats reflect commit ...`
- `rack create ...`
- `rack ls ...` / `rack context ...` / `ai-hats list ...` (inspection)

Forbidden without L2 activation:

- Direct edits of `<ai_hats_dir>/tracker/backlog/**` or
  `<ai_hats_dir>/tracker/hypotheses/**` (use CLI, see
  `rule_backlog_discipline`).
- Edits of role / skill / rule / trait source files.
- Any verb not on the whitelist — escalate via **request-supervisor**.

## Edge Cases

- **Standalone launch (no Phase 1 draft)** — `ai-hats execute --role
  judge` is supported. Glob the latest `*-draft.md`, or ask the
  supervisor for ad-hoc context. The protocol from Step 1 onwards is
  unchanged.
- **Draft conflicts with recent evidence** — the supervisor or your
  own re-read may surface evidence the auditor missed. Override the
  draft's verdict, note the override in `## Notes`, and execute the
  corrected CLI invocation. The draft is a proposal, not a binding
  decision.
- **Empty draft (all `(none)` sections)** — write a report with
  `(none)` sections and exit. The empty pass is itself a signal.
- **Mid-session supervisor request out of CLI whitelist** — escalate
  via **request-supervisor**; do not silently exceed scope.
- **Counter-claims `(none)` in your report but `## Notes` negative** —
  anti-pattern; Step 3.5 was skipped on the new observations from
  dialogue. Return to it before Write.
- **`## Proposals` with no `inbox:` line** — anti-pattern; Step 0 was
  skipped, so the section reports what the draft happened to mention
  rather than what the inbox holds. Run Step 0 and rewrite the section
  before Write. `0 open` is a fine value; a missing line is not.
- **Kickoff scopes you away from the inbox** — Step 0 still runs. A
  narrow kickoff is what produced the miss; report the count
  even when the pass itself stays inside the scoped contour.

## Scope

The only allowed direct write under `.agent/` is the judge report at
`<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md` (Step 4).
All other side effects — task creation, HYP verdicts/status, PROP
votes/status — go through the `rack` CLI (`rack create` / `rack hyp` /
`rack proposal`). See **rule_backlog_discipline**.
