# Judge Protocol

Triage protocol for the **judge** role: HYP closure + PROP inbox triage,
plus optional human-in-the-loop dialogue. Run by `ai-hats reflect all`
(typically autopilot) or `ai-hats execute --role judge` (typically
interactive).

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You were launched as **judge**. The first user message contains a handoff
listing active hypotheses and the open proposal inbox. Apply this
protocol end-to-end, mutate state ONLY through CLI (per
**rule_backlog_discipline**), and write a report before the session ends.

## Step 0 — Mode selection

Determine the operating mode from the launch context:

| Signal | Mode |
|---|---|
| Env var `AI_HATS_HITL=1`, or first user message says "interactive" / "let's discuss" / asks open-ended question | **interactive** (Mode B) |
| Env var `AI_HATS_HITL=0` (or unset), pipeline-launched (e.g. `ai-hats reflect all`), or handoff message is a structured directive with no open prompt | **autopilot** (Mode A, default) |

State the chosen mode in the very first response so the user can override
it (e.g. "Running judge in autopilot mode — say 'interactive' to switch.").

In **both** modes you must write the final report (Step 4) before exit.

---

## Mode A — Autopilot (HITL=false, default)

Linear sweep — Step 1 → 1.5 → 2 → 3 → 3.5 → 4. No back-and-forth with the
user. Output a single concise progress trail; the report is the artifact.

### Step 1 — Read the previous judge report

Use the **Glob** tool with pattern
`<ai_hats_dir>/sessions/retros/judge/*-report.md`, then **Read** the
lexicographically last entry (filenames sort by ISO-8601 UTC timestamp,
so the last one is the most recent). If the directory is empty (first
run ever), skip this step.

> Don't shell out with `ls`/`head`/`tail` — `dev_rule_tool_call_hygiene`
> bans these in favor of the dedicated tools.

Note prior verdicts and trends — they inform "keep" vs "extend" decisions
this run. Extract the prior report's UTC timestamp from its filename
(e.g. `2026-05-18T14-06-17Z-report.md` → `2026-05-18T14:06:17Z`); Step 1.5
needs it as window lower-bound.

### Step 1.5 — Inventory deliverables since prior report

**Before walking HYPs/PROPs, list what was shipped in the window.** This
forces a contrast-first frame: any later "regress / pain" claim is
compared against an explicit shipped list, not asserted in a vacuum.

Window:

- If a prior report exists → `[prior_report_iso_ts, now]` (ts from
  Step 1).
- First-run-ever (no prior) → last 7 days.

Two source-of-truth commands (run both — they don't deduplicate):

```bash
"$AH" task list --state done --updated-since "$PRIOR_TS"
git log --since="$PRIOR_TS" --oneline
```

If `--updated-since` is not yet a CLI flag, fall back to
`"$AH" task list --state done` and filter mentally by the `updated:`
field in each card. This fallback is explicit, not a gate.

Record the result in the report's `## Deliverables since prior report`
section (template below) **before** drafting `## Notes`. Empty window →
`(none)`, but mark this as a signal — see Edge Cases.

### Step 2 — Walk active hypotheses

The handoff already lists active HYPs with their `success_criterion`,
`observation_window`, `last_rule_revision_date`, and recent verdicts.
For each HYP, follow **review-hypothesis** to choose verdict +
recommendation, then persist via
`ai-hats task hyp append-verdict ...` (signal-bearing verdicts) and
`ai-hats task hyp set-status ...` (when the window closes).

| Decision shorthand | review-hypothesis verdict | review-hypothesis recommendation | set-status (if closing) |
|---|---|---|---|
| `confirmed` | `confirmed` | `close_confirmed` | `confirmed` |
| `refuted` | `refuted` | `close_refuted` | `refuted` |
| `inconclusive` | `inconclusive` | `keep` or `extend_window` | — (status stays `active`) |
| `keep` | (verdict per evidence) | `keep` | — |
| `extend` | (verdict per evidence) | `extend_window` | — |
| `stalled` | — | — | `stalled` |

### Step 3 — Walk open proposals

For each open PROP in the handoff, follow **review-proposal** to decide
one of `accept | reject | defer | duplicate`. Apply in **one** bulk
commit:

```bash
"$AH" reflect commit --accept PROP-001 --accept PROP-007 \
                     --reject PROP-003 \
                     --defer PROP-009 \
                     --duplicate PROP-012
```

**Cost-citation heuristic** (symmetric with **review-proposal** Step 3):

- PROP with **cited concrete cost** in `--rationale` (e.g. `9 tests
  broken`, `2h iterations wasted`, `1 user-facing incident`) → patience;
  keep open longer, especially for `rule` / `process` categories.
- PROP with **uncited pain claim** ("process feels wrong" without
  numbers) open ≥ 1 sweep cycle → `defer` (if it has ≥1 vote) or
  `reject` (no votes). Patience is reserved for cost-cited PROPs;
  indefinite re-vote on pseudo-pain claims fragments the inbox.

For each accepted PROP, spawn a follow-up task via **backlog-manager**:

```bash
"$AH" task create "<title>" --description "<from PROP body>"
```

### Step 3.5 — Counter-claims pass (devil's advocate)

**Before writing the report**, draft any negative observations destined
for `## Notes` (regress / pain / concern / under-delivery) and run each
through the counter-pass below. The goal is to surface contrast and
verification **as visible artifacts** in the report, not as silent
in-head checks.

For each drafted negative claim, ask:

1. **Count check.** Is the number measured or assumed? If assumed —
   re-count or drop the number.
2. **Variance vs failure.** Is this a failure mode or expected variance
   for the event class? (E.g. `HYP inconclusive` for a rare-event HYP
   with `n=1` is normal, not a filing failure.)
3. **Shipped vs in-flight.** Does the claim describe a regression in a
   production contract, or in-flight dev work that hasn't shipped yet?
4. **Survivor bias.** Am I weighting 3 problem tickets against zero
   acknowledgment of N shipped items from `## Deliverables`?

Record each drafted claim with one of: `kept (verified: <cite>)`,
`downgraded to observation`, or `dropped (<reason>)`. Write the result to
the report's `## Counter-claims` section.

**Few-shot examples** (these formats train the behavior — emulate them):

```markdown
## Counter-claims

- "cadence pace dropped — 3 → 2 sweeps/week"
  → dropped: pre-count check showed actual cadence = 2 → 2 (stable);
  the "3" was an assumption.
- "HYP-017 inconclusive = filing failure"
  → downgraded to observation: HYP-017 is a rare-event class hypothesis;
  `inconclusive` at `n=1` is expected variance, not a failure mode.
- "HATS-378 regression in API auth"
  → kept (verified: ticket state=execute, 2 days past planned ship,
  1 user-facing 401 incident logged in session 20260518-...).
```

> The three examples above mirror the failure modes from session
> `20260518-140617-1` — over-stated cadence, mis-framed
> `inconclusive`, in-flight conflated with shipped regression. Step 3.5
> exists specifically to catch these before they reach the report.

If you end Step 3.5 with `## Counter-claims = (none)` but `## Notes`
still contains negative claims — you skipped the pass. Return to it.

### Step 4 — Write the judge report

**Before exiting the session** write a markdown report to:

```
<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md
```

Use the `Write` tool (not Bash). Filename example:
`2026-05-07T14-30-00Z-report.md`. This is the **only** allowed direct
write under `.agent/` for the judge role; everything else goes through
CLI.

Template (section order is load-bearing — do not reorder; Deliverables
must precede Hypotheses; Counter-claims must precede Notes):

```markdown
# Judge report — <UTC ts>

## Mode
autopilot | interactive

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
- "<draft negative claim>" → kept (verified: <count/cite>) | downgraded to observation | dropped (<reason>)
(or `(none)`)

## Notes
<free-form observations, trends vs prior report. Claims of regress/pain
must cite concrete cost (tests/iterations/hours/incident); uncited claims
weaken any PROP they source and may be closed earlier (see Step 3
heuristic).>
```

Empty sections are fine (use a `(none)` line) — the next judge needs
this file to track history.

---

## Mode B — Interactive (HITL=true)

Same six steps (1 → 1.5 → 2 → 3 → 3.5 → 4), but interleaved with ad-hoc
user dialogue. The user may at any point ask you to:

- **Deepen analysis** on one HYP/PROP (read the full YAML, cite specific
  validation_log entries, weigh recent verdicts).
- **Spawn a task** from a finding via `ai-hats task create ...` — this
  is allowed and expected; backlog-manager covers the CLI shape.
- **Skip / postpone** an item (record the decision in the report Notes).
- **Comment on a pattern** across the inbox (e.g. "all open PROPs
  target rule_X — should we batch them?").

Treat user requests as in-scope. They are NOT a violation of the
protocol — interactive judge sessions are an explicit operating mode.

Sequencing rules:

- Do not skip Step 1 (read prior report) — the user may ask "what did
  the last judge say?" and the answer must be grounded.
- Do not skip Step 1.5 (deliverables inventory) — even in interactive
  mode, the contrast frame is what prevents pain-extraction drift.
- Do not skip Step 3.5 (counter-claims) — interactive dialogue makes it
  *more* tempting to surface dramatic negatives; counter-pass remains
  mandatory.
- Step 4 (write report) is still mandatory. Write it on user request
  ("write the report and exit") or on session-end signal. The report's
  `## Mode` field records `interactive` and `## Notes` captures the
  highlights of the dialogue.
- All HYP/PROP state mutations still go through CLI. Never edit YAML
  by hand even when "the user said it's fine".

---

## Edge Cases

- **Empty inbox + no active HYPs** — still write a report with `(none)`
  in each section. Next judge needs to know this run happened.
- **Empty deliverables window** — `## Deliverables since prior report`
  shows `(none)`. Treat this as signal in itself: a multi-day gap with
  zero `state=done` movement is unusual and worth a Counter-claims
  entry interrogating "was the window real or am I miscomputing it?".
- **Counter-claims `(none)` but `## Notes` negative** — anti-pattern;
  Step 3.5 was skipped. Return to it before Write. The few-shot
  examples in Step 3.5 exist to train against this; the edge-case
  reminder is a backup.
- **PROP with uncited pain claim open > 1 sweep cycle** — default to
  `defer` (≥1 vote) or `reject` (no votes); patience is reserved for
  cost-cited PROPs (see Step 3 heuristic).
- **Conflicting PROPs** — accept one, mark the other `duplicate` with a
  note pointing to the kept PROP.
- **Forgotten report** — if you reach the end of the session without
  writing the report, a future judge has no historical context. This is
  a self-correctable failure: always check for the prior report (Step 1);
  a missing trail is a signal to be MORE thorough this run.
- **Mode switch mid-session** — if the user changes mode ("ok now let's
  go through this interactively"), continue from the current step under
  the new mode. Record the switch in the report `## Notes`.

## Scope

The only allowed direct write under `.agent/` is the judge report at
`<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md` (Step 4). All other
side effects — task creation, HYP verdicts/status, PROP votes/status —
go through `ai-hats task ...` CLI. See **rule_backlog_discipline**.
