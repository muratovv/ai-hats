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

A root composition that the agent wears during a session — bundles traits, rules, skills, and an injection block into one config. Catalog — `ai-hats list roles`; base configs live in `src/ai_hats/libraries/roles/` (e.g. [`assistant/config.yaml`](../src/ai_hats/libraries/roles/assistant/config.yaml) — a universal Python-leaning agent). Customization (add / remove / override) — see [6] (HATS-355, not yet written).

## Trait

An ai-hats-native composition primitive: a reusable bundle (rules + skills + injection text) included by one or more roles. Traits are the unit of cross-role reuse — a fix in one trait reaches every role that pulls it in on the next `ai-hats self bump`. Flat model: a trait cannot include another trait. Format: `traits/<name>/config.yaml`. Catalog — `ai-hats list traits`. Composition rules — see [3].

## Rule, Skill

The two component kinds that ai-hats injects into the **provider** prompt (`CLAUDE.md` / `GEMINI.md`). They apply at the provider layer — the LLM reads them and follows; ai-hats does not interpret their content.

| Component | What it is                                                  | Format                                                                  |
| --------- | ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Rule**  | Behavioural constraint (do / don't). No decision logic.     | `rules/<name>/rule.md` + `metadata.yaml`                                |
| **Skill** | Procedure, checklist, or protocol with steps and branching. | `skills/<name>/SKILL.md` (+ `metadata.yaml`, `scripts/`, `references/`) |

Catalog — `ai-hats list {rules,skills}`. Formats — see [3].

## Backlog

Three kinds of cards with strict state machines. **All operations go through the `ai-hats task …` CLI** — direct access to `<ai_hats_dir>/tracker/**` is forbidden.

| Kind                 | ID         | Lifecycle                                                                                          |
| -------------------- | ---------- | -------------------------------------------------------------------------------------------------- |
| **Task**             | `HATS-NNN` | `brainstorm → plan → execute → document → review → done` (plus `blocked` / `failed` / `cancelled`) |
| **HYP** (hypothesis) | `HYP-NNN`  | `active → confirmed` / `refuted` / `stalled` — accumulates verdicts in `validation_log`            |
| **PROP** (proposal)  | `PROP-NNN` | `open → accepted` / `rejected` / `deferred` / `duplicate`                                          |

State-machine diagrams — see [4]. Day-to-day workflow — see [7] (HATS-358, not yet written).

## Reflect

The feedback loop that turns session evidence plus active HYP / open PROP into actionable items. Three entry points:

- **`ai-hats reflect session`** — per-session retrospective under the `session-reviewer` role. Votes on every active HYP it can test, files a PROP on self-problem. Runs automatically after `session_end` (policy `always` / `smart`) or on demand.
- **`ai-hats reflect all`** — bulk triage under the `judge` role: closes HYPs and decides PROPs in the inbox.
- **`ai-hats reflect role <target>`** — coherence audit of a role composition (`auditor-for-role` autopilot pass and / or interactive `judge-for-role`).

Practical recipes — see [5]. Pipeline architecture — see [8].

## Worktree

An isolated git-worktree clone where an agent works without touching the mainline. CLI: `ai-hats wt create | exec | merge | discard | env`. **All task execution flows happen in their own worktree** — `ai-hats agent <role>` and `ai-hats task transition <id> execute` both spawn one automatically. The mainline stays clean; results merge back explicitly via `ai-hats wt merge`.

## Artifacts

What ai-hats persists on disk during normal use.

- **`ai-hats.yaml`** — project config. Fields: `schema_version`, `provider`, `active_role`, `default_role`, `task_prefix`, `customizations`, `feedback`, `library_paths`, `venv_path`. Source of truth for composition. Apply changes with `ai-hats self bump`; verify with `ai-hats config status`. Full walkthrough — see [6] (HATS-355, not yet written).
- **SessionReview** — `<ai_hats_dir>/sessions/retros/sessions/<id>.md`. Output of `session-reviewer`: `summary`, `observations`, `hypothesis_verdicts`, `proposal_actions`. Schema `hats-session-review/v1`. Consumed by the next reflect cycle. Detail — see [5].
- **JudgeReport** — `<ai_hats_dir>/sessions/retros/judge/<UTC-ts>-report.md`. Output of `ai-hats reflect all` — HYP closures plus PROP decisions for one triage session. Detail — see [5].
- **RoleCoherenceReport** — `<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ts>-<target>.md`. Output of `ai-hats reflect role` — findings on internal contradictions in a role composition. Detail — see [8].

---

## References

**[1]** — [`docs/ARCHITECTURE.md#providers`](ARCHITECTURE.md#providers) — provider model and injection targets.

**[2]** — [`docs/ARCHITECTURE.md#session-lifecycle`](ARCHITECTURE.md#session-lifecycle) — session lifecycle diagram, where `<id>` comes from.

**[3]** — [`docs/ARCHITECTURE.md#component-model`](ARCHITECTURE.md#component-model) — component formats, composition rules.

**[4]** — [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — task / HYP / PROP lifecycle diagrams.

**[5]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — reflect-session and reflect-all in practice; policy setup.

**[6]** — `docs/how-to-configure.md` — full configuration walkthrough (provider, role, customizations, feedback policy, venv). *Not yet written — tracked under HATS-355.*

**[7]** — `docs/how-to-backlog.md` — task / HYP / PROP day-to-day workflow. *Not yet written — tracked under HATS-358.*

**[8]** — [`docs/reflect.md`](reflect.md) — retrospective pipeline architecture and schema dispatch.

[1]: ARCHITECTURE.md#providers
[2]: ARCHITECTURE.md#session-lifecycle
[3]: ARCHITECTURE.md#component-model
[4]: ARCHITECTURE.md#backlog-state-machines
[5]: how-to-feedback-loop.md
[8]: reflect.md
