# Hypothesis Workflow

Closed-loop improvement cycle for agent quality. Findings from judge
retros become hypotheses tracked as backlog tasks, validated on new data.

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, resolve the binary once:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> "$AH" judge-aggregate --since 2025-01-01
> ```
> If neither works, the project's venv lives at `./.venv/bin/ai-hats`. Never call bare `ai-hats` blindly and fall back — it wastes a turn.

## When to Use

After accumulating 3+ judge retros and noticing recurring patterns.

## Flow 1: Discover Patterns and Create Hypothesis

### Step 1: Aggregate findings

```bash
ai-hats judge-aggregate [--since YYYY-MM-DD] [--min-severity medium]
```

Review the aggregation report. Look for clusters with frequency > 1
and rate > 30% — these are recurring patterns worth addressing.

### Step 2: Discuss with judge

```bash
ai-hats judge --last N --interactive
```

Or re-run judge with a focus on the aggregated pattern:

```bash
ai-hats judge --last N --focus "tool-call batching" --interactive
```

In the interactive session:
- Discuss root causes of recurring findings
- Evaluate proposed fixes from the aggregation report
- Decide which patterns to address first (severity x frequency)

### Step 3: Create hypothesis task

A hypothesis is a regular backlog task with tag `hypothesis`.

```bash
ai-hats task create "hypothesis: <description of expected improvement>" \
  -p medium --tag hypothesis \
  -d "Baseline: <pattern> appears in X% of retros. \
      Change: <what we will change>. \
      Expected: <pattern> drops to Y% after N retros. \
      Observation window: N judge retros."
```

### Step 4: Implement the change

Apply the improvement (rule update, skill change, etc.) and transition
the hypothesis task to `execute`.

## Flow 2: Validate Hypothesis

### Step 1: Accumulate new data

After the observation window (N judge retros post-change), re-aggregate:

```bash
ai-hats judge-aggregate --since <date-of-change>
```

### Step 2: Compare with baseline

Check if the target pattern still appears in the new aggregation.
Compare rate before vs after.

### Step 3: Discuss results

```bash
ai-hats judge --last N --focus "<hypothesis pattern>" --interactive
```

### Step 4: Close the hypothesis

- **Validated**: pattern frequency dropped to target or below.
  Transition task to `done` with work_log noting the result.
- **Ineffective**: pattern persists at similar frequency.
  Log the result, consider alternative approaches, create a new hypothesis
  or reject the approach.
- **Inconclusive**: not enough data yet. Extend the observation window.

## Anti-Patterns

- Applying changes without creating a hypothesis task — no way to track effect
- Skipping the aggregation step — acting on single findings instead of patterns
- Closing hypotheses before the observation window completes
- Changing multiple things at once — can't attribute improvement to a specific change
