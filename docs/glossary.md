# Glossary

Core concepts used across ai-hats code and docs. One short definition per term, plus a pointer to the canonical detail. **Not** a catalog — for the full listing of roles, skills, traits, or CLI commands use `ai-hats list ...` and `ai-hats --tree`.

This file is the naming source-of-truth. When another doc needs to define a core term, link here instead of redefining it.

---

## Provider

A target LLM CLI that ai-hats wraps: `claude` or `gemini`. The choice lives in `ai-hats.yaml` (`provider:`). One role composition produces two injection targets — `CLAUDE.md` or `GEMINI.md` — built by `ai-hats self bump`. Switching keeps composition intact: `ai-hats config set -p <provider>`.

Detail — see [1].

## Session

One invocation of the provider CLI under a chosen role. Entry points: `ai-hats` (no subcommand), `ai-hats agent <role>`, `ai-hats execute …`. Per-session artefacts land in `<ai_hats_dir>/sessions/runs/session_<id>/` (`audit.md`, `metrics.json`, transcript). The runtime ends a session with a `session_end` event that may trigger a per-session retrospective — see [Reflect](#reflect).

Lifecycle diagram — see [2].

## Role

A root composition that the agent wears during a session — bundles traits, rules, skills, and an injection block into one config. The shipped library splits into two layers: `library/core/roles/` (engine-internal: `initial-wizard`, `session-reviewer`, `judge`, `judge-for-role`, `auditor-for-role`, `hypothesis-intake`, `test-agent`) and `library/usage/roles/` (curated user-facing: `assistant`, `dev-python`, `maintainer`, `architect`, `sre`, `go-dev`, `go-dev-full`). Catalog — `ai-hats list roles`; layered structure and override precedence — see [9]. Example: [`library/usage/roles/assistant/config.yaml`](../library/usage/roles/assistant/config.yaml). Customization (add / remove / override) — see [6].

Key system roles you will meet in cross-doc prose:

- `initial-wizard` — interactive setup that runs on `ai-hats self init`. See [6].
- `session-reviewer` — per-session retrospective; votes on active HYPs and files a PROP on self-problem. Triggered by `ai-hats reflect session` (auto on `session_end` per policy, or manual). See [5].
- `judge` / `judge-for-role` / `auditor-for-role` — the reflection-loop roles for backlog triage (`ai-hats reflect all`) and role-coherence audits (`ai-hats reflect role`). See [5].

## Trait

An ai-hats-native composition primitive: a reusable bundle (rules + skills + injection text) included by one or more roles. Traits are the unit of cross-role reuse — a fix in one trait reaches every role that pulls it in on the next `ai-hats self bump`. Flat model: a trait cannot include another trait. Format: `library/{core,usage}/traits/<name>/config.yaml`. Catalog — `ai-hats list traits`. Composition rules — see [3]; library layout — see [9].

Key system traits every role inherits transitively:

- `trait-base` — minimum behaviour for every role: core principles (safety > integrity > convenience > velocity), pessimistic verification, brevity, least astonishment.
- `trait-agent` — agent-mode loop primitives: backlog state machine, delegation pattern, memory hygiene (context-reset / handoff / summary), anti-anchoring, tool-call hygiene.

## Rule, Skill

The two component kinds that ai-hats injects into the **provider** prompt (`CLAUDE.md` / `GEMINI.md`). They apply at the provider layer — the LLM reads them and follows; ai-hats does not interpret their content.

| Component | What it is                                                  | Format (under `library/{core,usage}/…`)                                 |
| --------- | ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Rule**  | Behavioural constraint (do / don't). No decision logic.     | `rules/<name>/rule.md` + `metadata.yaml`                                |
| **Skill** | Procedure, checklist, or protocol with steps and branching. | `skills/<name>/SKILL.md` (+ `metadata.yaml`, `scripts/`, `references/`) |

Catalog — `ai-hats list {rules,skills}`. Formats — see [3]; library layout and override precedence — see [9].

## Backlog

Three kinds of cards with strict state machines. **All operations go through the `ai-hats task …` CLI** — direct access to `<ai_hats_dir>/tracker/**` is forbidden.

| Kind                 | ID         | Lifecycle                                                                                          |
| -------------------- | ---------- | -------------------------------------------------------------------------------------------------- |
| **Task**             | `HATS-NNN` | `brainstorm → plan → execute → document → review → done` (plus `blocked` / `failed` / `cancelled`) |
| **HYP** (hypothesis) | `HYP-NNN`  | `active → confirmed` / `refuted` / `stalled` — accumulates verdicts in `validation_log`            |
| **PROP** (proposal)  | `PROP-NNN` | `open → accepted` / `rejected` / `deferred` / `duplicate`                                          |

State-machine diagrams — see [4]. Day-to-day workflow — see [7] (HATS-358, not yet written).

## Attachment

A file attached to a Task via `ai-hats task attach add`. Blob lives in
`<ai_hats_dir>/tracker/backlog/tasks/<ID>/attachments/<name>`; the manifest
entry — `name`, `digest` (12-char SHA-256 prefix), `added`, `note` — is stored
in `task.yaml::attachments[]`. A pre-commit hook (HATS-402) refuses commits
that add or modify files under `attachments/` without a corresponding
manifest entry; the only legal path is the CLI.

## Reflect

The feedback loop that turns session evidence plus active HYP / open PROP into actionable items. CLI subcommand ↔ spawned role:

| CLI subcommand | Spawned role | Mode | Purpose |
| --- | --- | --- | --- |
| `ai-hats reflect session` | `session-reviewer` | non-interactive | Per-session retrospective: HYP verdicts + PROP-on-self-problem. Auto on `session_end` (policy `always` / `smart`) or on demand. |
| `ai-hats reflect all` | `judge` | interactive (HITL) | Bulk triage: close HYPs and decide PROPs in the inbox. |
| `ai-hats reflect role <target>` | `auditor-for-role` then `judge-for-role` | autopilot + optional HITL | Coherence audit of a single role: autopilot pass first, then interactive review. |
| `ai-hats reflect roles` | `judge-for-role` * | per-role HITL | Bulk role audit — spawns one session per project role. |
| `ai-hats reflect issue` | (no role) | non-interactive | Log a supervisor observation as a new HYP, or merge into an active one. |

**Naming note:** `auditor-for-role` and `judge-for-role` are distinct — `auditor-for-role` is the non-interactive coherence pass; `judge-for-role` is the interactive review. They co-exist; one does not replace the other. `hypothesis-intake` exists for Haiku-class observation classification but is **not** wired into `reflect *` directly.

Practical recipes — see [5]. Pipeline architecture — see [8].

## Artifacts

What ai-hats persists on disk during normal use.

- **`ai-hats.yaml`** — project config. Fields: `schema_version`, `provider`, `active_role`, `default_role`, `task_prefix`, `customizations`, `feedback`, `library_paths`, `venv_path`. Source of truth for composition. Apply changes with `ai-hats self bump`; verify with `ai-hats config status`. Full walkthrough — see [6].
- **SessionReview** — `<ai_hats_dir>/sessions/retros/sessions/<id>.md`. Output of `session-reviewer`: `summary`, `observations`, `hypothesis_verdicts`, `proposal_actions`. Schema `hats-session-review/v1`. Consumed by the next reflect cycle. Detail — see [5].
- **JudgeReport** — `<ai_hats_dir>/sessions/retros/judge/<UTC-ts>-report.md`. Output of `ai-hats reflect all` — HYP closures plus PROP decisions for one triage session. Detail — see [5].
- **RoleCoherenceReport** — `<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ts>-<target>.md`. Output of `ai-hats reflect role` — findings on internal contradictions in a role composition. Detail — see [8].

## Composition & pipeline internals

Names for the framework's composition pipeline + runtime split. Lock these in code, docs, and conversation so the "who owns the prompt" boundary stays unambiguous. Full rationale: [ADR-0005](adr/0005-composition-and-pipeline-value-contract.md).

- **CompositionResult** — the flat, immutable output of `Composer.compose(role, overlays=...)`. `@dataclass(frozen=True)`; modifications via `with_*` methods only (`with_injection_override(text)` returns a new instance). Carries `priorities`, `rules`, `skills`, `hooks`, `injections`, plus provenance maps (`trait_injections`, `role_injection`, `overlay_injection`).
- **Pipeline funnel** — the producer-emits / consumer-may-ignore convention by which pipeline steps thread state. Producer puts a key in the merge delta; consumer may or may not pick it up. **Value contract:** `obj is None` and `key not in ctx` are identical (the framework drops `None` values at the merge boundary); `""` / `0` / `False` / `[]` are valid non-absent values whose semantics differ from "absent". Never use magic `""` to signal "no value" — emit `None` or omit the key.
- **HITL runner** — `WrapRunner`. The human-in-the-loop runner: a user is at the keyboard, the agent runs interactively under a PTY proxy. **Has no `system_prompt_override` channel** — prompt injection is meaningless here. The role's full composition reaches the agent through `composer.compose(...)` + `build_session_prompt` inside `run_session`.
- **Automate runner** — `SubAgentRunner`. The automation runner: subprocess invocation with a required `task` argument. Used for sub-agent fan-out, batch / non-interactive `ai-hats execute`, and pipeline-driven spawns. **Accepts** `system_prompt_override` (HATS-267 use case — caller-supplied prompt replaces the composed injection text).
- **Composition snapshot** — `_composition_snapshot(assembler, role, result) -> dict`. Audit-only structural snapshot emitted into `Session.init_audit`. Lives in a separate channel from data-producing pipeline steps (П4 in ADR-0005) — a producer step does NOT piggyback composition data into its `produces` set for downstream routing.
- **Materialization facade** — `src/ai_hats/materialize.py`. Single derivation point for "compose role X for this project" (HATS-456 / ADR-0005 Phase 2). Exposes `compose_for_role(assembler, role) -> CompositionResult` — a thin wrapper around `assembler.composer.compose(role, overlays=assembler._get_overlays(role))`. Every runtime / pipeline consumer (HITL runner, Automate runner, `MaterializeSystemPrompt` step, `Assembler.set_role` writer, hook-install / status / bump compose-only sites) routes through this function. Direct `composer.compose(..., overlays=...)` outside the facade is a drift signal — pinned by `tests/test_no_direct_compose_outside_facade.py`. The build surface (`build_session_prompt`, `build_system_prompt`, `_build_meta_prompt`) stays runtime-specific per П2 in ADR-0005.

## Session-end output blocks

Two visually similar blocks fire at the end of a [Session](#session). Use these names in code, docs, and conversation — calling both "плашка" or "banner" hides the distinction.

- **Session summary** — the `✨ Session <id> complete!` block with duration, turn count, audit / trace size, retro decision, tokens, and session directory. Always printed; produced by `runtime._print_session_end` inside the `launch_provider` pipeline step.
- **Update banner** — a separate three-line block surfaced only when the installed `ai-hats` SHA lags upstream `master`. Format: yellow lead line with `current → latest` short SHAs, cyan `ai-hats self update` command, dim `silence: export AI_HATS_NO_UPDATE_CHECK=1` hint. Produced by the `render_update_banner` pipeline step (`execute.yaml` / `human.yaml`); reads `<ai_hats_dir>/.cache/update-check.json` written by the `check_update_async` step's background probe (24h TTL, stale-while-revalidate).

---

## References

**[1]** — [`docs/ARCHITECTURE.md#providers`](ARCHITECTURE.md#providers) — provider model and injection targets.

**[2]** — [`docs/ARCHITECTURE.md#session-lifecycle`](ARCHITECTURE.md#session-lifecycle) — session lifecycle diagram, where `<id>` comes from.

**[3]** — [`docs/ARCHITECTURE.md#component-model`](ARCHITECTURE.md#component-model) — component formats, composition rules.

**[4]** — [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — task / HYP / PROP lifecycle diagrams.

**[5]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — reflect-session and reflect-all in practice; policy setup.

**[6]** — [`docs/how-to-configure.md`](how-to-configure.md) — full configuration walkthrough (provider, role, customizations, feedback policy, venv).

**[7]** — [`docs/how-to-backlog.md`](how-to-backlog.md) — `ai-hats task` / `task hyp` / `task proposal` day-to-day workflow.

**[8]** — [`docs/reflect.md`](reflect.md) — retrospective pipeline architecture and schema dispatch.

**[9]** — [`docs/how-to-extend.md`](how-to-extend.md) — shipped library layout (`library/core/` vs `library/usage/`), override precedence, recipes for adding your own roles / traits / rules / skills.
