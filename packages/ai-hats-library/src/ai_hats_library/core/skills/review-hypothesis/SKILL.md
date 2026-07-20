---
name: review-hypothesis
description: Vote on one active hypothesis (HYP-NNN) against session evidence — pick confirmed/refuted/inconclusive/n-a + a recommendation and persist via `rack hyp`. Use when sweeping active hypotheses during a session review (reflect-session, session-reviewer, or judge).
license: MIT
---

# Review Hypothesis

Vote on one active hypothesis (`HYP-NNN`). Role-agnostic: same procedure
whether you are running as `reflect-session`, `session-reviewer`, `judge`,
or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }  # HATS-790: no bin/ai-hats console script
> ```

## When to Use

Boundaries & disambiguation (the description already states the trigger):

- **One verdict per active HYP — no skipping.** A sweep that omits a HYP
  is rejected by the post-validator.
- **`n/a` is narrow** — reserved for sessions that *physically cannot*
  test the HYP. If you are merely unsure whether it applies, write
  `inconclusive`, not `n/a`.
- **Meta-problems go to `review-proposal`**, not into the verdict. If the
  HYP is ambiguous/untestable, file a meta-proposal and write
  `inconclusive` citing it — do not guess a verdict.

## Procedure

### Step 1 — Read the hypothesis

```bash
ah task hyp show HYP-NNN
```

Pay attention to `success_criterion`, `observation_window`, `exit_criteria`,
`expected_outcome`, and `freshness_rule` — these define what
`confirmed` / `refuted` / `inconclusive` / `n/a` mean for *this* hypothesis.

### Step 1.5 — Read the HYP's `verification_protocol` (HATS-528)

If the HYP YAML contains a **`verification_protocol`** field, your
`--evidence` string MUST follow that protocol — verbatim if it
prescribes a literal format, near-verbatim if it prescribes a shape.
The protocol is the HYP author's contract with future auditors:
"this is how I want you to report on this specific hypothesis".

The field is free-form text written by the HYP author at document-stage,
after the diff is final (see **library-change-hypothesis-protocol**;
HYPs are filed post-ship, not at plan — HATS-567). It is stored under
`Hypothesis.extra` (the schema is permissive) — `rack context HYP-NNN`
prints it verbatim; do not silently drop it.

**If `verification_protocol` is absent** — proceed with free-form
evidence per the original convention. Legacy HYPs (001–015) have no
protocol field; their verdicts continue to look the way they always
have.

**If your verdict is `n/a`** — protocol-shape compliance is OPTIONAL: a
short free-form rationale (`"session has no <relevant phase>"`) is fine
even when the HYP carries a `verification_protocol`. The protocol is for
signal-bearing verdicts (`confirmed` / `refuted` / `inconclusive`).

Strict vs loose protocol examples (HYP-016 / HYP-017) →
[`references/examples.md`](references/examples.md).

**Format check before persist.** Before calling `append-verdict`, eyeball
your evidence string against the protocol. If they don't match — fix
the evidence (not the protocol; protocols are only refined via a new
task, never silently). If the protocol is ambiguous or untestable
against the available session evidence, raise it via
**review-proposal** with `--category process --target <hyp-id>` rather
than guess.

### Step 2 — Gather session evidence

The session's audit and metrics live in `<ai_hats_dir>/sessions/runs/session_<session_id>/`.
Cite specific lines or metric values; verdicts without traceable evidence
are noise. **If the HYP carries a `verification_protocol`, the gather
phase is constrained by it** — Step 1.5 tells you what to extract.

### Step 3 — Choose verdict

| Value          | Use when                                                       |
| -------------- | -------------------------------------------------------------- |
| `confirmed`    | Evidence directly supports the hypothesis (criterion met).     |
| `refuted`      | Evidence contradicts (baseline pattern persists or got worse). |
| `inconclusive` | Session has relevant data but is mixed/insufficient.           |
| `n/a`          | This session physically cannot test the hypothesis.            |

**`n/a` is reserved** for sessions that physically cannot test the hypothesis
(e.g. trait/role doesn't apply). If you are *unsure* whether the hypothesis
applies — do NOT write `n/a`; file a meta-proposal via **review-proposal**
and write `inconclusive`.

### Step 4 — Choose recommendation

| Value             | Use when                                    |
| ----------------- | ------------------------------------------- |
| `close_confirmed` | Verdict + observation_window threshold met. |
| `close_refuted`   | Verdict + rollback path is clear.           |
| `keep`            | Continue observing.                         |
| `extend_window`   | Window expired without enough evidence.     |

### Step 5 — Persist via CLI

For verdicts that carry signal (`confirmed`, `refuted`, `inconclusive`):

```bash
ah task hyp append-verdict \
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
ah task hyp set-status --hyp HYP-NNN --status {confirmed|refuted|stalled}
```

`append-verdict` does NOT auto-flip status — `set-status` is a separate,
deliberate step.

## Output handoff

How the verdict is *reported* depends on the calling role:

- **Running as `reflect-session` / `session-reviewer`** — the verdict is mirrored in the
  `hypothesis_verdicts` array of the role's session document
  (`hats-reflect-session/v1`); see **review-session**.
- **Running as `judge`** — verdicts feed into the judge report at
  `<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md` per **judge-protocol**.

In both cases the persistence (`append-verdict`, `set-status`) is the same — only
the wrapper artifact differs.

## Examples

Worked examples — confirmed / inconclusive / silent-n/a / missing-verdict,
plus `verification_protocol`-anchored verdicts (HATS-528 PoC) — live in
[`references/examples.md`](references/examples.md).
