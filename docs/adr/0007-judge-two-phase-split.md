# ADR-0007: Judge two-phase split — structural composition replaces runtime mode-switch

## Status

Accepted (HATS-513, 2026-05-26).

## Context

The `judge` role (HYP closure + PROP triage, invoked via `ai-hats reflect all`) carried a runtime mode-switch inside its protocol skill (`judge-protocol` Step 0 — autopilot vs interactive, selected from `AI_HATS_HITL` env or first user-message content). Two architectural smells fell out of that shape:

1. **Asymmetric baseline.** `judge` composed `trait-base + trait-agent + judge-protocol` — bypassing the L0/L1/L2 analyst-tier taxonomy (`base-auditor` / `base-judge`) that every other judge variant uses. `judge-for-role`, for instance, composes `base-judge` and inherits its L1 contract (HITL + ack'd CLI mutations) consistently. The `judge` role had to re-state mode boundaries inline in its injection because no baseline trait carried them.
2. **Mode-switch as inline branch, not composition.** `judge-protocol` Step 0 read the runtime context and chose between two end-to-end flows in the same role. This collapses two roles' worth of behaviour into one prompt, makes the L0 (read-only) contract impossible to enforce structurally (the L1 baseline is what's loaded — read-only is a self-imposed rule the agent can break), and leaks the headless vs HITL distinction into protocol prose instead of pipeline composition.

The pragmatic forcing function was a real workflow shift requested by HATS-499: split context-gathering from supervisor dialogue, so a headless auditor pass produces a draft, and a HITL session discusses + ack's mutations against that draft. The clean shape of that workflow does not survive a runtime mode-switch — every Step in the protocol has to be parameterized on mode, and the L0/L1 contract collapses into convention.

## Decision

Replace the runtime mode-switch with structural pipeline composition. Two roles, two pipeline phases, two baseline traits — one per privilege tier.

### П1 — Tier-symmetric baseline composition

| Role            | Baseline trait    | Tier | Allowed writes                  | Allowed CLI                                |
| --------------- | ----------------- | ---- | ------------------------------- | ------------------------------------------ |
| `judge-auditor` | `base-auditor`    | L0   | single declared report path     | none                                       |
| `judge`         | `base-judge`      | L1   | L0 baseline + ack'd CLI verbs   | `task hyp ...`, `reflect commit`, `task create` |

`judge-auditor` is forbidden by its L0 baseline from invoking `ai-hats` CLI verbs or editing source files — the read-only contract is enforced by composition, not by inline protocol prose. `judge` inherits L1 from `base-judge` symmetrically with `judge-for-role`.

### П2 — Mode-switch becomes pipeline composition

`ai-hats reflect hypothesis` orchestrates two phase-pipelines sequentially:

- **Phase 1**: `reflect-hypothesis-phase1.yaml` — `provider` step with `interactive: false` → `SubAgentRunner` drives `judge-auditor` headless. Output: draft markdown at `<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`. No CLI side-effects.
- **Phase 2**: `reflect-hypothesis-phase2.yaml` — `launch_provider` step with `interactive: true` → `WrapRunner` opens HITL session as `judge`. Supervisor reads the draft, discusses weak spots, ack's mutations; judge executes CLI ops and writes the final report at `<ai_hats_dir>/sessions/retros/judge/<ts>-report.md`.

Mid-pipeline runner-switching in a single YAML was rejected: the `provider` step reads `interactive: bool` from the funnel as one value per run (`src/ai_hats/pipeline/steps/launch.py:88`). Two separate pipelines orchestrated by CLI (same pattern as `finalize-hitl` / `finalize-subagent` from HATS-535) is the minimal-mechanism solution — no new step types, no per-step `interactive` override.

### П3 — Headless mode contract

`ai-hats reflect hypothesis --headless` runs only Phase 1. The contract is **read-only by construction**: `judge-auditor` cannot mutate state through CLI (L0), and the pipeline does not invoke Phase 2. This makes the headless mode safe for CI / cron without a separate "dry-run" flag at the protocol level.

### П4 — Draft → report flow

CLI orchestration is explicit:

1. Phase 1 returns `saved_path` (draft).
2. CLI checks Phase 1 exit code and `saved_path` presence — failure aborts before Phase 2.
3. CLI inlines the draft body into the Phase 2 preamble (no funnel-key plumbing).
4. Phase 2 opens HITL session with draft as first user message; report is the artifact.

Empty draft (only `(none)` sections) is **not** a special case — Phase 2 still opens, supervisor decides nothing-to-do in 1 turn. Special-casing "empty" would hide the "ran but found nothing" outcome.

## Consequences

**New artifacts**
- `library/core/roles/judge-auditor/config.yaml` — L0 role.
- `library/core/skills/judge-auditor-protocol/SKILL.md` — extracted non-mutation parts of `judge-protocol`; replaces CLI-mutation blocks with "record proposed verdict in draft".
- `library/core/pipelines/reflect-hypothesis-phase1.yaml` + `reflect-hypothesis-phase2.yaml`.
- `library/core/initial_injections/reflect-hypothesis.md` + `reflect-hypothesis-interactive.md`.
- `ai-hats reflect hypothesis [--headless] [--dry-run]` CLI command.

**Modified surfaces**
- `library/core/roles/judge/config.yaml` — Mode-A/B paragraphs removed from injection; references draft input.
- `library/core/skills/judge-protocol/SKILL.md` — Step 0 (Mode selection) and Mode-A (autopilot) block removed; Step 1 reads draft from handoff instead of prior report directly.

**Markers**
- `BEGIN_JUDGE_DRAFT` / `END_JUDGE_DRAFT` — new, Phase 1 output.
- `BEGIN_JUDGE` / `END_JUDGE` — unchanged, Phase 2 output (kept for backward-compat with `reflect all`).

**Deprecation**
- `ai-hats reflect all` and `library/core/pipelines/reflect-all.yaml` remain unchanged for one bake cycle. Removal tracked as a follow-up task filed after `reflect hypothesis` ships and parity is confirmed.

**Risks**
- Two-pipeline orchestration moves "this is a single user-facing operation" logic from one YAML into Python CLI code. The CLI is the integration point; the contract between Phase 1 and Phase 2 (draft path + exit code) must be tested explicitly.
- `--headless` becomes the only CI-suitable variant. Operators currently scripting `reflect all` for non-interactive sweeps must migrate when the legacy command is removed.

## References

- HATS-499 — parent task introducing the two-phase workflow request.
- HATS-535 — `launch_provider` split and `finalize-hitl` / `finalize-subagent` precedent for sub-pipeline orchestration.
- HATS-452 / ADR-0005 — composition value contract; П2 (HITL vs Automate hard split) is the axis along which the two phases now live.
- HATS-514 / HYP-014 — symmetric attachment of hygiene rules in `trait-analyst-base`, anchoring the L0/L1 baselines.
- `src/ai_hats/pipeline/steps/launch.py` — `provider` step runner dispatch.
- `src/ai_hats/pipeline/steps/save.py` — `save_artifact` `saved_path` producer key.
