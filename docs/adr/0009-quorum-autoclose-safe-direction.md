# ADR-0009: Quorum auto-close — deterministic safe-direction mutation outside the judge phases

## Status

Accepted (HATS-769, 2026-06-15).

## Context

The reflect-loop files hypotheses (HYP) continuously — 75 filed — but closes them
almost never (2 terminal verdicts ever). ADR-0007 gates **every** backlog
mutation behind the HITL Phase-2 ack: Phase 1 (`judge-auditor`, L0) is read-only
*by construction*, and only the Phase-2 `judge` (L1) executes CLI mutations after
a supervisor ack. That contract is correct for judgement calls, but it makes the
*consumer* of the HYP backlog rate-limited by supervisor attention. The result is
a producer/consumer imbalance: the backlog grows unbounded because closing even
an obviously-gone hypothesis requires a human in the loop.

Not all closures are judgement calls. A hypothesis that multiple independent
sessions have each marked `refuted` ("behaviour no longer observed") is
*verifiably gone* — closing it needs counting, not judgement. The question is
where to put that auto-mutation without re-opening the hole ADR-0007 closed:
`judge-auditor` is L0 read-only, so auto-close is a **new mutation surface**.

## Decision

Auto-execute **only the safe closure direction** — close-as-gone (`refuted`) —
when an `active` HYP accumulates a **quorum of K independent `refuted` verdicts**
(distinct `session_id` on the `validation_log`; default **K=3**, configurable).
**Never** auto-confirm / auto-accept — those imply downstream action and stay
HITL.

### П1 — Asymmetric-risk rationale

The automation is licensed by an asymmetry, not by certainty:

- A **wrong auto-close** costs one `ai-hats task hyp set-status --status active`
  to undo. Cheap, reversible, visible.
- A **wrong auto-confirm/accept** acts on a phantom — it ships a rule change or
  greenlights work against a hypothesis that was never real. Expensive,
  sometimes irreversible.

So we mechanize only the cheap-to-reverse direction. Three guardrails keep the
asymmetry true: (a) close direction only, (b) every closure is appended to
`validation_log` as a synthetic `auto-quorum` entry naming the contributing
sessions, (c) one-command reversal.

### П2 — Placement: a deterministic step, not the L0 auditor, not a cron

The mutation runs as a deterministic pipeline step (`quorum_autoclose`) at the
tail of `finalize-hitl`, the post-user-session pipeline invoked from
`WrapRunner.run()`'s `finally` block. Rationale:

- **Not inside `judge-auditor`.** Giving the L0 auditor a mutation channel would
  re-break exactly the invariant ADR-0007 established — read-only enforced by
  composition, not by self-imposed prose the agent can break. The quorum check
  is pure arithmetic over `validation_log`; it needs no LLM. Running it as
  deterministic code keeps ADR-0007's judge phases untouched and *reinforces*
  the L0 contract instead of holing it.
- **Not a cron.** The `refuted` verdict that tips a HYP over quorum is appended
  during a HITL session (judge Phase-2 / reflect). That session's `finally` runs
  `finalize-hitl`, so the sweep closes the HYP in the same session that reached
  quorum — tied to activity, no new scheduler surface.
- **`failure_policy = "continue"`** — a sweep error logs to `state["errors"]`
  and session finalization still completes cleanly (mirror of `make_audit` /
  `compute_usage`).

### П3 — Independence and the sentinel

"Independent" means distinct `session_id`. Entries without a `session_id` cannot
establish independence and are not counted. The synthetic closure entry carries
`session_id = "auto-quorum"` and is itself excluded from the count, so a HYP that
is reopened (`set-status active`) does not start pre-loaded with one vote.

## Consequences

**New artifacts**

- `packages/ai-hats-tracker/src/ai_hats_tracker/hypothesis/quorum.py` — pure core (`find_quorum_closures`,
  `apply_closure`, `autoclose_quorum`).
- `src/ai_hats/pipeline/steps/quorum_autoclose.py` — the deterministic step,
  registered in `_BUILTINS`.
- `ai-hats task hyp autoclose [--k N] [--dry-run]` — manual / ops entrypoint
  sharing the same core.

**Modified surfaces**

- `library/core/pipelines/finalize-hitl.yaml` — `quorum_autoclose` appended.

**Scope limits (deliberate)**

- **HYP only.** PROP has no refuted-quorum analog: a `Vote` carries only support
  `reasoning`, and "close-as-gone" for a proposal (e.g. PROP-020 satisfied by
  HATS-676) is a linked-task-done / human signal, not a quorum. PROP auto-close
  is deferred.
- **`observation_window` not enforced.** The field is free-text (`str | None`)
  and not machine-parseable; v1 counts across the whole `validation_log`.
  Windowed counting is a follow-up if false-closes appear in practice.
- **HITL only.** v1 fires on user (HITL) sessions; `finalize-subagent` parity is
  a trivial follow-up if needed.
- **Not reconciled with `min_sessions_per_bundle` / `exit_criteria.refute`** —
  both are dormant (defined, never evaluated). K is an orthogonal new knob; those
  fields are intentionally left untouched.

**Risks**

- A `refuted` verdict means "this session did not observe the behaviour" —
  absence of evidence, not proof of absence. Three independent non-observations
  can in principle still be a flaky miss. Accepted under П1: the close is cheap
  to reverse and fully logged. If false-closes are observed, the first lever is
  raising K or adding `observation_window` enforcement, both already designed
  for.

## References

- ADR-0007 `docs/adr/0007-judge-two-phase-split.md` — the L0/L1 read-only
  contract this carves out from.
- ADR-0005 `docs/adr/0005-composition-and-pipeline-value-contract.md` — the
  None-filtered delta merge (`{}` when nothing closed).
- HATS-751 — backlog-hygiene reframe that surfaced the producer/consumer
  imbalance.
- HATS-770 — session-end pileup banner (the *nudge* lever; this ADR is the *act*
  lever for the safe subset).
