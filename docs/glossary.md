# Glossary

Core concepts used across ai-hats code and docs. One short definition per term, plus a pointer to the canonical detail. **Not** a catalog — for the full listing of roles, skills, traits, or CLI commands use `ai-hats list ...` and `ai-hats --tree`.

This file is the naming source-of-truth. When another doc needs to define a core term, link here instead of redefining it.

---

## Provider

A target LLM CLI that ai-hats wraps: `claude` or `gemini`. The choice lives in `ai-hats.yaml` (`provider:`). One role composition produces two injection targets — `CLAUDE.md` or `GEMINI.md` — built during `ai-hats self init` / `self update` (the `self bump` CLI verb was removed in HATS-470; bump runs implicitly inside those flows via `ai_hats._bump_internal`). Switching keeps composition intact: `ai-hats config set -p <provider>`.

Detail — see [1].

## Session

One invocation of the provider CLI under a chosen role. Entry points: `ai-hats` (no subcommand), `ai-hats agent <role>`, `ai-hats execute …`. Per-session artefacts land in `<ai_hats_dir>/sessions/runs/session_<id>/` (`audit.md`, `metrics.json`, `transcript.txt`, `meta_prompt.txt` — the exact bytes the provider saw as system-prompt override, HATS-523). The runtime ends a session with a `session_end` event that may trigger a per-session retrospective — see [Reflect](#reflect).

Lifecycle diagram — see [2].

## Role

A root composition that the agent wears during a session — bundles traits, rules, skills, and an injection block into one config. The shipped library splits into two layers: `library/core/roles/` (engine-internal: `initial-wizard`, `session-reviewer`, `judge`, `judge-for-role`, `auditor-for-role`, `hypothesis-intake`, `test-agent`) and `library/usage/roles/` (curated user-facing: `assistant`, `dev-python`, `maintainer`, `architect`, `sre`, `go-dev`, `go-dev-full`). Catalog — `ai-hats list roles`; layered structure and override precedence — see [9]. Example: [`library/usage/roles/assistant/config.yaml`](../library/usage/roles/assistant/config.yaml). Customization (add / remove / override) — see [6].

Key system roles you will meet in cross-doc prose:

- `initial-wizard` — interactive setup that runs on `ai-hats self init`. See [6].
- `session-reviewer` — per-session retrospective; votes on active HYPs and files a PROP on self-problem. Triggered by `ai-hats reflect session` (auto on `session_end` per policy, or manual). See [5].
- `judge` / `judge-for-role` / `auditor-for-role` — the reflection-loop roles for backlog triage (`ai-hats reflect all`) and role-coherence audits (`ai-hats reflect role`). See [5].

## Trait

An ai-hats-native composition primitive: a reusable bundle (rules + skills + injection text) included by one or more roles. Traits are the unit of cross-role reuse — a fix in one trait reaches every role that pulls it in on the next `ai-hats self init`. Flat model: a trait cannot include another trait. Format: `library/{core,usage}/traits/<name>/config.yaml`. Catalog — `ai-hats list traits`. Composition rules — see [3]; library layout — see [9].

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

- **`ai-hats.yaml`** — project config. Fields: `schema_version`, `migration_step`, `provider`, `active_role`, `default_role`, `task_prefix`, `customizations`, `feedback`, `library_paths`, `venv_path`. `schema_version` describes the yaml format; `migration_step` (HATS-471) is a monotonic counter for one-shot migrations replayed at bump time — each entry runs once per project, then the gate short-circuits. Source of truth for composition. Apply changes by re-running `ai-hats self init` (idempotent) or `ai-hats self update` (which folds bump in); verify with `ai-hats config status`. Full walkthrough — see [6].
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

## Pipeline steps & sub-pipelines

Lock the post-HATS-535 split here. The pre-HATS-535 `launch_provider`
megastep silently hid spawn + audit + lifecycle hooks behind one
pipeline entry; the names below make the lifecycle structurally honest
in YAML and give a stable surface for tests and docs to reference.

- **`provider` step** — `Provider` (file `src/ai_hats/pipeline/steps/launch.py`). Renamed from `launch_provider` in HATS-535. Spawns the configured provider (`WrapRunner` for HITL, `SubAgentRunner` for Automate) and emits flat funnel keys `{session_id, session_dir, transcript_path, exit_code}`. **Does NOT publish `audit.md`** — that step moved to `make_audit`. `LaunchProvider` is retained as a deprecated class alias so externally-loaded YAMLs referencing `id: launch_provider` keep loading.
- **`make_audit` step** — `MakeAudit` (file `src/ai_hats/pipeline/steps/make_audit.py`). Sole post-spawn audit derivation surface; reads claude's per-session JSONL at `~/.claude/projects/<key>/<claude_session_id>.jsonl` (with `_discover_claude_jsonl` mtime fallback) and rewrites `<session_dir>/audit.md` with structured turn markers (👤/👾/🔧/💭). `failure_policy = "continue"` — best-effort; the trace-log fallback in `AuditWriter` keeps audit non-empty even when JSONL is missing. Shared across both HITL and SubAgent paths — closes the pre-HATS-535 asymmetry where SubAgent's `audit.md` was meta-only.
- **`run_session_end` step** — `RunSessionEnd` (file `src/ai_hats/pipeline/steps/run_session_end.py`). Final lifecycle stage of `finalize-hitl`: retro decision + `write_retro_log` + `_spawn_session_reviewer_background` (gated by `HATS_SKIP_RETRO=1` recursion guard) + `SESSION_END` hooks dispatch + cyan retro reminder banner. **HITL-only** — `finalize-subagent` intentionally omits this step to preserve pre-HATS-535 behaviour (no SESSION_END hooks for SubAgent, no auto-retro spawn).
- **`finalize-hitl` sub-pipeline** — `library/core/pipelines/finalize-hitl.yaml`. Steps: `make_audit → run_session_end`. Invoked by `WrapRunner.run` from its `finally` block via `PipelineHarness`-equivalent `pipeline.run(...)`. `claude_session_id` + `hooks_env` pass via `initial=` kwargs (NOT through the main `human` pipeline funnel) so the runner-private state stays out of the public funnel surface.
- **`finalize-subagent` sub-pipeline** — `library/core/pipelines/finalize-subagent.yaml`. Steps: `make_audit` only. Invoked by `_finalize_sub_agent` when `work_dir` + `claude_session_id` are both available (single-turn `_run_attempt` path, multi-turn `_finalize_session_audit` path). Adding `- id: run_session_end` here would extend SESSION_END hooks + retro to SubAgent — currently intentionally absent.

## Session-end output blocks

Two visually similar blocks fire at the end of a [Session](#session). Use these names in code, docs, and conversation — calling both "плашка" or "banner" hides the distinction.

- **Session summary** — the `✨ Session <id> complete!` block with duration, turn count, audit / trace size, retro decision, tokens, and session directory. Always printed; produced by `runtime._print_session_end` from `WrapRunner.run`'s outer `finally` block (HATS-086 SIGINT-safe). Fires AFTER the `finalize-hitl` sub-pipeline so audit size reflects the structured `audit.md` produced by [make_audit](#pipeline-steps--sub-pipelines).
- **Retro reminder banner** — cyan "Reflect through N sessions" lines + optional wrap-up nudge. Pre-HATS-535 inline inside `_print_session_end`; post-HATS-535 emitted by `RunSessionEnd._print_retro_banner` at the tail of `finalize-hitl` (after SESSION_END hooks). Visually trails the Session summary block.
- **Update banner** — a separate three-line block surfaced only when the installed `ai-hats` SHA lags upstream `master`. Format: yellow lead line with `current → latest` short SHAs, cyan `ai-hats self update` command, dim `silence: export AI_HATS_NO_UPDATE_CHECK=1` hint. Produced by the `render_update_banner` pipeline step (`execute.yaml` / `human.yaml`); reads `<ai_hats_dir>/.cache/update-check.json` written by the `check_update_async` step's background probe (24h TTL, stale-while-revalidate).

## Canonical base branch

The name (or names) of the branch that worktrees are expected to be created from and merged back into. Hardcoded to `master` and `main`, in that priority order (HATS-518); the first one that actually exists in the repo is the comparison target.

- **Why it matters.** `WorktreeManager.create()` captures whatever branch the main repo's HEAD currently points at as the worktree's `_original_branch` — and that is the branch `ai-hats wt merge` later lands commits on. Two silent-wrong-branch failure modes:
    - *Create-time.* Operator parks the main repo on a feature branch before `wt create` / `task transition <ID> execute`. The worktree quietly inherits that branch as its merge target; CLI reports "merged" while master never sees the work.
    - *Merge-time.* Even when create-time HEAD was on a canonical base, the main-repo HEAD can wander off `_original_branch` between create and merge — manual `git checkout`, IDE branch-switch, a peer agent operating directly in the main repo without a linked worktree. `_fast_forward_merge` / `_squash_merge` run `git merge` in the main-repo cwd, so the merge lands on whatever branch is currently checked out — not on `_original_branch`.
- **Create-time guard.** `assert_head_is_canonical_base()` in `ai_hats.worktree` refuses both `wt create` and `task transition execute` when HEAD is not on a canonical base (HATS-518) — raises `WorktreeBaseBranchError`. Detached HEAD, non-git directories, and exotic repos that have neither `master` nor `main` are passed through (no canon to compare against).
- **Merge-time guard.** `WorktreeManager.merge()` refuses when `git rev-parse --abbrev-ref HEAD != self._original_branch` (HATS-533) — raises `WorktreeBaseBranchMismatchError` BEFORE any mutation. Positioned ahead of `_check_clean` / `_check_drift`: with HEAD wrong, drift is asking the wrong question. No-op for legacy states where `_original_branch is None`.
- **Recovery (both guards).** `git checkout <expected>` in the main repo, then retry. No work lost in either case, but the surfaces differ:
    - *Create-time refusal* (HATS-518) — no worktree exists yet; the refusal aborts before `git worktree add` runs. Retry creates the worktree fresh once HEAD is on a canonical base.
    - *Merge-time refusal* (HATS-533) — the worktree dir and worktree branch are preserved untouched; the refusal happens before `_check_clean` / `_check_drift` / the actual `git merge`. Retry from the corrected HEAD finishes the merge as if the refusal hadn't happened.
  Both CLI surfaces (direct `wt merge` and `task transition done`) emit a copy-pasteable recipe.
- **`--force` / `--accept-drift` do NOT bypass either guard.** `--force` is the dirty-worktree consent; `--accept-drift` is the moved-base consent. Neither addresses wrong-branch protection — three independent safety contracts, three independent flags.
- **Not configurable.** Hardcoded two-name list, no override flag. If a project needs a different base, raise a ticket with the second use case — until then, KISS / design-minimalism.

## Safe-delete trash bin

Single point of truth for destructive filesystem ops in ai-hats core (HATS-470). Replaces the historical pattern of raw `path.unlink()` / `shutil.rmtree()` / in-place `path.write_text(new)` calls.

- **`ai_hats.safe_delete`** — module exposing `discard(path, *, reason, project_dir)`, `replace(path, new_content, *, reason, project_dir)`, `session_summary()`, `session_root()`, `reset_session()`, plus `TrashFullError`. `discard` moves a file/dir/symlink to the current trash session; `replace` snapshots the old content to trash, then atomic-writes the new bytes. Symlinks are unlinked (link only — target preserved, original target written to a sidecar `.symlink` file).
- **Trash session directory** — `${TMPDIR:-/tmp}/ai-hats/trash-<utc-ts>-<pid>-XXXXXX/`. One per process, lazily created on the first destructive op; `tempfile.mkdtemp` guarantees uniqueness across concurrent ai-hats invocations. Contents mirror the project-relative path of each victim, plus a `MANIFEST.md` recording every op (timestamp, kind, reason, original → trash path). External-to-project paths land under `_external/<abs-tail>/`. Recovery: `cp -r <session>/<rel> <project>/<rel>`. **Not auto-cleaned** — relies on OS `/tmp` retention.
- **`AI_HATS_TRASH_DIR`** — env var. Overrides the trash base directory (the `trash-<ts>-<pid>-XXXXXX/` subdir is still created underneath). Special sentinel `AI_HATS_TRASH_DIR=-` enables **hard-delete mode**: no snapshots, one WARN to stderr per op, no session directory. Intended for CI / ephemeral environments where snapshot value is zero.
- **`TrashFullError`** — `OSError` subclass raised when the snapshot can't land (ENOSPC, read-only filesystem, missing permission). Callers (bump / init) treat it as fatal — partial migrations without a recoverable snapshot violate the trash-bin contract.
- **Inline whitelist marker** — `# safe-delete: ok <reason>` on the same line as a raw `unlink` / `rmtree` / `rmdir`. Use only for genuinely safe cases (empty-dir cleanup, internal `.tmp` from atomic-write, session-cache rebuild, framework-managed manifest). Enforced by the `pre-commit-no-raw-destructive.sh` hook in the `git-mastery` skill — refuses commits that introduce raw destructive ops without either being in `src/ai_hats/safe_delete.py` or carrying the marker. Override: `AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1 git commit ...`.

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
