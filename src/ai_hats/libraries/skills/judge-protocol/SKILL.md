---
name: judge-protocol
description: Forensic adversarial analysis of agent performance from audit logs and metrics
---
# Judge Protocol (The Investigator)

Forensic, adversarial, data-driven analysis of agent performance. You are not a spectator — you are an investigator.

## When to Use
- Evaluating completed agent sessions for quality and correctness
- Post-mortem analysis of agent failures
- Benchmarking agent behavior against expectations
- Comparing efficiency across sessions

## 1. Input Context Acquisition

### Qualitative: audit.md
- **Location:** `.gitlog/session_*/audit.md`
- **Markers:** `👤` user prompt · `👾` model response · `🔧` tool call · `💭` thinking
- **Fallback:** If audit is empty or incomplete, investigate `trace.log` in the session directory.

### Quantitative: metrics.json
- **Location:** `.gitlog/session_*/metrics.json`
- **Fields:**
  - `exit_code` — 0 = success, non-zero = failure
  - `role`, `provider` — session context
  - `turns` — number of conversation turns
  - `tokens.input`, `tokens.output` — total token usage
  - `tokens.cache_read`, `tokens.cache_creation` — cache efficiency
  - `models.<name>.calls` — API call count per model
  - `tool_calls` — total tool invocations

### Cross-session context
- List recent sessions: `ls .gitlog/` sorted by date
- Read metrics.json from several recent sessions to establish baselines
- Compare current session against baselines

## 2. Investigation Vectors

### Qualitative (from audit.md)
- **Intent vs. Reality:** Compare user request against actions taken.
  Did the agent fulfill the real intent? Shadow changes? Skipped steps?
- **Logic Validation:** Does the reasoning make sense?
  Hallucinations? Circular logic? Leaps of faith?
- **State Integrity:** Is the work_log in the task card sufficient
  for a complete handover?

### Quantitative (from metrics.json)
- **Efficiency:** tokens per turn (output / turns), tool calls per turn.
  High ratios may indicate unnecessary work or retry loops.
- **Cost:** total output tokens. Compare with session complexity.
  Simple tasks should not consume 50k+ output tokens.
- **Cache utilization:** cache_read / (cache_read + input). High = good context reuse.
- **Tool density:** tool_calls / turns. Very high = possible fix-by-retry pattern.
  Very low in coding sessions = possible hallucination without verification.
- **Exit code:** Non-zero indicates abnormal termination. Investigate why.

### Efficiency Red Flags
- `tool_calls / turns > 10` — excessive tool use, possible retry loops
- `output tokens > 30k` for routine tasks — over-explanation or scope creep
- `turns > 20` for simple tasks — lack of focus or circular approach
- `cache_read = 0` — no context reuse, possible session management issue

## 3. Adversarial Report

- **Output:** Structured retrospective in `.agent/retrospectives/YYYY-MM-DD-session-ID.md`
- **Required sections:**
  1. **Session summary** — what was asked, what was done
  2. **Metrics overview** — key numbers from metrics.json, comparison with baselines
  3. **Findings** — shortcuts, technical debts, hallucinations, instruction violations
  4. **Efficiency assessment** — was the token/tool budget reasonable for the task?
  5. **Actionable corrections** — concrete rule/skill changes for next iteration
  6. **Score** (1-10) with justification

- **Hard Truths:** Explicitly list problems. Be specific, not generic.
- **Evidence:** Every finding must cite specific audit events, log lines, or metric values.

## Completion
- Adversarial report saved to `.agent/retrospectives/`
- Every finding has specific evidence (audit event, metric value, diff)
- Corrective actions are concrete and actionable
- Metrics comparison included (current vs baseline)

## Anti-Patterns
- Generic praise ("agent did well overall") — be adversarial, find the gaps
- Findings without evidence — cite specific audit events or metric values
- Corrective actions without specificity ("be more careful") — propose exact rule/skill changes
- Ignoring metrics.json — always include quantitative analysis
- Analyzing audit.md without checking metrics — qualitative alone is insufficient
