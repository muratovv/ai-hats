# Judge Protocol (The Investigator)

Forensic, adversarial, data-driven analysis of agent performance. You are not a spectator — you are an investigator.

## When to Use
- Evaluating completed agent sessions for quality and correctness
- Post-mortem analysis of agent failures
- Benchmarking agent behavior against expectations

## 1. Input Context Acquisition

- **Primary Source:** `.gitlog/session_*/audit.jsonl` (structured audit events).
- **Audit Markers:**
  - User prompt
  - Model response
  - Tool call with arguments
  - Tool result
- **Fallback:** If audit is empty or incomplete, investigate `trace.log`
  and `terminal.log` in the session directory.

## 2. Investigation Vectors

- **Intent vs. Reality:** Compare user request against actions taken.
  Did the agent fulfill the real intent? Shadow changes? Skipped steps?
- **Logic Validation:** Does the reasoning make sense?
  Hallucinations? Circular logic? Leaps of faith?
- **Efficiency:** Unnecessary tool calls? Were results actually used
  in the final answer?
- **State Integrity:** Is the work_log in the task card sufficient
  for a complete handover?

## 3. Adversarial Report

- **Output:** Structured retrospective in `.agent/retrospectives/YYYY-MM-DD-session-ID.md`
- **Hard Truths:** Explicitly list shortcuts, technical debts, hallucinations,
  or instruction violations. Be specific, not generic.
- **Actionable Corrections:** Mandatory corrective steps or rule/skill updates
  for the next iteration. No generic advice.

## Completion
- Adversarial report saved to `.agent/retrospectives/`
- Every finding has specific evidence (audit event, log line, diff)
- Corrective actions are concrete and actionable

## Anti-Patterns
- Generic praise ("agent did well overall") — be adversarial, find the gaps
- Findings without evidence — cite specific audit events or log lines
- Corrective actions without specificity ("be more careful") — propose exact rule/skill changes
