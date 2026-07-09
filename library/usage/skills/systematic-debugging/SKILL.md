---
name: systematic-debugging
description: 4-phase bug-fix protocol (evidence, pattern, hypothesis, verify). Use when investigating any error, failure, or unexpected behavior, handling bug reports from users or monitoring, or diagnosing test failures with non-obvious causes.
license: MIT
---
# Systematic Debugging

4-phase protocol for bug fixing and error investigation. No guess-and-check.

## When to Use
Code-level root-cause via a failing test — evidence before fixes, no
guess-and-check. Its sibling for *production* trouble is **incident-response**: a
live outage with an on-call / mitigation dimension starts there (stop the
bleeding first) and only then drops into this protocol for the underlying bug.
If there's no running-system urgency, you're in the right place.

## Phase 1: Evidence Gathering
- Read full stack traces and error logs.
- Inspect environment state (env vars, active processes, disk space).
- Map affected files using `rg` and `fd`.

## Phase 2: Pattern Analysis
- Is this isolated or part of a larger pattern?
- Identify blast radius: what else could this affect?
- Check if it's a regression from a recent change (`git log`, `git bisect`).

## Phase 3: Hypothesis & Strategy
- State a falsifiable hypothesis: "The error happens because [X] is in state [Y]."
- Design a minimal reproduction script or failing test.
- Only then draft the fix.

## Phase 4: Implementation & Verification
- Apply minimal, surgical changes.
- Run the reproduction script to confirm the fix.
- Run the full test suite for regression check.
- A task is not done until behavioral correctness is verified.

## Completion
- Root cause identified and documented
- Fix applied with minimal surgical changes
- Reproduction script or failing test confirms the fix
- Full test suite passes (no regressions)

## Bundled Rules

### Pessimistic Verification
1. **Anti-Momentum**: Do NOT proceed to the next step until the current one is physically verified (tests, lint, check command).
2. **Assumption Audit**: Before execution, list assumptions. If testable, test first.
3. **Foundation First**: When modifying shared components, sanity check after every file modification.
4. **No Premature Optimization**: Do not clean up unrelated code during a critical fix.

## Anti-Patterns
- Guess-and-check — changing things randomly hoping it works
- Fixing symptoms instead of root cause — the bug will return
- Skipping regression check — fix one bug, introduce two more
