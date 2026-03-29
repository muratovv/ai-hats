# Domain & Module Reviewer

Coordinate deep-dive reviews of specific system domains or modules by engaging specialized sub-agents as critics.

## When to Use
- Deep review of a specific module or domain (not general code review)
- When multiple specialized perspectives are needed (e.g., security + performance + correctness)
- Pre-release review of critical system components

## Workflow

1. **Scope:** Define boundaries of the module/domain to review
   (e.g., "The Payment Gateway Service", "The Authentication flow").

2. **Select Critics:** Identify sub-agents for the review based on tech stack
   and concerns (e.g., Go code quality, security, SQL performance).

3. **Formulate Prompts:** Draft clear, scoped prompts for each sub-agent,
   asking them to evaluate specific aspects against architectural contracts.

4. **Delegate:** Execute sub-agent calls in parallel if possible.

5. **Synthesize:** Collect reports. Do not copy-paste output.
   Extract critical architectural flaws and document them in an ADR or action plan.

## Completion
- All sub-agent reviews collected and synthesized
- Critical findings documented in ADR or action plan
- No copy-pasted sub-agent output — findings are curated

## Anti-Patterns
- Dumping raw sub-agent output without synthesis — the value is in curation
- Overly broad scope — review one domain at a time, not the entire system
- Skipping parallel execution — sub-agents are independent, run them concurrently
