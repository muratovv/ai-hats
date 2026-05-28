# review-hypothesis — worked examples

Companion to `../SKILL.md`. Verdict examples, including
`verification_protocol`-anchored ones (HATS-528 PoC).

## Strict vs loose protocols in the wild

- **HYP-016 (HATS-527) — strict three-line protocol:**
  `CRITERION: <verbatim>` / `OBSERVED: <verbatim or NOT OBSERVED>` /
  `VERDICT_REASON: satisfies | fails | silent`. The `--evidence` for
  HYP-016 MUST be exactly those three lines, joined by `\n`.
- **HYP-017 (HATS-528) — loose paragraph protocol:** "one paragraph
  addressing (a) criterion, (b) session observation, (c) whether (b)
  satisfies (a)". Evidence is short prose; verbatim quotes encouraged
  but not required.

## ✓ Good: confirmed verdict

HYP-008 success_criterion: "bash_anti_count == 0 in ≥4 of 5 sessions".
Session metrics: `bash_anti_count: 0`. → `confirmed`,
`evidence: "metrics.json:bash_anti_count=0"`, `recommendation: keep`
(need 4 more).

## ✓ Good: inconclusive with cited evidence

HYP-003 about over-engineering. Session is a planning-only session with no
implementation. → `inconclusive`,
`evidence: "session has no implementation phase to evaluate"`,
`recommendation: keep`.

## ✗ Bad: silent n/a

HYP-005 about neutral example prefixes. You don't understand what the
hypothesis means. Filing `n/a` "to be safe" hides a knowledge gap.

**Correct response**: file a meta-proposal via **review-proposal**, then
write `inconclusive` for HYP-005 citing the meta-proposal id in evidence.

## ✗ Bad: missing verdict

A sweep that omits one or more active HYPs. The runtime post-validator
(or the judge) rejects this and files an automatic meta-proposal.
**Always emit one verdict per active HYP**, even when the answer is `n/a`.

## ✓ Good: protocol-anchored verdict (HATS-528 PoC)

HYP-016 carries the strict three-line `verification_protocol`. Session
evidence: a HATS-499 child task transitioned plan→execute and filed
HYP-NNN with a `verification_protocol` field. Verdict:

```bash
"$AH" task hyp append-verdict \
  --hyp HYP-016 --session "$SID" \
  --verdict confirmed \
  --evidence "CRITERION: ≥3 of next 4 HATS-499 child tasks land with either a companion HYP that has verification_protocol filled, or a work_log entry no behavior change — pure refactor: <reason>.
OBSERVED: HATS-543 plan→execute transition; HYP-019 filed with verification_protocol set; commit body references HATS-543/HYP-019.
VERDICT_REASON: satisfies" \
  --recommendation keep
```

Three lines, joined with newlines, each prefixed exactly as the
protocol demands. A future auditor sweeping HYP-016 will see the same
shape and can compare across the window.

## ✗ Bad: ignoring `verification_protocol`

HYP-016 (strict protocol). Auditor emits free-form prose:
`evidence: "saw HATS-543 file a HYP, looks good"`. Wrong — the
HYP's protocol demanded three labeled lines. Fix by reshaping the
evidence string; do NOT alter the HYP's protocol mid-sweep.
