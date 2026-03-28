# Audit Reviewer

Internal code review via triple-perspective debate before finalizing any non-trivial change.

## When to Use
- Before finalizing any non-trivial code change
- Self-review when no human reviewer is available
- Evaluating third-party code or pull requests

## Three Perspectives

### 1. Architect (Structural Integrity)
- Do design patterns, SOLID/DRY hold?
- Does this introduce technical debt or break existing abstractions?

### 2. Security Expert (Risk Mitigation)
- OWASP/SANS principles, least privilege, injection risks.
- Could an attacker exploit this change?

### 3. Quality Engineer (Verification & Performance)
- Test coverage, edge cases, silent failure modes.
- Is there a scenario where this fails silently or performs poorly?

## Self-Refinement Loop
1. **Debate** — one key concern from each perspective.
2. **Refine** — address at least one concern before finalizing.
3. **Approve** — finalize only when no perspective has a blocking issue.

## Output
Include a brief "Internal Review" section in your response noting the three perspectives. A change is "Reviewed" only when this section is present.

## Completion
- All 3 perspectives evaluated with at least one concern each
- Blocking issues resolved before approval
- "Internal Review" section present in the response

## Anti-Patterns
- Rubber-stamp review — all three perspectives say "looks good" without substance
- Skipping refinement loop — listing concerns without addressing them
- Only reviewing happy path — security and edge cases matter most
