---
name: positive-control
description: Distinguish a failed subject from a broken verification path. Use before relying on search or grep output, logs, composed prompts, stored artifacts, or before/after measurements to support a verdict.
license: MIT
---

# Positive Control

Separate subject failure from verification-path failure before issuing a verdict.

## When to Use

This complements exit-code provenance: it validates the evidence path, not
process success. Put deterministic controls in tests; use this protocol when
selecting a sensitive control requires judgment.

## Procedure

1. State the subject claim before the check.
2. Choose a known-outcome control that traverses the same path and would fail
   under the likely verifier fault.
3. Run the control before interpreting the subject.
4. If it fails, the method is invalid: repair it and rerun both checks. Issue no
   subject verdict.
5. If it passes, a subject failure belongs to the subject; a pass is usable
   evidence for the claim.
6. Before a delta, establish each input's intended source identity.

## Completion

- Report the subject, control, both outcomes, and provenance; a failed control
  leaves no subject verdict.
- Validation — case 3: RED compares a missing baseline with the current
  tree and falsely reports zero delta. GREEN checks each component's intended
  root; the baseline control fails, so the agent withholds the delta verdict.

## Anti-Patterns

- Another parser, source, or normalization path isolates nothing; use the
  subject's exact path.
- An insensitive control can pass on wrong or empty input; choose one that
  detects the suspected fault.
- Success or zero delta without provenance is ambiguous; establish identity.
