---
name: positive-control
description: Distinguish a failed subject from a broken verification path. Use before relying on search or grep output, logs, composed prompts, stored artifacts, or before/after measurements to support a verdict.
license: MIT
---

# Positive Control

Separate subject failure from verification-path failure before using evidence
as a verdict.

## When to Use

This complements exit-code provenance: it tests whether the evidence path is
valid, not whether a process exited successfully. Encode deterministic controls
as test assertions; use this protocol when choosing a sensitive control requires
judgment.

## Procedure

1. State the subject claim before running the check.
2. Choose a control whose outcome is known in advance, traverses the same path,
   and would fail under the likely verifier fault.
3. Evaluate the control before interpreting the subject result.
4. Control failed: the method is invalid. Repair it and rerun both checks before
   issuing a verdict.
5. Control passed: interpret the subject result. A failure belongs to the
   subject; a pass is usable evidence for the claim.
6. For before/after comparisons, establish each input's intended source identity
   before computing the delta.

## Completion

- The evidence report names the subject, the control, both outcomes, and their
  provenance.
- A failed control leaves no subject verdict; the repaired method evaluates both
  again.
- Validation scenario — HATS-1824 case 3: RED compares a missing baseline with
  the current tree and falsely reports a zero delta. GREEN checks that each
  composed component comes from its intended root; the baseline control fails,
  so the agent withholds the delta verdict.

## Anti-Patterns

- Route the control through the same parser, source, and normalization path as
  the subject.
- Choose a control sensitive to the suspected fault, not one that stays true
  when the checker reads the wrong source or empty input.
- Establish artifact and source identity before treating success or zero delta
  as evidence.
