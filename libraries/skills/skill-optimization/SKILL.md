# Skill Optimization

Audit and refactor library components (rules, skills, traits) to eliminate
redundancy, verbosity, and stale instructions.

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
