---
name: library-change-hypothesis-protocol
description: At plan-stage of a library-curation task, file a companion HYP with an explicit verification_protocol — or record an explicit "no behavior change" note for refactors.
---

# Library-Change Hypothesis Protocol

> **PoC status (HATS-527).** This skill is intentionally lightweight.
> The framework does NOT enforce companion-HYP filing — discipline lives
> here. If the PoC produces signal that curators skip the step, a future
> task may lift enforcement into the engine.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

Triggered at plan-stage of any task parented to **HATS-499** (the
agent-behavior library-curation epic) — that is, any change to
`library/{core,usage}/roles/`, `library/{core,usage}/traits/`,
`library/{core,usage}/skills/`, or `library/core/rules/`.

Skip when: harness-only edits (`src/ai_hats/`, `cli/`, `scripts/`,
`_bootstrap.py`, `cli/maintenance.py`), tasks outside HATS-499, or pure
refactors with no observable behavior delta.

## Why

Without a companion HYP, library-curation changes ship blind:

- HATS-510 / HATS-520 / HATS-521 shipped behavior-changing edits with
  no HYP filed. No data tells us whether the change worked.
- HATS-514 spawned HYP-014 only because the user prompted explicitly.
  The discipline does not survive without a checklist.
- Even when HYPs exist, their `success_criterion` text is interpreted
  qualitatively. HYP-007 was marked `confirmed` on one audit cite
  against a "≥3 of 4 transitions" criterion. The criterion text alone
  did not constrain the auditor.

This skill addresses the producer side: every behavior-changing
library edit gets a companion HYP whose YAML carries a
**`verification_protocol`** field — explicit, free-form text telling
future auditors exactly what shape of `--evidence` to emit. The
consumer side (auditor follows the protocol) lives in
**review-hypothesis** (HATS-528).

## Procedure

### Step 1 — Behavior delta check

In your task's `plan.md` (or work_log if you skipped a plan-md), answer
two questions explicitly:

1. **Prior behavior?** What did agents observably do before this edit?
2. **Post-change behavior?** What should they observably do after?

Both "no observable change" → **pure refactor**. Record the decision
explicitly in `plan.md` or via `ai-hats task work-log` (one line:
"no behavior change — pure refactor: <reason>"). Skip the rest of this
skill.

Otherwise → continue to Step 2.

### Step 2 — Author the HYP

Use `ai-hats task hyp create` (or hand-write the YAML; both routes
work). Required fields per the existing `Hypothesis` schema:
`id`, `title`, `status: active`, `created`, `source_task`,
`hypothesis`, `baseline`, `observation_window`, `success_criterion`.

**This skill adds one more field — `verification_protocol`** — a
free-form string telling auditors how to shape their `--evidence` for
this specific HYP. Examples below.

The field is unrecognized by the framework's typed schema but
`Hypothesis` carries `extra="allow"`, so it round-trips through YAML
load/save with no engine change.

> **Picking the data shape.** Apply `design-minimalism`'s
> behavioral-delivery escalation ladder. Text-in-YAML under
> `extra="allow"` (rung 1) is almost always sufficient; lift to typed
> schema (rung 6) only after a sweep shows the loose shape produces
> unreadable verdicts.

#### `verification_protocol` examples

**Strict format** (auditor evidence is a tight machine-readable block):

```yaml
verification_protocol: |
  Evidence MUST be exactly three lines, no prose:
  Line 1: "CRITERION: <verbatim string from this HYP's success_criterion>"
  Line 2: "OBSERVED: <verbatim cite from audit.md or work_log, OR 'NOT OBSERVED'>"
  Line 3: "VERDICT_REASON: satisfies | fails | silent"
```

**Loose format** (auditor evidence is short prose):

```yaml
verification_protocol: |
  Evidence: one paragraph (1–4 sentences). Address (a) what the
  success_criterion asks for, (b) what the session showed, (c) whether
  (b) satisfies, fails, or is silent on (a). Verbatim quotes encouraged
  but optional.
```

**Window-counter format** (auditor maintains a running tally):

```yaml
verification_protocol: |
  Evidence MUST start with "WINDOW: <N>/<M>" where N = current sweep
  count incl. this one, M = observation_window target. Then one line
  citing whether this session moved the counter.
```

Pick whichever protocol matches the kind of verdict you actually want
to be able to read back in 4 weeks. Write the protocol so an auditor
who has never seen the task can comply.

### Step 3 — Cross-link

- Task description references the new `HYP-NNN`.
- HYP `source_task` points back to this task.
- Final commit body includes both IDs: e.g. `HATS-527 / HYP-016`.

## Acceptance for this skill's own run (dogfood)

This very skill ships under HATS-527 alongside HYP-016. HYP-016's
`verification_protocol` is the strict-three-line format above. HATS-528
ships in the same PR with HYP-017 carrying the loose-paragraph format.
The next 4 reflect-session / judge sweeps will tell us which protocol
auditors comply with.

## Examples

### ✓ Good

Task: rewrite `review-session` skill to add a new output field.
Plan-stage: prior behavior = "output has N fields", post-change =
"output has N+1 fields". File HYP-NNN with
`verification_protocol: "Evidence MUST quote the field name from the
session retro YAML and confirm it appears under hypothesis_verdicts[*]"`.

### ✓ Good (refactor exemption)

Task: dedup identical injection text between `trait-base` and
`trait-analyst-base`. Plan-stage: prior = post = same observable
behavior. Record "no behavior change — pure refactor: shared injection
text deduplicated, semantics unchanged". No HYP filed.

### ✗ Bad

Task: add a new rule to `trait-base`. Ship without companion HYP. No
record of expected behavior shift. Two weeks later, no way to tell if
the rule worked.

**Correct response:** file a HYP at plan-stage with
`verification_protocol` describing what audit.md should show; refer to
the HYP in the commit.

## Scope

This skill describes a **discipline**, not a gate. Nothing in the
framework rejects a merge that lacks a companion HYP. If discipline
slips repeatedly (track via HYP-016), a follow-up task may add CI
enforcement.
