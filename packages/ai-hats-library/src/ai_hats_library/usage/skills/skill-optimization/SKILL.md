---
name: skill-optimization
description: Audit and refactor library components to eliminate redundancy and staleness. Use during periodic library maintenance, after adding multiple new skills or rules, when a role's token budget is too high, or after retrospectives identify component issues.
license: MIT
---

# Skill Optimization

Audit and refactor library components (rules, skills, traits) to eliminate redundancy, verbosity, and stale instructions.

## When to Use

Cross-component *audit* — dedup, staleness, and token-budget trimming across the
whole rule/skill/trait library. Authoring or validating a *single* skill's
structure is **skill-template**, not this. And the upstream act of turning a
retro finding into a new framework component is **retro-to-framework**; this
skill optimises what already exists.

## Procedure

1. **Audit:** Review `<ai_hats_dir>/sessions/retros/` for recurring issues.
   List all active rules and skills in the current role composition.

2. **Identify Debt:**
   - Redundancy: two components covering the same behavior
   - Stale instructions: references to removed tools, obsolete workflows
   - Verbosity: rules that can be condensed without losing meaning
   - Misplaced content: rule that should be a skill (has a procedure),
     or skill that should be a rule (is just a constraint)
   - Prohibition-led wording: state the target behavior positively; a
     prohibition survives only as a hard guardrail paired with its
     replacement (model: "Redirect instead: `pytest > /tmp/gate.log`")
   - Unowned silences: each decision a component leaves unstated is delegated
     to model priors — make every omission deliberate (fill it, or mark it
     an open question)

3. **Refactor:**
   - Merge fragmented rules into existing traits or skills
   - Condense verbose content into declarative checklists
   - Delete deprecated items
   - Move misplaced content to the right component type

4. **Validate:**
   - Run `composer.compose()` for affected roles — 0 errors
   - Run test suite — all green
   - Spot-check assembled prompt — no regressions

## Completion

- Audit report produced with identified debt
- Refactoring applied and validated (0 errors, tests green)
- Token impact measured before/after

## Anti-Patterns

- Refactoring without measuring token impact — optimization must be quantified
- Deleting components without checking wiring — verify every trait/role still resolves
- Cosmetic changes disguised as optimization — focus on real redundancy and staleness
