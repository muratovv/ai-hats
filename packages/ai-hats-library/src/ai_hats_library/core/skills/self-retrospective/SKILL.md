---
name: self-retrospective
description: Post-work analysis to identify systemic improvements (5 Whys, classify, archive). Use when the supervisor asks to "write a retro" / "do a retrospective", at the end of a multi-task work session (wrap-up across several closed tasks), after task completion (especially with failures or backtracks), after a failed task (mandatory), or when backlog-manager transitions to the review or failed state. NOT the automated session-review loop — that is reflect-session.
license: MIT
---

# Self-Retrospective

Analyze completed or failed work to identify systemic improvements.

## When to Use

- When the supervisor explicitly asks to "write a retro" / "do a
  retrospective" — follow THIS protocol (4.5 filter, step-7 backlog gates);
  do not free-form a retro file by pattern-matching an existing one.
- At the end of a multi-task work session (wrap-up across several closed
  tasks) — not only single-task completion.
- After task completion (especially with failures or backtracks)
- After a failed task (mandatory)
- When backlog-manager transitions to `review` or `failed` state

**Not** the automated session-review loop. `reflect-session`
(`hats-reflect-session/v1`) is a pipeline the harness runs to triage
hypotheses/proposals; `self-retrospective` is the personal agent reflection
you run by hand (free-form markdown, archived under `sessions/retros/`). And
**not** a factual outcome record — that is **task-summary**; this skill is the
*improvement* analysis (why the work went as it did, what to change next time).

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

    Run the 5 sub-steps in order. Do not skip ahead — each gate exists
    because skipping it produced a real class of HYP-backlog noise
    (audit of HYP-001..026 found ~50% drop+merge waste, baseline for
    HATS-548).

    #### 4.5.a Filter — drop noise BEFORE formulating

    Apply each criterion. The candidate survives only if ALL hold:

    - **Recurrence or cost:** observed ≥2 times in this session, OR
      cost ≥5 minutes wasted, OR has at least one named adjacent
      flavor (sibling failure mode with the same shape).
    - **Not already covered:** run `ai-hats task hyp list --status
      active --json` and verify no active HYP describes the same
      mechanism. If one does, the finding becomes evidence for that
      HYP via `ai-hats reflect issue` — but presented as a verdict
      contribution, not a new candidate.
    - **Cause is hypothesised, not known:** if you can name the
      mechanism AND the fix with confidence, it belongs in the task
      tracker (step 7), not in HYP — HYP is for behavioural patterns
      whose cause is uncertain enough to need observation across
      sessions.

    A finding that fails any criterion is **not** a HYP. Either drop
    it, route it to step 7 (task card), or attach it as evidence to an
    existing HYP. Single-instance one-off contexts almost always fail
    the recurrence gate — most of the dropped HYPs in the HATS-548
    baseline were single-context (HYP-023, 024, 026).

    #### 4.5.b Root-cause — chain to Step 3, do not restate

    For every surviving candidate, take the 5 Whys output from Step 3
    and walk it down until the answer is a **behaviour mechanism**,
    not a behaviour description. Stop when the next "why?" would name
    an environmental / system constraint outside the agent's control.

    Mechanism = "rule X fires only in context Y because…",
                "agent generalises from prior Z without re-reading…",
                "agent treats each flavor as a one-shot lesson…"
    NOT mechanism = "agent forgot to do X",
                    "agent assumed X" (assumed *why*? keep asking).

    Sanity check: if your CAUSE sentence is the same content as the
    OBSERVATION sentence with different wording, you have not reached
    the mechanism. Go one level deeper. (HYP-023 is the negative
    example: cause text "agent assumes transition requires pre-merge"
    sounded like a mechanism but was actually surface — the HYP was
    refuted because the real mechanism was muscle-memory git-merge
    reflex unrelated to FSM assumptions.)

    #### 4.5.c Generalize — collapse to class

    **Umbrella-first gate — ask this BEFORE you frame anything:**
    do ≥2 surviving candidates share one root failure mode? If yes,
    write **ONE** class-level HYP now that names each context as a
    flavor. Do **not** file N atomic HYPs and leave the supervisor to
    merge them (the HYP-043/044/045 → HYP-046 churn: three filings, all
    stalled, replaced by one umbrella, two wasted CLI round-trips).

    Lay all surviving candidates side-by-side. For any two (or more)
    that share a mechanism, fold them into ONE class-level HYP that
    names both contexts as flavors. Reference HYP-022 as the model:
    one HYP, four named flavors of the same shell-quoting class.

    Also re-scan active HYPs for class-level overlap — if your new
    class-HYP is a generalisation of an existing narrow HYP, propose
    superseding the narrow one rather than adding a third HYP.

    #### 4.5.d Frame — present with concrete trace, not a one-liner

    For each surviving candidate, write a block in this shape:

    ```
    [N] OBSERVATION  (class-level: the behavioural pattern, NOT the instance)
        <one line naming the pattern the agent systematically exhibits>

        WHAT WENT WRONG (concrete trace, 3-7 lines)
        - <what the agent did, with specific commands / files / cites>
        - <what was expected instead>
        - <how it surfaced: error message, supervisor pushback, lost time>
        - <if class-level: name 2+ flavors observed or named>

        HYPOTHESISED CAUSE  (class-level mechanism)
        <mechanism from 4.5.b — one or two sentences>

        SCOPE
        single | class (list adjacent flavors)

        WHY RECORD
        <what evidence the observation window collects;
         what changes if confirmed vs refuted>
    ```

    **Class-level framing rule.** The OBSERVATION (and the HYP title you
    derive from it) and the HYPOTHESISED CAUSE must read as a *class* —
    the pattern and its mechanism — never as the single trace you saw.
    The concrete instance (this session's commands, files, the one
    symptom) belongs ONLY in WHAT WENT WRONG. Litmus: if the OBSERVATION
    line can be rewritten as "in <session/turn> the agent did <X>", it is
    still instance-level — lift it one level before presenting.

    - ✗ instance: «агент сообщил "zero blast radius" до конца прогона»
    - ✓ class: «агент формулирует влияние изменения из guess, а не из
      evidence» — the "zero blast radius" trace moves into WHAT WENT WRONG

    One-liner candidate lists are forbidden — the supervisor needs the
    trace to decide keep/drop without re-reading the session log.

    #### 4.5.e Confirm — supervisor decides, then act

    Present the framed blocks. Ask: "Record as hypotheses? (all /
    1,3 / none / discuss)". On confirmation, call `ai-hats reflect
    issue` (one per item, `--bg` ok). Do NOT call before confirmation
    — the HYP backlog is the supervisor's source of truth, not an
    agentic auto-flush.

    Skip 4.5 entirely if no findings survive 4.5.a.

5. **Quantify:** "7 iterations wasted", "3 failed attempts before pivot" —
   numbers make the impact visible.

6. **Archive:** Save report to `<ai_hats_dir>/sessions/retros/YYYY-MM-DD-retro-<title>.md`.

   Self-retros are personal agent reflections — free-form markdown is fine.
   They are NOT part of the automated feedback loop (reflect-session handles
   that via `hats-reflect-session/v1`).

7. **Backlog:** For deferred improvements, create task cards via backlog-manager.
   Every identified improvement must either be fixed now or tracked.

## Completion

- Retrospective report saved to `<ai_hats_dir>/sessions/retros/`
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
- **Skipping the 4.5.a filter** and dumping every candidate at the
  supervisor — when the agent retracts after pushback ("на самом деле
  это шум"), the candidate failed the filter and should never have been
  presented. Run the filter first.
- **Restating the observation as the cause** in 4.5.b — if your CAUSE
  sentence paraphrases the OBSERVATION, you stopped one "why?" short.
- **One-liner candidate presentation** — Frame (4.5.d) demands a
  concrete trace. A line like "agent omits paths in tracker references"
  forces the supervisor to reconstruct context; the trace block makes
  the keep/drop call obvious.
- **Filing N narrow HYPs that share a mechanism** — collapse to one
  class HYP at 4.5.c (umbrella-first gate). HYP-022 is the model (one
  HYP, four flavors); HYP-043/044/045 → HYP-046 is the anti-model.
- **Instance-level OBSERVATION / CAUSE** — framing the candidate as the
  single trace you saw ("agent said 'zero blast radius' before the run
  finished") instead of the class ("agent states change-impact from
  guess, not evidence"). The instance belongs in WHAT WENT WRONG; the
  OBSERVATION and HYPOTHESISED CAUSE must be class-level (4.5.d).
