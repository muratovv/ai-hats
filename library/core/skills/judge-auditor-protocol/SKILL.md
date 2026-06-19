---
name: judge-auditor-protocol
description: Read-only audit protocol for the judge-auditor role (Phase 1 of the two-phase judge split) — produces a draft report with proposed verdicts and mutations for the HITL judge to consume.
---
# Judge Auditor Protocol

Read-only audit protocol for the **judge-auditor** role (Phase 1 of the
two-phase judge split — HATS-513 / ADR-0007). Runs headless via
`SubAgentRunner` from `ai-hats reflect hypothesis [--headless]`. Produces
a **draft** report with proposed verdicts and proposed mutations; the
HITL `judge` session (Phase 2) consumes it.

## When to Use

You were launched as **judge-auditor**. The first user message contains
a handoff listing active hypotheses and the open proposal inbox. Apply
this protocol end-to-end and emit a single artifact between
`BEGIN_JUDGE_DRAFT` / `END_JUDGE_DRAFT` markers.

You operate at **L0** (`base-auditor` baseline): no CLI invocations, no
source-file edits, no mid-run dialogue. Every CLI verb listed in the
table below names what Phase 2 (`judge`) should execute — never invoke
it yourself.

## Step 1 — Read the previous judge report

Use the **Glob** tool with pattern
`<ai_hats_dir>/sessions/retros/judge/*-report.md`, then **Read** the
lexicographically last entry (filenames sort by ISO-8601 UTC timestamp,
so the last one is the most recent). If the directory is empty (first
run ever), skip this step.

Note prior verdicts and trends — they inform "keep" vs "extend"
recommendations this run. Extract the prior report's UTC timestamp from
its filename (e.g. `2026-05-18T14-06-17Z-report.md` →
`2026-05-18T14:06:17Z`); Step 1.5 needs it as window lower-bound.

> `*-draft.md` files (your own prior drafts) are NOT prior reports —
> they are pre-Phase-2 artifacts. Read the latest `*-report.md` only.

## Step 1.5 — Inventory deliverables since prior report

Before walking HYPs/PROPs, list what was shipped in the window. This
forces a contrast-first frame: any later "regress / pain" claim is
compared against an explicit shipped list, not asserted in a vacuum.

Window:

- If a prior report exists → `[prior_report_iso_ts, now]` (ts from
  Step 1).
- First-run-ever (no prior) → last 7 days.

Two source-of-truth invocations (run both via **Bash**; you may **read**
the output but NOT mutate state):

```bash
# HATS-790: no bin/ai-hats console script — fallback runs the venv module.
ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
ah task list --state done --updated-since "$PRIOR_TS"
git log --since="$PRIOR_TS" --oneline
```

`task list` is read-only — it does not violate the L0 CLI-ban. The ban
covers state-mutating verbs (`task create`, `task hyp ...`,
`reflect commit`); `list` and `show` are inspection-only and allowed.

Record the result in the draft's `## Deliverables since prior report`
section. Empty window → `(none)`, but mark this as a signal — see Edge
Cases.

## Step 2 — Walk active hypotheses (propose, do NOT persist)

The handoff lists active HYPs with their `success_criterion`,
`observation_window`, `last_rule_revision_date`, and recent verdicts.
For each HYP, follow **review-hypothesis** to choose verdict +
recommendation, then **record the proposed verdict in the draft's
`## Proposed mutations` section**. Do NOT invoke
`ai-hats task hyp append-verdict` / `set-status` — Phase 2 will run
these after supervisor ack.

| Decision shorthand | review-hypothesis verdict | recommendation             | Phase-2 CLI (record, do not run)    |
| ------------------ | ------------------------- | -------------------------- | ----------------------------------- |
| `confirmed`        | `confirmed`               | `close_confirmed`          | `task hyp append-verdict ...` + `set-status confirmed` |
| `refuted`          | `refuted`                 | `close_refuted`            | `task hyp append-verdict ...` + `set-status refuted`   |
| `inconclusive`     | `inconclusive`            | `keep` or `extend_window`  | `task hyp append-verdict ...`       |
| `keep`             | (verdict per evidence)    | `keep`                     | `task hyp append-verdict ...`       |
| `extend`           | (verdict per evidence)    | `extend_window`            | `task hyp append-verdict ...`       |
| `stalled`          | —                         | —                          | `task hyp set-status stalled`       |

## Step 3 — Walk open proposals (propose, do NOT persist)

For each open PROP in the handoff, follow **review-proposal** to decide
one of `accept | reject | defer | duplicate`. **Record** the proposed
decisions in the draft; the **bulk commit** (`reflect commit`) is Phase
2's job.

**Cost-citation heuristic** (symmetric with **review-proposal** Step 3):

- PROP with **cited concrete cost** in `--rationale` → recommend
  patience; keep open longer, especially for `rule` / `process`
  categories.
- PROP with **uncited pain claim** open ≥ 1 sweep cycle → recommend
  `defer` (if it has ≥1 vote) or `reject` (no votes).

For each PROP you recommend accepting, also record the proposed
follow-up task title and one-line description in the draft — Phase 2
runs `ai-hats task create` after supervisor confirms.

## Step 3.5 — Counter-claims pass (devil's advocate)

Before emitting the draft, draft any negative observations destined for
`## Notes` (regress / pain / concern / under-delivery) and run each
through the counter-pass below. The goal is to surface contrast and
verification **as visible artifacts** in the draft, not as silent
in-head checks.

For each drafted negative claim, ask:

1. **Count check.** Is the number measured or assumed? If assumed —
   re-count or drop the number.
2. **Variance vs failure.** Is this a failure mode or expected variance
   for the event class?
3. **Shipped vs in-flight.** Does the claim describe a regression in a
   production contract, or in-flight dev work that hasn't shipped yet?
4. **Survivor bias.** Am I weighting 3 problem tickets against zero
   acknowledgment of N shipped items from `## Deliverables`?

Record each drafted claim with one of: `kept (verified: <cite>)`,
`downgraded to observation`, or `dropped (<reason>)`. Write the result
to the draft's `## Counter-claims` section.

If you end Step 3.5 with `## Counter-claims = (none)` but `## Notes`
still contains negative claims — you skipped the pass. Return to it.

## Step 4 — Emit the draft

Emit the draft as a single block between explicit markers. The pipeline
`extract_marker` step (Phase 1 pipeline YAML) captures the body between
the markers and `save_artifact` writes it to
`<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`. **Do not use the
`Write` tool** — your L0 baseline forbids direct filesystem writes;
the pipeline persists the artifact.

Template (section order is load-bearing — Deliverables before
Hypotheses; Counter-claims before Notes; Proposed mutations last so
Phase 2 can scan-and-execute):

````markdown
BEGIN_JUDGE_DRAFT
# Judge draft — <UTC ts>

## Mode

draft (Phase 1 — judge-auditor)

## Deliverables since prior report

- <HATS-NNN> — <title> (done, <date>)
- git: <sha> <subject>
  (or `(none)`)

## Hypotheses

- HYP-NNN — <verdict>: <one-line rationale>

## Proposals

- PROP-NNN — <decision>: <one-line rationale>

## Counter-claims

- "<draft negative claim>" → kept (verified: <count/cite>) | downgraded to observation | dropped (<reason>)
  (or `(none)`)

## Notes

<free-form observations, trends vs prior report. Claims of regress/pain
must cite concrete cost (tests/iterations/hours/incident); uncited
claims weaken any PROP they source.>

## Proposed mutations

<one CLI invocation per line — these are recommendations for Phase 2,
not actions you have taken. Phase 2 runs them after supervisor ack.>

```bash
ai-hats task hyp append-verdict HYP-NNN --verdict <verdict> --recommendation <rec> --note "<reason>"
ai-hats task hyp set-status HYP-NNN --status <confirmed|refuted|stalled>
ai-hats reflect commit --accept PROP-001 --reject PROP-003 --defer PROP-009
ai-hats task create "<title from accepted PROP-NNN>" --description "<from PROP body>"
```

(or `(none)` if no mutations are recommended)
END_JUDGE_DRAFT
````

Empty sections are fine (use a `(none)` line) — the next judge needs
this artifact to drive Phase 2.

## Edge Cases

- **Empty inbox + no active HYPs** — still emit a draft with `(none)`
  in each section. Phase 2 still opens and supervisor closes in 1 turn.
- **Empty deliverables window** — `## Deliverables since prior report`
  shows `(none)`. Treat this as signal in itself: a multi-day gap with
  zero `state=done` movement is unusual and worth a Counter-claims
  entry interrogating "was the window real or am I miscomputing it?".
- **Counter-claims `(none)` but `## Notes` negative** — anti-pattern;
  Step 3.5 was skipped. Return to it before emitting the draft.
- **Conflicting PROPs** — recommend accepting one, mark the other
  `duplicate` in `## Proposed mutations` with a note pointing to the
  kept PROP.
- **Tempted to run `ai-hats task hyp append-verdict` now** — STOP.
  L0 baseline forbids state mutations. Record the verdict in the draft
  under `## Proposed mutations`; Phase 2 will run it.

## Scope

L0 contract (per `base-auditor`): single artifact between
`BEGIN_JUDGE_DRAFT` / `END_JUDGE_DRAFT` markers; no CLI mutations; no
source-file edits; no `.agent/**` filesystem writes (the pipeline
persists the draft, not you). Read-only `ai-hats list` / `ai-hats show`
and read-only Bash (`git log`, file reads via Read tool) are allowed —
they are inspection, not mutation.
