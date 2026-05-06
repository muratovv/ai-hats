# Hypothesis Validation

Vote on active hypotheses (`.agent/hypotheses/HYP-*.yaml`, status: active)
during a single-session reflect run. One verdict per active HYP — no skipping.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You are running as the **reflect-session** role. Your output is a
`hats-reflect-session/v1` document; the `hypothesis_verdicts` array MUST
contain one entry per active HYP. For verdicts that carry signal
(`confirmed`/`refuted`/`inconclusive`) you also persist to the HYP ledger
via CLI; `n/a` verdicts are recorded in the retro frontmatter only (see
Step 3).

## Procedure

### Step 1 — Enumerate active hypotheses

```bash
"$AH" task hyp list --status active --json
```

For each `HYP-NNN`, read its file:

```bash
"$AH" task hyp show HYP-008
```

Pay attention to `success_criterion`, `observation_window`, `exit_criteria`,
and `freshness_rule` — these determine what "confirmed" / "refuted" / "n/a" mean
for *this* hypothesis.

### Step 2 — Gather session evidence

The session's audit, metrics, and (if present) session retro live in
`.gitlog/session_<session_id>/`. Cite specific lines or metrics.

### Step 3 — Choose verdict + write CLI entry

Verdict enum:

| Value | Use when |
|---|---|
| `confirmed` | Evidence directly supports the hypothesis (criterion met). |
| `refuted` | Evidence contradicts (baseline pattern persists or got worse). |
| `inconclusive` | Session has relevant data but is mixed/insufficient. |
| `n/a` | This session physically cannot test the hypothesis (e.g. trait/role doesn't apply). |

Recommendation enum:

| Value | Use when |
|---|---|
| `close_confirmed` | Verdict + observation_window threshold met. |
| `close_refuted` | Verdict + rollback path is clear. |
| `keep` | Continue observing. |
| `extend_window` | Window expired without enough evidence. |

After choosing, persist via CLI (atomic, filelock-protected) — **but only
for verdicts that carry signal**: `confirmed`, `refuted`, `inconclusive`.

```bash
"$AH" task hyp append-verdict \
  --hyp HYP-008 --session "$SID" \
  --verdict inconclusive --evidence "audit.md:Turn 3 — no Bash anti-pattern usage observed" \
  --recommendation keep
```

**Do NOT call `append-verdict` for `n/a` verdicts.** `n/a` means the session
physically cannot test the hypothesis — there is no observation to record,
and persisting it would only add noise to the validation log without
contributing to the observation window. Mirror the `n/a` verdict in the
`hypothesis_verdicts` frontmatter array (so the retro stays complete) but
skip the CLI call.

Then mirror every verdict in your `hypothesis_verdicts` frontmatter array.

### Step 4 — When `n/a` is allowed

Only when the session **physically cannot** test the hypothesis. If you are
*unsure* whether the hypothesis applies — do **not** write `n/a`; instead
file a meta-proposal (see `proposal-management`) and write `inconclusive`.

## Examples

### ✓ Good: confirmed verdict

HYP-008 success_criterion: "bash_anti_count == 0 in ≥4 of 5 sessions".
Session metrics: `bash_anti_count: 0`. → `confirmed`, `evidence: "metrics.json:bash_anti_count=0"`, `recommendation: keep` (need 4 more).

### ✓ Good: inconclusive with cited evidence

HYP-003 about over-engineering. Session is a planning-only session with no
implementation. → `inconclusive`, `evidence: "session has no implementation phase to evaluate"`, `recommendation: keep`.

### ✗ Bad: silent n/a

HYP-005 about neutral example prefixes. You don't understand what the
hypothesis means. Filing `n/a` "to be safe" — this hides a knowledge gap.

**Correct response**: file a meta-proposal:

```bash
"$AH" task proposal create \
  --category process --target reflect-session \
  --title "HYP-005 phrasing ambiguous to reflect-session" \
  --description "..." --rationale "..." \
  --session "$SID"
```

Then write `inconclusive` for HYP-005, citing `evidence: "see PROP-NNN — reflect-session could not interpret success_criterion"`, and add the PROP id to `self_problems`.

### ✗ Bad: missing verdict

Output omits one or more active HYPs from `hypothesis_verdicts`. The runtime
post-validator rejects this and files an automatic meta-proposal.
**Always emit one entry per active HYP**, even when the answer is `n/a`.
