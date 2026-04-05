---
name: skill-optimization
description: Audit and refactor library components to eliminate redundancy and staleness
---
# Skill Optimization

Audit and refactor library components (rules, skills, traits) to eliminate redundancy, verbosity, and stale instructions.

## When to Use
- Periodic library maintenance
- After adding multiple new skills or rules
- When token budget for a role is too high
- After retrospectives identify component issues

## Procedure

1. **Audit:** Review `.agent/retrospectives/` for recurring issues.
   List all active rules and skills in the current role composition.

2. **Identify Debt:**
   - Redundancy: two components covering the same behavior
   - Stale instructions: references to removed tools, obsolete workflows
   - Verbosity: rules that can be condensed without losing meaning
   - Misplaced content: rule that should be a skill (has a procedure),
     or skill that should be a rule (is just a constraint)

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
