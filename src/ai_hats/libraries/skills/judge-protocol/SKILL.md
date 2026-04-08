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

- **Output channel:** Print the full judge retrospective to **stdout**, wrapped
  between the literal markers `BEGIN_JUDGE_RETRO` and `END_JUDGE_RETRO`. The
  parent process (JudgeRunner) extracts everything between these markers,
  validates it via the HATS-051 loader, and saves it to
  `.agent/retrospectives/judge/YYYY-MM-DD-judge-NNN.md`. **Do NOT write the file
  yourself** — the parent assigns the filename and counter.
- **Format:** `hats-judge-retro/v1` markdown — YAML frontmatter (`---`) followed
  by a markdown body. The frontmatter MUST be the first thing inside
  `BEGIN_JUDGE_RETRO`. Anything you print outside the markers is ignored.
- **Validation:** The parent runs the loader on your output. On schema failure
  the parent will spawn you again with a correction prompt that includes the
  validation error — respond by reprinting a corrected document between the
  markers.

### Output template (print this to stdout)

```
BEGIN_JUDGE_RETRO
---
schema: hats-judge-retro/v1
judge_run_id: judge-YYYY-MM-DD-NNN
project: <project-name>
date: YYYY-MM-DD
bundle_id: BUNDLE-YYYY-MM-DD-NNN   # references the analyzed bundle
findings:                           # min 1 required
  - id: F1
    title: ...
    category: knowledge|environment|process|communication|assumption|tooling
    severity: low|medium|high|critical
    cost_minutes: <float>
    root_cause: ...
    evidence:                       # min 1 required
      - session_id: <which session>
        source: audit|metrics|session_retro|git|external
        location: "audit.md:Turn 4"
        quote: ...                  # optional
    proposed_fix:                   # optional
      type: skill_update|rule_create|memory|code_change|...
      target:
        kind: skill|rule|trait|memory|project_md|code|external
        name: <identifier>
      description: ...
      expected_impact:              # REQUIRED if status=tracked AND type is skill/rule
        reduces_category: ...
        reduces_root_cause_pattern: ...
        target_frequency_after: 0.0
        observation_window_retros: 10
    status: open|applied|tracked|rejected
    task_ref: <backlog id>          # REQUIRED if status=tracked
patterns_to_keep:
  - "..."
meta_critique: "..."                # optional self-assessment
---

# Markdown body — narrative analysis (sections 1-7 below)
END_JUDGE_RETRO
```

### Markdown body (under frontmatter)
1. **Session summary** — what was asked, what was done
2. **Metrics overview** — key numbers from metrics.json, baseline comparison
3. **Findings** — shortcuts, technical debts, hallucinations, instruction violations
4. **Efficiency assessment** — was the token/tool budget reasonable?
5. **Actionable corrections** — concrete rule/skill changes for next iteration
6. **Score** (1-10) with justification
7. **Meta-retrospective** — self-critique of the judge analysis itself

The body content should mirror the findings in the frontmatter — frontmatter
is the machine-readable index, body is the human-readable narrative.

- **Hard Truths:** Explicitly list problems. Be specific, not generic.
- **Evidence:** Every finding must cite specific audit events, log lines, or metric values.

## Completion
- Full judge retro printed to stdout between `BEGIN_JUDGE_RETRO` and
  `END_JUDGE_RETRO` markers (parent saves the file)
- `hats-judge-retro/v1` schema validation passes (parent runs the loader)
- Every finding has min 1 evidence with explicit `session_id`
- Tracked skill/rule fixes carry `expected_impact` for longitudinal validation
- Metrics comparison included (current vs baseline)

## Anti-Patterns
- Writing the file yourself instead of printing between markers (parent will
  not pick it up; the worktree runs in `discard` mode and the file will be lost)
- Adding commentary or analysis outside the `BEGIN_JUDGE_RETRO`/`END_JUDGE_RETRO`
  markers (ignored by the parent extractor)
- Writing free-form markdown without frontmatter (will fail validation)
- Generic praise ("agent did well overall") — be adversarial, find the gaps
- Findings without evidence — cite specific audit events or metric values
- Corrective actions without specificity ("be more careful") — propose exact rule/skill changes
- Ignoring metrics.json — always include quantitative analysis
- Analyzing audit.md without checking metrics — qualitative alone is insufficient
- Tracked skill/rule fix without `expected_impact` — breaks longitudinal cycle
