# Review Hypothesis

Vote on one active hypothesis (`HYP-NNN`). Role-agnostic: same procedure
whether you are running as `reflect-session`, `session-reviewer`, `judge`,
or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You are evaluating one HYP-NNN against session evidence (`audit.md`,
`metrics.json`, session retro) — typically as part of a sweep over all
active hypotheses (one verdict per HYP, no skipping).

## Procedure

### Step 1 — Read the hypothesis

```bash
"$AH" task hyp show HYP-NNN
```

Pay attention to `success_criterion`, `observation_window`, `exit_criteria`,
`expected_outcome`, and `freshness_rule` — these define what
`confirmed` / `refuted` / `inconclusive` / `n/a` mean for *this* hypothesis.

### Step 2 — Gather session evidence

The session's audit and metrics live in `.gitlog/session_<session_id>/`.
Cite specific lines or metric values; verdicts without traceable evidence
are noise.

### Step 3 — Choose verdict

| Value | Use when |
|---|---|
| `confirmed` | Evidence directly supports the hypothesis (criterion met). |
| `refuted` | Evidence contradicts (baseline pattern persists or got worse). |
| `inconclusive` | Session has relevant data but is mixed/insufficient. |
| `n/a` | This session physically cannot test the hypothesis. |

**`n/a` is reserved** for sessions that physically cannot test the hypothesis
(e.g. trait/role doesn't apply). If you are *unsure* whether the hypothesis
applies — do NOT write `n/a`; file a meta-proposal via **review-proposal**
and write `inconclusive`.

### Step 4 — Choose recommendation

| Value | Use when |
|---|---|
| `close_confirmed` | Verdict + observation_window threshold met. |
| `close_refuted` | Verdict + rollback path is clear. |
| `keep` | Continue observing. |
| `extend_window` | Window expired without enough evidence. |

### Step 5 — Persist via CLI

For verdicts that carry signal (`confirmed`, `refuted`, `inconclusive`):

```bash
"$AH" task hyp append-verdict \
  --hyp HYP-NNN --session "$SID" \
  --verdict {confirmed|refuted|inconclusive} \
  --evidence "<one-line citation from audit.md or metrics.json>" \
  --recommendation {close_confirmed|close_refuted|keep|extend_window}
```

**Do NOT call `append-verdict` for `n/a` verdicts.** `n/a` means the session
physically cannot test the hypothesis — there is no observation to record.

### Step 6 — Close (when window closes)

When `--recommendation` was `close_confirmed` or `close_refuted` and the
observation window has filled, flip the status:

```bash
"$AH" task hyp set-status --hyp HYP-NNN --status {confirmed|refuted|stalled}
```

`append-verdict` does NOT auto-flip status — `set-status` is a separate,
deliberate step.

## Output handoff

How the verdict is *reported* depends on the calling role:

- **Running as `reflect-session` / `session-reviewer`** — the verdict is mirrored in the
  `hypothesis_verdicts` array of the role's session document
  (`hats-reflect-session/v1`); see **review-session**.
- **Running as `judge`** — verdicts feed into the judge report at
  `.agent/retrospectives/judge/<UTC-ISO-ts>-report.md` per **judge-protocol**.

In both cases the persistence (`append-verdict`, `set-status`) is the same — only
the wrapper artifact differs.

## Examples

### ✓ Good: confirmed verdict

HYP-008 success_criterion: "bash_anti_count == 0 in ≥4 of 5 sessions".
Session metrics: `bash_anti_count: 0`. → `confirmed`,
`evidence: "metrics.json:bash_anti_count=0"`, `recommendation: keep`
(need 4 more).

### ✓ Good: inconclusive with cited evidence

HYP-003 about over-engineering. Session is a planning-only session with no
implementation. → `inconclusive`,
`evidence: "session has no implementation phase to evaluate"`,
`recommendation: keep`.

### ✗ Bad: silent n/a

HYP-005 about neutral example prefixes. You don't understand what the
hypothesis means. Filing `n/a` "to be safe" hides a knowledge gap.

**Correct response**: file a meta-proposal via **review-proposal**, then
write `inconclusive` for HYP-005 citing the meta-proposal id in evidence.

### ✗ Bad: missing verdict

A sweep that omits one or more active HYPs. The runtime post-validator
(or the judge) rejects this and files an automatic meta-proposal.
**Always emit one verdict per active HYP**, even when the answer is `n/a`.
