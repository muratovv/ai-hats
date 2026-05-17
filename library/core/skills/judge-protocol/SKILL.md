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

Linear sweep — Step 1 → 2 → 3 → 4. No back-and-forth with the user.
Output a single concise progress trail; the report is the artifact.

### Step 1 — Read the previous judge report

Use the **Glob** tool with pattern
`<ai_hats_dir>/sessions/retros/judge/*-report.md`, then **Read** the
lexicographically last entry (filenames sort by ISO-8601 UTC timestamp,
so the last one is the most recent). If the directory is empty (first
run ever), skip this step.

> Don't shell out with `ls`/`head`/`tail` — `dev_rule_tool_call_hygiene`
> bans these in favor of the dedicated tools.

Note prior verdicts and trends — they inform "keep" vs "extend" decisions
this run.

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

For each accepted PROP, spawn a follow-up task via **backlog-manager**:

```bash
"$AH" task create "<title>" --description "<from PROP body>"
```

### Step 4 — Write the judge report

**Before exiting the session** write a markdown report to:

```
<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md
```

Use the `Write` tool (not Bash). Filename example:
`2026-05-07T14-30-00Z-report.md`. This is the **only** allowed direct
write under `.agent/` for the judge role; everything else goes through
CLI.

Template:

```markdown
# Judge report — <UTC ts>

## Mode
autopilot | interactive

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

---

## Mode B — Interactive (HITL=true)

Same four steps, but interleaved with ad-hoc user dialogue. The user
may at any point ask you to:

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
