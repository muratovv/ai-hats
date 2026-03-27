# Systematic Debugging

4-phase protocol for bug fixing and error investigation. No guess-and-check.

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
