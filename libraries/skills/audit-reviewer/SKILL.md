# Audit Reviewer

Internal code review via triple-perspective debate before finalizing any non-trivial change.

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
