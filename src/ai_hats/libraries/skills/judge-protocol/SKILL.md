# Judge Protocol

Triage protocol for the **judge** role: HYP closure + PROP inbox triage.
Run by `ai-hats reflect all` (interactive) or `ai-hats execute --role judge`.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You were launched as **judge**. The first user message contains a handoff
listing active hypotheses and the open proposal inbox. Apply this
protocol end-to-end, mutate state ONLY through CLI, and write a report
before the session ends.

## Procedure

### Step 1 — Read the previous judge report

```bash
ls -1t .agent/retrospectives/judge/*-report.md 2>/dev/null | head -1
```

If a file exists — read it. Note prior verdicts and trends. If the dir
is empty (first run ever), skip this step.

### Step 2 — Walk active hypotheses

The handoff already lists active HYPs with their `success_criterion`,
`observation_window`, `last_rule_revision_date`, and recent verdicts.
For each HYP decide one outcome:

- `confirmed` — success criterion met within window
- `refuted` — criterion violated; pattern keeps occurring
- `inconclusive` — window not yet elapsed or signal noisy
- `keep` — leave open as-is, more data needed
- `extend` — extend observation window

Persist with `ai-hats task hyp append-verdict ...` (per
**hypothesis-validation**). Don't edit YAML files by hand.

### Step 3 — Walk open proposals

For each open PROP in the handoff decide one of:

- `accept` — apply; spawn task via `ai-hats task create ...`
- `reject` — won't do; keep PROP closed for posterity
- `defer` — keep open, revisit next cycle
- `duplicate` — dedupe against an existing PROP

Apply in **one** bulk call:

```bash
"$AH" reflect commit --accept PROP-001 --accept PROP-007 \
                     --reject PROP-003 \
                     --defer PROP-009 \
                     --duplicate PROP-012
```

For each accepted PROP, spawn a task:

```bash
"$AH" task create "<title>" --description "<from PROP body>"
```

### Step 4 — Write the judge report

**Before exiting the session** write a markdown report to:

```
.agent/retrospectives/judge/<UTC-ISO-ts>-report.md
```

Use the `Write` tool (not Bash). Filename example:
`2026-05-07T14-30-00Z-report.md`. Template:

```markdown
# Judge report — <UTC ts>

## Hypotheses
- HYP-NNN — <verdict>: <one-line rationale>

## Proposals
- PROP-NNN — <decision>: <one-line rationale>

## New tasks
- HATS-NNN — <title> (created from PROP-NNN)

## Notes
<free-form observations, trends vs prior report, anything to flag>
```

Empty sections are fine (use a `(none)` line) — the next judge needs
this file to track history.

## Edge Cases

- **Empty inbox + no active HYPs** — still write a report with `(none)`
  in each section. Next judge needs to know this run happened.
- **Conflicting PROPs** — accept one, mark the other `duplicate` with a
  note pointing to the kept PROP.
- **Forgotten report** — if you reach the end of the session without
  writing the report, a future judge has no historical context. This is
  a self-correctable failure: when launched, always check for the prior
  report (Step 1); a missing trail is a signal to be MORE thorough this
  run.

## Scope

You DO NOT edit `.agent/backlog/tasks/*`, `.agent/hypotheses/*`, or
`.agent/backlog/proposals/*` files directly. All side effects go
through `ai-hats task hyp …`, `ai-hats reflect commit …`, and
`ai-hats task create …`.
