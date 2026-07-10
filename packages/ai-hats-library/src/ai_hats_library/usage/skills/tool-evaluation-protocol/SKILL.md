---
name: tool-evaluation-protocol
description: Time-bounded protocol for evaluating new tools, libraries, or methodologies before adoption. Use when evaluating a candidate dependency for the project, researching a methodology (e.g. ML/LLM frameworks, testing approaches), or following up on a hype-driven recommendation.
license: MIT
---
# Tool Evaluation Protocol

Decide whether to adopt an external tool, library, or methodology with bounded effort.

## When to Use
Only for a *candidate* — a tool/library/methodology not yet adopted, especially
a hype-driven one. Not for tools already standard in the stack (just use them),
and not a written feature-comparison essay — the protocol is a time-boxed PoC
that ends in an adopt/reject call, not analysis for its own sake.

## Procedure

1. **Frame** — Candidate (name, version, source). Purpose: what it enables or replaces. Known alternatives.

2. **Kill-criteria check** — Hard blockers warranting immediate `reject` without PoC: license incompatibility, dormant upstream (no commits/issues for >12 months without maintainer note), runtime incompatibility, non-OSS dependency where OSS-only is required. Document any fired criterion and stop.

3. **Maturity assessment** — Signals: years in production, contributor count, breaking-change history, ecosystem adoption. Apply **Lindy bias** for core paths (older + boring beats newer + exciting); apply **Hype Cycle skepticism** for ML/LLM-fresh tools (heavy stars in last 12mo → halve confidence). Output: signals + bias-adjusted readout, not yes/no.

4. **PoC** — Set budget **before execution**: time (one working session unless justified) and scope (one real project artifact, end-to-end, not a toy). List explicit **assumptions** the PoC will validate, in categories: functional, integration, performance, cost. Run within budget. Record per-assumption pass/fail. **Stop on time-budget exhaustion regardless of completion %.**

5. **Decision** — `adopt` / `evaluate-further` / `reject` + ADR (see references/adr-template.md). Document assumption verdicts, PoC results, residual risk. Pick `evaluate-further` **only if** a specific blocker can be resolved with bounded effort; indefinite "monitoring" counts as `reject`.

## Completion
- ADR written using `references/adr-template.md` structure
- Decision recorded with per-assumption verdicts
- For `adopt` — follow-up tickets for integration; for `reject` — re-open criteria documented

## Anti-Patterns
- Skipping the kill-criteria check ("we'll see during PoC") — wastes time on doomed candidates
- Letting PoC overflow the budget because "we're close" — anti-Hofstadter rule violated
- Choosing `evaluate-further` as a way to defer the decision indefinitely
- Tuning the candidate to pass the metric rather than testing real fit — once the metric becomes the target, it stops measuring fit (Goodhart's law)
