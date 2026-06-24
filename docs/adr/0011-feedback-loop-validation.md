# ADR-0011: Feedback-loop validation — how we accept behavioral-library changes

## Status

Proposed (HATS-805, 2026-06-23). Spawned from HATS-803 (ponytail разбор) / HATS-804 (honesty-boundary A/B).

## Context

- The behavioral library (skills, rules, roles, injections) changes constantly under HATS-499.
- Validation was **observation-only**: file a HYP + `verification_protocol`, ship, let `reflect-session` accumulate verdicts over an observation window. Post-ship, passive, slow, confounded by real-session noise.
- HATS-803/804 exercised a second mode — a **pre-ship A/B experiment** (compose control vs treatment roles, run baited stimuli through a blind judge, decide against pre-registered thresholds). It rejected two plausible changes cheaply (ponytail #2 + #3) before they shipped.
- Both loops produce the same currency — a HYP verdict. This ADR fixes *when each applies*.

## Decision

Two complementary feedback loops, one routing gate.

```text
        +-------------------------------------------+
        |  Proposed library change                  |
        |  (skill / rule / role / injection)        |
        +---------------------+---------------------+
                              |
                              v
        +-------------------------------------------+
        |  GATE: worth empirical proof?             |
        |  observable . contested .                 |
        |  cheap reasoning won't settle it          |
        +----+----------------+----------------+----+
             |                |                |
        no:  |          yes:  |          yes:  |
       trivial          testable        only prod
             |        before ship       reveals it
             v                v                v
     +-------------+  +---------------+  +---------------+
     | Ship at     |  |  LOOP A       |  |  LOOP B       |
     | lowest rung |  |  A/B exprmnt  |  |  observation  |
     | design-min  |  |  (pre-ship)   |  |  (post-ship)  |
     +-------------+  +-------+-------+  +-------+-------+
                              |                  |
                  rates vs    |        hyp       |
                  thresholds  |        append-verdict
                              +--------+---------+
                                       v
                     +----------------------------------+
                     |  VERDICT: ship / null / rewrite  |
                     +----------------+-----------------+
                             pass     |    fail / null
                               +------+------+
                               v             v
                         +----------+   +----------+
                         | Accept   |   | Revert   |
                         | (keep)   |   | (drop)   |
                         +----------+   +----------+

  chain : Accept -> ship -> LOOP B confirms it in production.
  watch : ship-at-lowest-rung may also feed LOOP B (optional).
```

### Gate

Experiment only when the change is **observable**, **contested**, and **cheap reasoning won't settle it**. Trivial/obvious edits ship at the lowest rung (`design-minimalism`), no experiment. One A/B ≈ 600k tokens — gate hard, or the framework becomes the over-engineering it guards against.

### Loop A — A/B experiment (pre-ship)

```text
   +----------------------------------------------+
   | 1. Pre-register                              |
   |    hypothesis . DVs . decision thresholds    |
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 2. Compose 2 roles via engine                |
   |    control   vs   treatment                  |
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 3. Positive control                          |
   |    base in both . delta only in treatment    |   (fail -> back to 2)
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 4. Run baited stimuli x reps                 |
   |    + scalpel condition (must-not-break case) |
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 5. Blind judge (arm hidden)                  |
   |    rubric -> structured verdict              |
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 6. Aggregate vs thresholds                   |
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 7. Verify-the-null                           |
   |    read raw outputs, not just the aggregate  |   (artifact -> back to 5)
   +---------------------+------------------------+
                         v
   +----------------------------------------------+
   | 8. Verdict: ship / null / rewrite            |
   +----------------------------------------------+
```

Load-bearing disciplines (each earned in HATS-804):

- **Positive control** — assert treatment actually differs and composes cleanly.
- **Baited stimuli + scalpel condition** — tasks that elicit the behavior, plus a must-not-break case to catch over-correction.
- **Blind judge** — arm hidden; rubric → structured verdict.
- **Replication** — rates, not anecdotes.
- **Pre-registered thresholds** — ship / null / rewrite decided *before* running.
- **Verify-the-null** — read raw outputs; a 0-rate can be a judge artifact.
- **Willingness to return NULL** + **weakest-model rule** — behavior varies by model; test the weakest you deploy.

### Loop B — observation (post-ship)

The existing HYP flow — internals in [`auto-reflect-session`](../assets/diagrams/auto-reflect-session.d2) and [`hypothesis-closure-flow`](../assets/diagrams/hypothesis-closure-flow.d2). Ship at low rung → real sessions → `reflect-session` accumulates verdicts → `hyp append-verdict` over the window → `confirmed | refuted | stalled`.

### How they compose

A/B is the fast pre-filter; observation is the durable ground truth. A change can chain: A/B pass → ship → observation confirms in prod. Both terminate in a HYP verdict — A/B writes it pre-ship, observation over a window.

## Consequences

- A/B: fast, controlled, **expensive**, synthetic-stimulus bias → use it to **reject cheaply**.
- Observation: cheap, real, **slow**, confounded → use it to **confirm durably**.
- NULL is the expected common outcome for contested changes (2/2 ponytail borrowings died). The loops exist to say *no* with evidence, not to bless.
- Artifacts: this ADR with inline ASCII diagrams (no external/d2 dependency). The v1 method **skill** + reusable **workflow template** are deferred (HATS-804/805 follow-up) — this ADR fixes the *decision*, not the tooling.
- No new CLI / engine surface.

## Related

- HATS-803 (ponytail разбор), HATS-804 (honesty-boundary A/B — worked example), HATS-805 (this ADR).
- `library-change-hypothesis-protocol` — files the HYP + `verification_protocol` that Loop A executes and Loop B fills.
- `design-minimalism` (behavioral-delivery ladder), `tool-evaluation-protocol` (validate-before-adopt, for external deps), `reflect-session` (Loop B engine).
- ADR-0009 (quorum autoclose) — verdict-direction safety in the HYP FSM.
