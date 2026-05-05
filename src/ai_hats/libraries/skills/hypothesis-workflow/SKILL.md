# Hypothesis Workflow

Closed-loop improvement cycle for agent quality. Hypotheses (HYP-NNN) are
proposed changes with measurable expectations; verdicts come from
`reflect-session` runs on session retros.

## When to Use

After spotting a recurring pattern across session retros (≥3 sessions where
the same friction shows up), or after a fix lands and you want to validate it.

## Flow 1: Discover Pattern → Create Hypothesis

### Step 1: Spot the pattern

Skim recent session retros under `.agent/retrospectives/sessions/` and
recent reflect-session outputs under `.agent/retrospectives/reflect-session/`.
Look for the same friction surfacing across multiple sessions.

### Step 2: Create the hypothesis

```bash
ai-hats hyp create "<short statement of expected improvement>" \
  --baseline "<measurable starting state>" \
  --target   "<measurable target after change>" \
  --window   "N sessions"
```

The hypothesis goes to `.agent/hypotheses/HYP-NNN-<slug>.yaml` with
`status: active`. Each subsequent reflect-session run is required to emit
one verdict per active HYP — that is how validation accumulates.

### Step 3: Implement the change

Apply the improvement (rule, skill, code) on a task branch. The hypothesis
itself does not block the change — it just defines what success looks like.

## Flow 2: Validate Hypothesis

Each session-end (when `feedback.session_retro.policy=run`) auto-spawns a
`reflect-session` run that votes on every active HYP. Verdicts append to
`HYP-NNN.yaml` under `validation_log`.

### Manual verdict (optional)

If you want a verdict on a specific session without waiting for auto-runs:

```bash
ai-hats reflect-session --session <SID>
```

### Close the hypothesis

After enough verdicts accumulate (the window declared on creation):

```bash
ai-hats hyp close HYP-NNN --verdict {validated|refuted|inconclusive} \
  --note "<one-line summary>"
```

- **validated**: target reached, the change worked. Document lessons.
- **refuted**: pattern persists or worsened. Roll back or pivot.
- **inconclusive**: not enough signal in the window. Extend window or close.

## Cross-project hypotheses

Before parking a hypothesis as **"untestable from this repo"** or starting a
retirement clock for lack of evidence, **ASK the user**:

> *"Are there sibling projects where this hypothesis is testable?
> If so, please share paths."*

Do NOT auto-survey other directories without confirmation.

## Anti-Patterns

- Applying changes without creating a hypothesis task — no way to track effect
- Closing hypotheses before the validation window completes
- Changing multiple things at once — can't attribute improvement to a specific change
- Treating reflect-session verdicts as ground truth without reading the cited evidence
