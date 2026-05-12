---
name: self-retrospective
description: Post-work analysis to identify systemic improvements (5 Whys, classify, archive)
---
# Self-Retrospective

Analyze completed or failed work to identify systemic improvements.

## When to Use
- After task completion (especially with failures or backtracks)
- After a failed task (mandatory)
- When backlog-manager transitions to `review` or `failed` state

## Procedure

1. **Facts:** Chronologically list all errors, backtracks, wasted iterations,
   or unexpected findings from the session.

2. **Classify:** Assign each issue to a category:
   - **Knowledge** — missing context or incorrect assumption about the system
   - **Environment** — tooling, permissions, infrastructure issues
   - **Process** — skipped steps, wrong order, missing verification
   - **Communication** — misunderstood requirements, ambiguous instructions
   - **Assumption** — untested belief that turned out wrong

3. **Root Cause (5 Whys):** For each significant issue, ask "Why?" iteratively
   until you reach an actionable root cause. Stop at the level where a fix
   is practical.

4. **Improvements:** Propose specific, concrete changes:
   - Rule or skill updates (with exact content)
   - Workflow changes (with steps)
   - New checks or verification steps
   Focus on **systemic** fixes over one-off patches.

4.5 **Hypothesis candidates:** Surface improvements that describe a
    **behavioural pattern** (the agent systematically does X) — those go
    into the hypothesis backlog, not the task tracker. The supervisor
    chooses what to record.

    **Classify each finding:**
    - **Pattern** — phrased as "<actor> <systematically does/skips/forgets>
      <what>". Reproducible across sessions. → `ai-hats reflect issue`.
    - **Fix** — phrased as "change / add / remove <a concrete artifact>".
      One-shot, doesn't recur in the same form. → task card (step 7).

    **Reformulate** each pattern as a one-line observation in
    **actor + behaviour** form, NOT imperative. Example:
    - ✓ "agent skips supervisor comments left in plan.md during plan
      iteration"
    - ✗ "make agent re-read plan.md before editing" (that's a fix)

    **Present** the candidates to the supervisor as a numbered list:
    ```
    Candidate hypotheses from this retro:
      [1] agent skips supervisor comments left in plan.md during plan iteration
      [2] agent over-elaborates plan before approach confirmation
    ```

    **Ask:** "Record as hypotheses? (all / 1,3 / none)".

    **Act** on the confirmed set — one call per item, in background:
    ```
    ai-hats reflect issue "agent skips supervisor comments..." --bg
    ```

    The intake pipeline will dedup against existing active HYPs and
    either merge as fresh evidence or open a new one. Do NOT call
    `reflect issue` without the supervisor's confirmation — the HYP
    backlog is their source of truth, not an agentic auto-flush.

    Skip this step entirely if no findings classify as patterns.

5. **Quantify:** "7 iterations wasted", "3 failed attempts before pivot" —
   numbers make the impact visible.

6. **Archive:** Save report to `.agent/retrospectives/YYYY-MM-DD-retro-<title>.md`.

   Self-retros are personal agent reflections — free-form markdown is fine.
   They are NOT part of the automated feedback loop (reflect-session handles
   that via `hats-reflect-session/v1`).

7. **Backlog:** For deferred improvements, create task cards via backlog-manager.
   Every identified improvement must either be fixed now or tracked.

## Completion
- Retrospective report saved to `.agent/retrospectives/`
- Every improvement either applied or tracked as a task card
- Quantified impact (wasted iterations, failed attempts)

## Anti-Patterns
- Vague findings ("things could be better") — be specific with numbers and examples
- Only listing problems without root cause analysis — use 5 Whys
- Skipping the backlog step — improvements that aren't tracked will be forgotten
- Conflating a **fix** (concrete change → task card) with a **pattern**
  (behavioural tendency → `reflect issue`). If the wording is "do X",
  it's a fix; if it's "agent does X", it's a pattern.
- Calling `ai-hats reflect issue` without the supervisor's confirmation —
  the hypothesis backlog is their source of truth, not an agentic
  auto-flush. Always present the candidate list and wait.
