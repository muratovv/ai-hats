---
name: judge-auditor-protocol
description: Read-only audit protocol for the judge-auditor role (Phase 1 of the two-phase judge split) — produces a draft report with proposed verdicts and mutations for the HITL judge to consume.
ai_hats:
  requires:
    cli:
      - name: ai-hats-rack
        check: "rack --help"
        hint: "pip install ai-hats-rack"
    mcp: []
license: MIT
---

# Judge Auditor Protocol

Phase 1 of the two-phase judge split (ADR-0007). Runs headless via
`SubAgentRunner` from `ai-hats reflect hypothesis [--headless]`. Produce a
**draft** — proposed verdicts, proposed mutations — for the HITL `judge` session
(Phase 2) to consume.

## When to Use

You were launched as **judge-auditor**. The first user message hands you the
active hypotheses and the open proposal inbox. Work Steps 1–4 in order, then
emit one artifact between `BEGIN_JUDGE_DRAFT` / `END_JUDGE_DRAFT`.

**L0 contract** (`base-auditor` baseline) — the single rule behind every
"record, do not run" below:

- **Mutate nothing.** No state-changing CLI (`rack create`, `rack transition`,
  `rack hyp …`, `ai-hats reflect commit`), no source edits, no `.agent/**`
  writes, no `Write` tool — the pipeline persists your draft, not you.
- **Read freely.** `rack ls`, `rack context`, `git log`, and the Read / Glob
  tools are inspection, not mutation.
- **Never open mid-run dialogue.**
- **Record, do NOT invoke.** Every CLI verb below is Phase 2's to execute.

## Step 1 — Read the previous report

Glob `<ai_hats_dir>/sessions/retros/judge/*-report.md` and Read the
lexicographically last hit — filenames sort by ISO-8601 UTC, so last is newest.
Empty directory (first run ever) → skip this step.

Take two things from it:

- prior verdicts and trends, which inform "keep" vs "extend" this run;
- the timestamp in the filename (`2026-05-18T14-06-17Z-report.md` →
  `2026-05-18T14:06:17Z`) — Step 1.5 needs it as `$PRIOR_TS`.

Ignore `*-draft.md`: those are your own pre-Phase-2 artifacts, not reports.

## Step 1.5 — Inventory deliverables

List what shipped in the window *before* walking HYPs/PROPs, so any later
"regress / pain" claim is weighed against an explicit shipped list rather than
asserted in a vacuum.

Window: `[$PRIOR_TS, now]` — or the last 7 days on a first run ever.

```bash
rack ls --state done --all --json \
  | jq --arg since "$PRIOR_TS" '[.tasks[] | select(.completed_at >= $since)]'
git log --since="$PRIOR_TS" --oneline
```

Keep `--all`: the default caps at 30 id-sorted rows and reports
`"capped": true`, so without it you audit the *oldest* done cards, not the
window. Cards closed before `completed_at` existed carry no such key and drop
out of the filter.

Record under `## Deliverables since prior report`; empty window → `(none)`, and
treat that as signal (see Edge Cases).

## Step 2 — Walk active hypotheses

For each active HYP in the handoff — it carries `success_criterion`,
`observation_window`, `last_rule_revision_date`, and recent verdicts — follow
**review-hypothesis** to pick a verdict + recommendation, then record it under
`## Proposed mutations`.

| Decision shorthand | review-hypothesis verdict | recommendation            | Phase-2 CLI (record, do not run)                            |
| ------------------ | ------------------------- | ------------------------- | ----------------------------------------------------------- |
| `confirmed`        | `confirmed`               | `close_confirmed`         | `rack hyp append-verdict ...` + `rack transition … confirm` |
| `refuted`          | `refuted`                 | `close_refuted`           | `rack hyp append-verdict ...` + `rack transition … refute`  |
| `inconclusive`     | `inconclusive`            | `keep` or `extend_window` | `rack hyp append-verdict ...`                               |
| `keep`             | (verdict per evidence)    | `keep`                    | `rack hyp append-verdict ...`                               |
| `extend`           | (verdict per evidence)    | `extend_window`           | `rack hyp append-verdict ...`                               |
| `stalled`          | —                         | —                         | `rack transition … stall`                                   |

## Step 3 — Walk open proposals

For each open PROP, follow **review-proposal** to decide
`accept | reject | defer | duplicate`, and record it. The bulk
`ai-hats reflect commit` is Phase 2's.

Apply the cost-citation heuristic (symmetric with **review-proposal** Step 3):

- Cited concrete cost in `--rationale` → recommend patience; keep it open
  longer, especially for `rule` / `process`.
- Uncited pain claim open ≥ 1 sweep cycle → `defer` with ≥1 vote, `reject`
  with none.

For every PROP you recommend accepting, also record a follow-up task title and
one-line description — Phase 2 runs `rack create` after the supervisor confirms.

## Step 3.5 — Counter-claims pass

Draft the negative observations destined for `## Notes` (regress / pain /
concern / under-delivery), then run each through the four checks below. Surface
the outcome as a visible artifact, not a silent in-head check.

1. **Count check** — measured or assumed? Assumed → re-count, or drop the number.
2. **Variance vs failure** — a failure mode, or expected variance for the event class?
3. **Shipped vs in-flight** — a regression in a production contract, or dev work that has not shipped?
4. **Survivor bias** — weighting 3 problem tickets against zero acknowledgment of N shipped items?

Record each claim under `## Counter-claims` as `kept (verified: <cite>)`,
`downgraded to observation`, or `dropped (<reason>)`.

## Step 4 — Emit the draft

Emit one block between the markers. The pipeline's `extract_marker` step
captures the body; `save_artifact` writes
`<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`.

Hold the section order — Deliverables before Hypotheses, Counter-claims before
Notes, Proposed mutations last so Phase 2 can scan and execute. Empty sections
are fine: write `(none)`.

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

<one CLI invocation per line — recommendations for Phase 2, not actions
you have taken. Phase 2 runs them after supervisor ack.>

```bash
rack hyp append-verdict HYP-NNN --verdict <verdict> --recommendation <rec> --evidence "<reason>"
rack transition HYP-NNN <confirm|refute|stall>   # HYP status is an FSM edge
ai-hats reflect commit --accept PROP-001 --reject PROP-003 --defer PROP-009
rack create "<title from accepted PROP-NNN>" --description "<from PROP body>"
```

(or `(none)` if no mutations are recommended)
END_JUDGE_DRAFT
````

## Edge Cases

- **Empty inbox + no active HYPs** — still emit a draft, `(none)` in each
  section. Phase 2 opens and the supervisor closes it in one turn.
- **Empty deliverables window** — a multi-day gap with zero `state=done`
  movement is unusual. Log a Counter-claims entry asking whether the window is
  real or miscomputed.
- **`## Counter-claims = (none)` while `## Notes` carries negative claims** —
  you skipped Step 3.5. Go back before emitting.
- **Conflicting PROPs** — recommend accepting one; mark the other `duplicate`
  in `## Proposed mutations`, pointing at the kept PROP.
- **Tempted to run `rack hyp append-verdict` now** — STOP. Record it under
  `## Proposed mutations`; Phase 2 runs it.
