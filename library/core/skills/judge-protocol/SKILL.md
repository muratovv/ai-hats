# Judge Protocol

HITL protocol for the **judge** role: discuss the Phase 1 draft with
the supervisor, ack proposed mutations, execute them via CLI, write
the final report. Phase 2 of the two-phase judge split
(HATS-513 / ADR-0007).

Runs via `ai-hats reflect hypothesis` (after Phase 1 `judge-auditor`
produces a draft) or `ai-hats execute --role judge` (standalone — see
Edge Cases).

> **Harness shell prelude.** Before any `ai-hats` invocation:
>
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
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

## Step 1 — Read the draft from handoff

The first user message contains the Phase 1 `judge-auditor` draft. Its
sections — `## Deliverables since prior report`, `## Hypotheses`,
`## Proposals`, `## Counter-claims`, `## Notes`, `## Proposed
mutations` — are your starting analysis. Treat them as **proposals**,
not facts:

- Read the draft cover-to-cover before mutating anything.
- The `## Proposed mutations` section is a CLI checklist. Each line is
  a verbatim invocation Phase 1 recommends; you decide which ones to
  execute after supervisor dialogue.
- If you disagree with a proposed verdict (e.g. draft says
  `keep` but recent evidence indicates `extend_window`), say so and
  ask the supervisor.

If launched standalone (no draft in handoff), use the **Glob** tool
on `<ai_hats_dir>/sessions/retros/judge/*-draft.md`, read the
lexicographically latest, and proceed from there — or ask the
supervisor for ad-hoc context.

## Step 2 — Walk hypotheses with supervisor

For each HYP in the draft's `## Hypotheses` section, surface the
proposed verdict + recommendation to the supervisor, dialogue if
needed, and on ack execute via CLI:

```bash
"$AH" task hyp append-verdict HYP-NNN \
  --verdict <confirmed|refuted|inconclusive> \
  --recommendation <close_confirmed|close_refuted|keep|extend_window> \
  --note "<reason>"
```

When a HYP's window closes (`close_confirmed` / `close_refuted` /
`stalled`):

```bash
"$AH" task hyp set-status HYP-NNN --status <confirmed|refuted|stalled>
```

Follow **review-hypothesis** for verdict-choice rules. The draft
already applied them; your job is to validate and adjust based on
dialogue.

## Step 3 — Walk proposals with supervisor

For each PROP in the draft's `## Proposals` section, surface the
proposed decision (`accept | reject | defer | duplicate`) to the
supervisor. On ack for the full batch, run **one** bulk commit:

```bash
"$AH" reflect commit \
  --accept PROP-001 --accept PROP-007 \
  --reject PROP-003 \
  --defer PROP-009 \
  --duplicate PROP-012
```

For each accepted PROP, spawn the follow-up task as recommended in the
draft's `## Proposed mutations` section:

```bash
"$AH" task create "<title>" --description "<from PROP body>"
```

Follow **review-proposal** for decision rules + the cost-citation
heuristic (cost-cited PROPs get patience; uncited pain claims default
to `defer` or `reject` after ≥1 sweep cycle).

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

````markdown
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

- PROP-NNN — <decision>: <one-line rationale>

## New tasks

- HATS-NNN — <title> (created from PROP-NNN)

## Counter-claims

- "<claim>" → kept (verified: <cite>) | downgraded to observation | dropped (<reason>)
  (or `(none)`)

## Notes

<dialogue highlights, mid-session changes of mind, items the supervisor
deferred. Claims of regress/pain must cite concrete cost.>
END_JUDGE
````

Empty sections are fine (use a `(none)` line) — the next sweep needs
this file to track history.

## CLI whitelist (L1 per base-judge)

Allowed:

- `ai-hats task hyp append-verdict ...`
- `ai-hats task hyp set-status ...`
- `ai-hats reflect commit ...`
- `ai-hats task create ...`
- `ai-hats task list ...` / `ai-hats list ...` / `ai-hats show ...` (inspection)

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

## Scope

The only allowed direct write under `.agent/` is the judge report at
`<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md` (Step 4).
All other side effects — task creation, HYP verdicts/status, PROP
votes/status — go through `ai-hats task ...` CLI. See
**rule_backlog_discipline**.
