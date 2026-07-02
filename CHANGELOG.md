# Changelog

All notable changes to ai-hats are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions are produced from git tags via `setuptools-scm`; everything
since the latest tag lives under **Unreleased** until the next release.

## [Unreleased]

## [0.12.0] - 2026-07-02

### Added

- **Standalone `ai-hats-core` + `ai-hats-wt` packages** (HATS-885). The atomic
  filesystem-I/O core primitives and the hook-agnostic git-worktree engine are
  extracted into two independently-versioned PyPI packages; `ai-hats` now depends
  on them (`ai-hats-core>=0.1.0`, `ai-hats-wt>=0.1.0`) and `ai-hats self update`
  pulls them transparently. The worktree engine is importable standalone as
  `ai_hats_wt` (`WorktreeManager` + the L1–L4 lock model) against a bare git repo
  with zero ai-hats config.
- **Tool-call-hygiene `PreToolUse` guard** (HATS-632). The `tool-call-hygiene`
  skill now ships a non-blocking `PreToolUse` Bash runtime hook: when a command
  is a pure invocation of `grep`/`find`/`cat`/`sed -i`/… that a dedicated tool
  covers, it injects an `additionalContext` nudge toward Grep/Glob/Read/Edit
  without blocking the command or prompting the user. Conservative by design —
  any pipe / redirect / chained command is left alone. Kill switch:
  `AI_HATS_TOOL_HYGIENE_OFF=1`. This is the first in-library `runtime_hooks`
  consumer, setting the shared `stdin tool_input → JSON hookSpecificOutput`
  convention for the behavior-hook family.
- **Python security-lint `PostToolUse` hook** (HATS-660). A new `py-security-lint`
  skill (composed by the `dev::python` trait) runs `ruff check --isolated --select S`
  (flake8-bandit security rules) on every `.py` you Edit/Write and forwards any
  findings to the agent via a non-blocking `additionalContext` note — an early,
  edit-time security layer that complements (does not replace) the project's CI
  lint. Zero egress, fail-open when `ruff` is absent. Kill switch:
  `AI_HATS_SECURITY_LINT_OFF=1`.

## [0.11.0] - 2026-06-26

Headline: epic **HATS-835 — worktree lifecycle & merge robustness**. A sweep
that hardens the git-worktree lifecycle (create → merge → teardown → tracker
consistency) against the failure modes that silently lost or corrupted state.

### Fixed

- **`task transition done` tolerates an already-merged, state-lost branch**
  (HATS-697). When work shipped on the base out-of-band (manual `git merge
  --no-ff task/<id>`) and/or the auto-worktree was removed by hand, `done`
  refused with a false `worktree state lost` ("un-merged commits") even though
  the branch was fully integrated. It now detects the already-merged branch,
  finalizes without a re-merge, and cleans up the stale ref; only a genuinely
  divergent branch still refuses (the silent-data-loss guard stays intact).
- **Forced `execute` spins no fresh worktree** (HATS-697). `transition execute
  --force` is a manual state correction; it no longer creates a worktree off
  HEAD that orphaned retrospective shipped-on-master work in the main tree.
- **In-worktree `transition done` / `close` is refused before teardown**
  (HATS-788). Running it from inside the task's own linked worktree used to
  delete the cwd and leave the CLI resolving a phantom tracker (false `task
  not found`); it now refuses with guidance and preserves the worktree.
- **No phantom tracker on a wrong-but-alive root** (HATS-839). `<ai_hats_dir>`
  is no longer created unconditionally, which had resurrected a phantom
  `.agent/` tracker and drove the HATS-788 id-collision.
- **Worktree-adopt short-circuit works from inside a worktree** (HATS-840).
  The HATS-060 adopt path no longer no-ops on a hopped `_project_dir`, so it
  adopts the caller's worktree instead of spinning a fresh one off main.
- **Typed refusal for `original_branch: null`** (HATS-714). `wt merge` /
  `task transition done` raise an "incomplete worktree state" error naming the
  field instead of an opaque `TypeError`.
- **`execute --batch` without `--role` fails cleanly** (HATS-827), instead of
  crashing on an invalid `agent//<session>` worktree branch.

### Added

- Capstone e2e matrix `test_worktree_lifecycle_robustness_matrix.py` asserting
  the epic's invariants hold together on the real launcher + binary.

## [0.10.0] - 2026-06-20

### Added

- **Self-location guard + out-of-band recovery + stray-shadow detector**
  (HATS-791, child of HATS-786). Closes the residual "shadow" case HATS-790's
  generator removal left open: a stale ai-hats running from a FOREIGN
  (non-managed) venv reached ahead of the host launcher. A pure classifier
  `ai_hats.self_location.classify_invocation` (`"sanctioned"` / `"foreign"`),
  wired by `_guard_self_location` into `main_entry`, **refuses-and-instructs**
  on a foreign invocation — prints `remediation_text` (run the host launcher /
  re-bootstrap / uninstall from the offending venv) to stderr and exits 3. It
  biases HARD toward fail-open (only a positively-identified foreign venv that
  ACTUALLY EXISTS as a resolvable managed venv is refused; every ambiguity,
  editable dev clone, or `--version`/`--help`/`--tree` info command resolves to
  sanctioned), is wired into `main_entry` (not the `main` click group, so
  in-process `CliRunner` tests bypass it), and has an escape hatch
  `AI_HATS_SKIP_SELF_LOCATION_GUARD=1` (`SKIP_ENV_VAR`). `scripts/bootstrap.sh`
  becomes the canonical **out-of-band recovery** hatch — paradox-immune because
  it is fetched fresh (`curl … | bash`) and drives the launcher by ABSOLUTE path
  (`"$LAUNCHER_DEST"`), so a shadow cannot intercept it — with a new `--repair`
  flag that force-reinstalls the launcher + the framework-managed default venv
  (`.agent/ai-hats/.venv` + `versions/`, never a user override). Both
  `bootstrap.sh` (`detect_stray_launchers`) and `ai_hats.cli.maintenance`
  (`find_stray_launchers`) scan `$PATH` for stray `ai-hats` binaries outside the
  sanctioned launcher and WARN — never delete.
- **Forward-safe `ai-hats.yaml` reader — preserve unknowns, fail loud on a newer
  schema** (HATS-792, child of HATS-786). `ProjectConfig` now round-trips a
  same-version unknown top-level field instead of dropping it: `from_yaml`
  stashes the pre-stripped unknown keys on an `_extra` `PrivateAttr` and
  `to_dict` merges them back (mirrors `TaskCard.extras`), so an OLDER ai-hats
  preserves (does not silently delete on `save()`) a field a NEWER ai-hats wrote
  without a `schema_version` bump — while the HATS-581 stderr WARN still fires.
  A genuinely newer schema fails loud: `from_yaml` raises `ProjectConfigError`
  pointing at `ai-hats self update` when on-disk `schema_version` exceeds
  `KNOWN_SCHEMA_VERSION` (4), and a matching `save()` clobber guard refuses to
  overwrite a file whose on-disk schema is newer than this binary knows.

### Removed

- **Migration:** see [`docs/migration-v0.10.0.md`](docs/migration-v0.10.0.md) for
  the one-time crossover (reinstall the host launcher; clear stray app-venv
  installs). **Removed the `ai-hats` console-script entry point; `python -m
  ai_hats` is now the sole package entry** (HATS-790, Alt 5). The `[project.scripts] ai-hats =
  "ai_hats.cli:main_entry"` generator made every venv depending on `ai-hats`
  materialise a `bin/ai-hats` that direnv could prepend ahead of the host
  launcher (`~/.local/bin/ai-hats`), silently running stale code. With the
  generator gone, no venv produces `bin/ai-hats`; the bash launcher now execs
  `<venv>/bin/python -m ai_hats "$@"` and probes venv health/usability via
  `bin/python` + a `python -c "import ai_hats"` import probe rather than the
  removed console-script proxy. `is_usable_version` / `read_current_sha`
  (`paths.py`) drop the `bin/ai-hats` clause and key on the `.complete` sentinel
  - `bin/python` (behaviour-equivalent for any real install). `python -m ai_hats`
    routes through `main_entry` so `--tree` / `--help --tree` ordering is identical
    to the old console entry. The host launcher remains named `ai-hats` and on
    `$PATH` — only the per-venv generated binary is gone.

## [0.9.0] - 2026-06-17

### Added

- **Release CI to PyPI via OIDC trusted publishing** (HATS-765, child of the
  HATS-762 distribution overhaul). A new `.github/workflows/release.yml` builds
  the wheel + sdist with `uv build` and publishes to PyPI on a `v*` tag push via
  tokenless OIDC trusted publishing — build and publish are split into two jobs
  so the `id-token: write` privilege is held only by a publish-only job. This is
  the artefact that makes the `stable` channel real: end users install a
  prebuilt `ai-hats==<version>` wheel instead of a git source build. A
  self-skipping live e2e (`tests/e2e/test_stable_channel_live.py`) exercises a
  stable-channel `self update` against the real PyPI index (skips until the name
  is published). `docs/RELEASING.md` documents the trusted-publisher one-time
  setup and the post-publish verify step.
- **`ai-hats session show` renders a Usage section from `usage.json`** (HATS-734,
  child of HATS-699 / HATS-698 audit) — the HATS-664 producer (`compute_usage`)
  had zero in-src consumers, so a producer regression (the resume-mode discovery
  bug fixed below) was invisible for months. `session show` now renders a
  fail-soft Usage block (measured/static always-on, `skill_loads`, tool
  success-rate, sidechain, parser flags) and lists `usage.json` among the
  session artefacts, making the channel falsifiable.

### Changed

- **Deleted the dead lifecycle `hooks:` composition channel; re-homed the one
  real consumer** (HATS-707, child of HATS-699 / HATS-698 audit). The
  role/trait `composition.hooks` channel (`CompositionResult.hooks`,
  `HooksConfig`, the `LifecycleEvent` enum, `composer._merge_hooks`, and
  `HooksRunner`) was composed and displayed in `config status` but had **zero**
  runtime execution consumers — `HooksRunner` scanned `library/hooks/` by
  filename convention (empty of lifecycle scripts since HATS-314) and never
  read `result.hooks`; `TASK_*` events never fired. `config status` no longer
  advertises a hook subsystem that never runs. The single piece of real intent —
  the maintainer's `session_start: [ai-hats self sync-hooks]` git-hook drift net
  (HATS-593 layer B), itself silently dead — is re-homed to a direct
  `WrapRunner._resync_git_hooks()` call at session start, for every role
  (idempotent, fail-open). Existing user configs with a `hooks:` block are
  unaffected (`Composition` is `extra="ignore"`; no migration). `RunSessionEnd`
  is now the retro-banner-only finalize step.
- **Claude system prompt no longer carries the `AVAILABLE SKILLS` index**
  (HATS-701, audit F2 of HATS-698, child of HATS-699 — harness optimization).
  `ClaudeProvider.build_system_prompt` appended a skills index built from
  `SKILL.md` frontmatter (5,988 chars for the 22-skill maintainer role) while
  the *same* session already passes the composed skills via `--plugin-dir`
  (HITL) / SDK plugin (sub-agent) — Claude Code natively lists every plugin
  skill with its full description, so the index was a 2-3x duplicate. It is
  now suppressed for Claude (returning ~1.5k tokens to the context window on
  every session and every sub-agent spawn, with less selector noise from the
  duplicate qualified/unqualified listings) and kept for Gemini, which has no
  native skill registry. The two near-identical `build_system_prompt` bodies
  are de-duplicated into `Provider._compose_sections(result, *, include_skills)`;
  `show-prompt` now mirrors the real (index-free) Claude prompt.
- **Trimmed the always-on `## RULES` block ~2.1 KB** (HATS-702, child of
  HATS-699 / HATS-698 audit). The block ships verbatim in every composed
  prompt for every role and consuming project — the one cost nobody can opt
  out of. `rule_pause_before_shared_state_write` (3,005 → 1,542 chars) drops
  the incident-narrative rationale and worked example (→ HYP-026 / HYP-027 /
  PROP-052 pointer) while keeping every behavioral clause, the command/
  reversibility table, and the hook-backstop + ACK warning — enforcement is
  unchanged (`pre_bash_shared_state_guard.sh` is wired unconditionally).
  `rule_composition_value_contract` (1,690 → 1,064 chars) compresses its four
  invariants to one-liners + ADR pointer, and its stale `providers.py` budget
  comment is corrected (`~600` → `~1.0 KB`). Net: block 8,149 → 6,060 chars,
  ~520 fewer tokens on every session and sub-agent.
- **Maintainer role injection deduped against its traits** (HATS-703, child of
  HATS-699 / HATS-698 audit — finding F4). The `maintainer` role injection
  re-described its own traits and restated the `brainstorm→…→done` workflow that
  `trait-agent` already delivers. Dropped the redundant `## Workflow` (covered by
  `trait-agent` Agent Protocol + `trait-base` pessimistic-verification /
  concise-communication) and `## Delegation` (near-verbatim `trait-agent`
  `### Delegation`); moved the author-facing "what sets this role apart"
  meta-section to a YAML comment so it no longer spends agent prompt budget. The
  no-`Co-Authored-By` commit-trailer policy now has a single tracked home — the
  `ai-hats-maintainer` trait — removing the contradictory-precedence risk of the
  former 3-way duplication. Role injection is now header + intro + `## Guardrails`
  (~0.5–1 KB/session saved). The three HATS-452 prompt-content e2e tests re-point
  their role-own-injection marker from `## Workflow` to the role intro string.
- **Skill bodies are no longer eager-loaded on every compose** (HATS-706, child
  of HATS-699 / HATS-698 audit). `Composer.compose` read each skill's full
  `SKILL.md` into `ResolvedComponent.injection` for every session and both
  providers, yet the *only* consumer of a skill's body is `ai-hats reflect`'s
  role-mirror (`_materialize_target_composition`) — the Gemini `AVAILABLE
  SKILLS` index reads its own single copy via `_extract_frontmatter_description`.
  The eager read is removed; reflect now reads the body on demand from the
  skill's `source_path`. Non-reflect sessions no longer pay one `SKILL.md` read
  per skill for a body they never use, and the per-skill double read on Gemini
  prompt builds collapses to one. No change to prompt output or reflect
  artefacts. (The card's other half — hoisting the identical `build_system_prompt`
  into the `Provider` base — was already delivered by HATS-701.)

### Removed

- **`ai-hats self clean` command** (HATS-709, child of HATS-699 / HATS-698
  audit — finding 2a-F3). A total no-op on v4: framework content is composed
  in memory (HATS-294), so the rules/skills mirrors it wiped are empty, the
  legacy `.agent/{skills,hooks}` it swept don't exist, and the `.ai-hats-managed`
  manifest its sweep read was never written (`_write_managed_manifest` had zero
  callers). The only materialized managed content (`library/hooks`) is owned by
  `_refresh`. The undocumented command and its dead helper chain
  (`Assembler._clean` / `_clean_non_local` / `_clean_managed_entries` /
  `_write_managed_manifest` + the unreachable `preserve_local` branch and
  `.library_rules` marker protocol) are removed (~90 LOC). Re-materialize a
  project's managed tree via `ai-hats self init` / `self update`.
  Migration: [`docs/migration-v0.9.0.md`](docs/migration-v0.9.0.md) §4 — the
  command was a no-op; drop any calls and use `self update` / `self init`.
- **Write-only `pipeline_metrics.json` telemetry** (HATS-736, child of
  HATS-699 / HATS-698 audit — dead-delivery class #5). `PipelineHarness.__exit__`
  wrote a per-run `pipeline_metrics.json` (terminal zero-output / timeout
  incident counters) into a namespace GC'd after `AI_HATS_PIPELINE_KEEP_N`
  (default 10) runs, with **zero readers** in `src/` — the data expired
  unread. Folding it into the session `metrics.json` (read by
  `session list --json`) was rejected: the harness has no map from its
  `session_id` to a spawned session dir, and the signal is already
  observable — per-session `timed_out` lives in session `metrics.json` and
  `HarnessReliabilityError` is routed to a meta-PROP by reflect-session. The
  writer, its dead imports, and its 5 unit tests are removed; `__exit__` is
  now a no-op (artefacts are still kept and GC'd at the next `__enter__`).

### Fixed

- **Two real-subprocess e2e files now run in the pre-push gate that protects
  master** (HATS-746, audit 4b-F4 of HATS-698). `tests/e2e/test_wave1_free_tier.py`
  (3 free-tier pilots) and `tests/e2e/test_wt_merge_ambiguity_guard.py` (2 tests —
  the HATS-502 `wt merge` ambiguity foot-gun guard) lacked
  `pytestmark = pytest.mark.integration`, so the gate's
  `-m "(integration or smoke) and not quarantine"` selection **deselected** them;
  they survived only by accident in CI Job 1's `not integration` pool. Adding the
  marker pulls all 5 into the gate (deliberate coverage increase) — a regression
  in the foot-gun guard no longer ships to master silently. Also deleted the
  dead `external_env` pytest marker (declared in `pyproject.toml`, zero uses
  repo-wide), and recorded on HATS-695 that the two quarantined `self update`
  pip tests have zero automated coverage (gate deselects via `quarantine`, CI
  Job 1 via `integration`, CI Job 2 via `--ignore=tests/e2e/`) until that task
  de-flakes and un-quarantines them.
- **Pipeline engine raises a typed `StepError` for a required ctx key absent at
  runtime, instead of a bare `KeyError`** (HATS-739, audit 2c-F8 of HATS-698).
  `_run_steps` projected `requires` (`kwargs = {k: state[k] for k in s.io.requires}`)
  *before* the per-step `try`, so when a producer legally omitted a *declared*
  `produces` key at runtime (None-filtered merge; `ComposeRole` emits `{}` for no
  role — ADR-0005 value contract), the missing-key lookup raised a context-free
  `KeyError` that bypassed both `failure_policy="continue"` and the `_emit` trace
  event. The projection is now non-raising and an explicit presence check raises a
  `StepError` naming the step + missing keys *inside* the `try`, so trace and
  continue-policy semantics apply. Latent (no shipped pipeline pairs an omittable
  produce with a downstream require) but the exact contract seam a custom-YAML
  pipeline author would hit.
- **`compute_usage` no longer skips `usage.json` in resume/continue sessions**
  (HATS-734, audit 2c-F3 of HATS-698). `ComputeUsage.run` passed
  `claude_session_id` — a `uuid4` — to `_discover_claude_jsonl`, which parses
  `session_id[:15]` with `strptime("%Y%m%d-%H%M%S")`; a uuid never parses, so the
  JSONL discovery fallback returned `None` and the step silently exited — in
  exactly the `--resume`/`--continue` scenario the fallback (HATS-272) exists
  for. It now passes the ai-hats `session_id`, converging on the `make_audit`
  sibling. The breakage stayed invisible because `usage.json` had no reader —
  see the matching Added entry.
- **`task transition --final-state` refuses non-review targets and persists
  atomically** (HATS-723, child of HATS-699 / HATS-698 audit) — the flag was
  applied only for the `review` target; on any other target it was parsed and
  silently dropped (option-parsed-then-ignored). It now exits 1 with a clear
  error. Separately, the summary was written in its own lock *before* the
  transition, so a transition that then raised (FSM guard, empty-plan, worktree
  errors) left a half-applied card; `final_state` now rides the transition's
  single lock window. The dead `TaskManager.set_final_state` (now without a
  production caller) was removed.
- **`_pty_spawn` no longer pollutes the parent process environment** (HATS-713,
  child of HATS-699 / HATS-698 audit) — it looped `os.environ[k] = v` over the
  per-session env, permanently leaking keys (`AI_HATS_SESSION_ID`,
  `AI_HATS_ROLE`, provider vars) into the parent. Those stale keys reached the
  finalize pipeline, `SESSION_END` hooks, and any later `WrapRunner.run` /
  in-process test in the same process. The per-session env is now passed to the
  child via `PtyProcess.spawn(..., env={**os.environ, **env})`; the child sees
  the same effective environment, the parent `os.environ` is left untouched.
- **`wt merge` no longer hangs forever on an unreachable remote** (HATS-711,
  audit 2a-F5 of HATS-698). `WorktreeManager._check_drift` runs a pre-merge
  `git fetch origin <base>` — a network call — while holding the per-branch
  lifecycle lock, but `_git` had no timeout. A hung fetch (dead VPN / DNS
  blackhole) wedged `merge()` unboundedly; concurrent `wt merge` / `wt discard`
  peers then timed out at the 60s lifecycle lock and failed with a
  `WorktreeLockError` blaming a phantom "concurrent `wt merge`/`wt discard`".
  The fetch is now bounded by `FETCH_TIMEOUT` (30s); a timeout is treated like
  the existing fetch-failure path (WARN + local-only drift check, merge
  proceeds) and named explicitly so triage starts at the network, not at
  phantom concurrency. Other (local) `_git` calls are unchanged.

## [0.8.0] - 2026-06-07

### Added

- **`compute_usage` step + `usage.json` per-session context-cost report**
  (HATS-664, first child of HATS-663 session-observability epic) — a transcript-
  first parser that turns one Claude Code JSONL session into a machine-readable
  `usage/v1` report: measured always-on budget (first `cache_creation` proxy), an
  ordered event timeline (skill-body loads via `Skill` tool_use, reference Reads
  of `*/references/*.md` + `SKILL.md`, tool calls with `is_error`, stop-hook
  firings), aggregates with tool success-rate, and sub-agent sidechain linkage
  (detect + link by `sessionId`/`sourceToolAssistantUUID`, no per-event token
  merge). The report also self-describes its ai-hats context — `role` /
  `provider` / `exit_code` copied from the session's `metrics.json` (so the
  comparison sibling pairs sessions by role and "what went wrong" debugging reads
  it in one place); when `role` resolves, a static `costs.py` per-component
  always-on breakdown is attached under `always_on.static` for a measured-vs-
  static cross-check. The pure `parse_session_usage` (`src/ai_hats/usage.py`) is
  transcript-only and fail-soft (malformed line / unknown entry type → `flags`,
  never a crash — verified over all ~550 historical transcripts with zero
  crashes) and
  doubles as a bash-composable primitive (`python -m ai_hats.usage <jsonl>`,
  JSON to stdout) for retroactive sweeps. The `ComputeUsage` step is the thin
  live driver — sibling of `make_audit`, same post-session JSONL, wired right
  after it in both `finalize-hitl` and `finalize-subagent` — so every new
  session writes `<session_dir>/usage.json` alongside `audit.md`/`metrics.json`.
  Reproduces the HATS-578 finding automatically (skill-BODY loads are rare —
  ~20% of sessions; `backlog-manager` + `self-retrospective` dominate). Per-event
  token attribution is a documented `reconstructed` heuristic (per-message usage
  is a per-turn total); unattributable events keep `tokens_delta = null`, never a
  magic `0` (honors `rule_composition_value_contract §3`).
- **`devils-advocate` skill + conditional "Approach & counter" plan section**
  (HATS-621, M3 of HATS-629) — the value-counter stage of the plan-gate. A new
  `required=False` `Approach & counter` section sits between `Requirements` and
  `Scope & Out-of-scope` (`PLAN_SECTIONS`); the engine never blocks `execute` on
  it (the "non-trivial plans fill it or write explicit `N/A`" norm is behavioural,
  carried by the skill + companion HYP). The `devils-advocate` skill ships the
  4-step skeptic method — steelman the value → name the unstated assumption →
  counter it (*needed? missed anything? another way?*) → assess impact — and is
  wired into `trait-agent`. `plan-gate` documents the
  `requirements-interview ⇄ devils-advocate → design-minimalism` flow, with
  cross-refs in both sibling stages. Catches "right scope, wrong direction" — the
  failure mode neither `requirements-interview` (WHAT) nor `design-minimalism`
  (HOW MUCH) catches.
- **`plan-discipline` skill** (HATS-643) — the named discipline for the plan-home
  invariant: a plan is always a task, authored directly into the canonical
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`, and never routed through
  `.claude/plans` (inert plan-mode scratch ≠ the plan). Carries the draft→tracker
  transfer procedure and hands off to `plan-gate` for section filling; the engine
  per-section gate (HATS-635) remains the enforcement backstop. Wired into
  `trait-agent`. Closes the plan-mode→`.claude/plans` salvage loophole left after
  HATS-637 at the discipline layer. `backlog-manager` and `rule_backlog_discipline`
  now point here instead of duplicating the flow. Covers the Claude Code plan-mode
  two-phase reality (HATS-644): plan mode is read-only, so the `.claude/plans`
  draft is expected Phase-1 scratch and the mandatory first post-approval action is
  to transfer it into the tracker `plan.md`; when plan mode isn't forced, plan
  directly in the tracker.
- **`ai-hats task hyp create --verification-protocol TEXT`** (HATS-623).
  `library-change-hypothesis-protocol` mandates a `verification_protocol`
  field on companion HYPs, but `hyp create` exposed no flag and
  `rule_backlog_discipline` forbids editing `hypotheses/*.yaml` directly —
  so HATS-616 had to fold the protocol text into `--success-criterion`. The
  `Hypothesis` model is already `extra="allow"` and persists via
  `model_dump(exclude_none=True)`, so the field round-trips with no model or
  storage change; the flag is dropped from the YAML when omitted. Consumed
  by `reflect`/session-reviewer handoff (HATS-534).
- **`dev-web` role — web/frontend development (JS/TS + React)** (HATS-616).
  Fills the one real library gap from the awesome-claude-skills review (no
  web role; Go had 40+ skills). Shape mirrors `dev-python` + `dev::python`:
  a single role `dev-web` over one gear `dev::web` (not a `go-dev`-style
  multi-gear split). The gear carries JS/TS + React + a11y + tooling
  conventions and bundles two seed skills: `ui-ux-review` (two-mode —
  guide + P0/P1/P2 review — cognitive UX rules, distilled from
  oil-oil/oiloil-ui-ux-guide [Apache-2.0] and wondelai/skills [MIT]) and
  `webapp-testing` (Playwright recon→act→assert browser verification,
  distilled from anthropics/skills [Apache-2.0]). `task_complete` gates:
  `npm run lint/test/build` + `npx playwright test`. Companion HYP-056
  tracks the expected behavior shift. Per-source licenses + attribution
  recorded in each skill's `metadata.yaml` `upstream:` block.
- **Skills can declare provider runtime hooks** (`runtime_hooks:` in a
  skill's `metadata.yaml`, HATS-597 / HATS-601). Mirrors the `git_hooks`
  open registry: a composed skill declares hooks keyed by Claude event
  (v1: `PreToolUse`, `PostToolUse`), each row `{matcher, script}`. On
  `self init` / `self update` the assembler materializes each declared
  script to `<ai_hats_dir>/library/hooks/<skill>-<basename>.sh` (`0o755`,
  manifest-tracked, swept when the skill leaves the role) and
  `ClaudeProvider` wires one managed `.claude/settings.json` entry per
  `(event, skill, matcher)`, tagged `ai-hats:<skill>:<event>:<matcher>`.
  A hook whose script cannot be resolved is skipped on both sides, so
  settings.json never points at a missing file. User-authored hook
  entries are never touched; Gemini is a no-op. The hard-coded HATS-437
  shared-state guard path is unchanged (its migration onto the registry
  is HATS-598).
- **Migration safety chain — backup-first + smoke-assert + user-hooks
  namespace** (HATS-549). Hardens `ai-hats self update` /
  non-greenfield `self init` against data-loss regressions of the
  class that produced the proxmox failure mode (user-authored
  `.agent/hooks/pre_bash_secret_guard.py` silently deleted by an
  older bump codepath, healer auto-rewriting the orphan ref in
  `.claude/settings.json`, every Bash tool call thereafter printing
  `/bin/sh: <path>: No such file or directory`). Four phases:
  - **Phase 1 — pre-bump snapshot** (`src/ai_hats/migration_backup.py`).
    Before any destructive step runs, snapshots the ai-hats-managed
    surface (`.agent/`, `.claude/settings*.json`, `ai-hats.yaml`,
    `CLAUDE.md` / `GEMINI.md`, `.githooks/`, `.gitignore`) to
    `/tmp/ai-hats/bump-backups/<utc-ts>-<slug>-<label>.tar.gz`.
    Path printed to stderr with `Recovery: tar -xzf <path> -C
    <project>` one-liner BEFORE any work starts. Retention sweep
    keeps last 10 per project-slug. Excludes `.venv` /
    `__pycache__` / `.cache` / `node_modules` / `*.pyc` / symlinks
    (regenerable / safety risks). Hard-fail on
    `BackupError`: proceeding without a snapshot defeats the
    safety guarantee. Env knobs: `AI_HATS_BUMP_BACKUP_DIR=<path>`
    overrides base dir; `AI_HATS_BUMP_BACKUP_DIR=-` hard-disables
    (one stderr WARN per call, for CI / sandbox).
  - **Phase 4 — `user-hooks/` namespace + disable-vs-rewrite**
    (`paths.user_hooks_dir`,
    `Assembler._migrate_layout_v4_hooks_partition`,
    `migration_healer._disable_user_hooks_in_settings`).
    Project-authored files under legacy `.agent/hooks/` (anything
    whose basename is NOT in `_ai_hats_owned_hook_basenames()`)
    relocate to `<ai_hats_dir>/user-hooks/` — disjoint from the
    managed `library/hooks/` namespace. The matching
    `.claude/settings.json` PreToolUse entry is REMOVED (not
    auto-rewritten); Stage B inventory carries a copy-paste JSON
    re-enable snippet. A second reconciliation pass walks
    `library/hooks/` for foreign content that landed there via a
    pre-HATS-549 auto-heal and relocates it to `user-hooks/` —
    next bump heals stuck states inherited from prior versions
    transparently.
- **Install diagnostics in `ai-hats config status` Health section**
  (HATS-497). `config status` now prints install-level fields
  alongside the existing project-side health checks: `Version`,
  `Interpreter` (Python executable + version), `Venv`, `Source`
  (editable / pinned / git, with ref and short SHA where applicable),
  `Library` path, `Resolved via` (heuristic over `AI_HATS_VENV` env >
  `ai-hats.yaml` `venv_path` > default), and `Repo HEAD` (editable
  installs only — short SHA + branch + clean/dirty). Pip-managed
  `direct_url.json` (PEP 610) is the source of truth for `Source`;
  HATS-496's `--revision` writes the ref that lights up the "pinned @"
  display. Refactor: the Health block now prints regardless of whether
  a role is active — install info is useful before init too (e.g.
  troubleshooting "what version am I on, where does it live" on a
  fresh checkout).
- **Docs: dev-vs-runtime venv discipline in CONTRIBUTING.md**
  (HATS-494). New `### Stable runtime vs editable dev install`
  subsection under `## Development setup` codifies the
  two-venv pattern (`AI_HATS_VENV` env override + `ai-hats self update
  --revision <REF>` to pin the stable venv to a known-good tag), with
  caveats about editable installs (frozen `pyproject.toml`, hardcoded
  repo path in the meta-path finder, generated `_version.py`,
  `direct_url.json` editable protection). Solves the dogfooding paradox
  where the harness driving a Claude Code session and the install
  under test are the same editable `.venv`.
- **`ai-hats self update --revision <REF>`** for pinned installs
  (HATS-496). Accepts a tag, branch, or commit SHA and installs ai-hats
  at exactly that ref instead of remote master. Unblocks reproducible
  QA, bisect, and "test against last known-good release" workflows
  without manual `pip install --force-reinstall git+...@<ref>`
  incantations. Bypasses the HATS-441 ahead/diverged downgrade guard
  with an explicit yellow WARN (the user asked for an arbitrary ref, so
  the guard would only obstruct). On an editable target venv — i.e. the
  `pip install -e .` dev loop inside the ai-hats repo itself — refuses
  unless `--force` is passed, with a message that points at the
  `AI_HATS_VENV` env override as the non-destructive alternative.
  Pre-flight `git ls-remote` validates the ref before any pip call so
  typos fail fast (~1s) instead of after a 30s pip clone. Pip-managed
  `direct_url.json` (PEP 610) records the literal ref and resolved SHA
  for later introspection — no custom marker files. New flags:
  `--revision REF`, `--force`.
- **e2e framework Wave 1 — `tmp_project` + `tmp_venv_project` fixtures**
  (HATS-478). Two reusable pytest fixtures plus a `tests/e2e/README.md`
  unlock 51 of the 69 Core e2e scenarios (32 free-tier CLI + 19
  venv-tier launcher) — contributors writing a new e2e test now pick a
  tier and write ≤10 LOC of body, instead of plumbing ad-hoc setup per
  PR. `tmp_project` is function-scoped, role-less, $0; `tmp_venv_project`
  is module-scoped and amortises the ~30-60s launcher install. New
  helper `tests/e2e/_helpers/venv.py` exposes `build_launcher_venv()`
  for callers that want raw access.
- **`safe_delete.replace(mode=...)` kwarg** (HATS-467) — optional
  octal permission bits applied to the temp file BEFORE the atomic
  rename, so executables (e.g. PreToolUse hooks) appear at the
  destination already with the right bits — no window where the file
  exists with default umask perms. Backward-compatible (`mode=None`
  keeps current behaviour). Bytes-identical no-op explicitly does NOT
  enforce the mode (skip path doesn't call `_write_atomic`).
- **Safe-delete trash bin** for all destructive ops under `src/ai_hats/`.
  New module `ai_hats.safe_delete` with `discard()` / `replace()`
  module-level API: instead of `path.unlink()` / `shutil.rmtree()` /
  in-place `path.write_text(new)`, victims are moved (or snapshotted,
  for overwrites) to `$TMPDIR/ai-hats/trash-<utc-ts>-<pid>/<relpath>/`
  before the original is touched. Recovery is `cp -r <session>/<rel>
  <project>/<rel>`. Each session writes a `MANIFEST.md` listing every
  op with timestamp + reason + original→trash mapping (HATS-470).
- **`AI_HATS_TRASH_DIR`** env var to override the trash base directory.
  Special sentinel `AI_HATS_TRASH_DIR=-` enables hard-delete mode (no
  snapshots, WARN to stderr per op) — intended for CI / ephemeral
  environments where snapshot value is zero. ENOSPC / read-only
  filesystem on snapshot raises `TrashFullError` and aborts the
  destructive operation rather than silently losing data (HATS-470).
- **Pre-commit hook** (`git-mastery` skill,
  `pre-commit-no-raw-destructive.sh`) forbidding raw `unlink` /
  `rmtree` / `rmdir` under `src/ai_hats/` outside `safe_delete.py`.
  Inline `# safe-delete: ok <reason>` markers on the offending line
  act as reviewer-visible bypasses for ephemeral / framework-state
  cases (git worktree state, session cache, empty-dir cleanup). No-op
  on projects without `src/ai_hats/`. Override:
  `AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1 git commit ...` (HATS-470).

### Changed

- **Pre-push e2e gate decoupled from the push connection** (HATS-686)
  — the maintainer master-push gate ran the ~27-min `pytest -m "(integration
  or smoke) and not quarantine" tests/e2e/ tests/smoke/` suite *inside* the
  pre-push hook. That is incompatible with pushing to GitHub over SSH: git
  holds the connection open across the hook, and GitHub closes it after ~30s,
  so the hook died with exit 141 before the suite finished (twice in HATS-684);
  client-side `ServerAliveInterval` (15 and 60) did not help. The hook is now
  **dual-mode**: `scripts/run-e2e-gate.sh` (or `… --run`) runs the suite *out
  of band* and, on pass + a clean working tree, writes a pass-marker keyed to
  HEAD's commit SHA under `<git-common-dir>/ai-hats/e2e-gate/`; the pre-push
  hook itself just checks—instantly—that a green marker exists for the master
  `local_sha` being pushed, and blocks otherwise. The HATS-550 "no-broken-
  master, no-bypass" contract is preserved (a forged marker is the moral
  equivalent of `git push --no-verify`). New maintainer flow:
  `scripts/run-e2e-gate.sh` then `git push origin master`.
- **`backlog-manager` skill split into a lean index + `references/`** (HATS-578,
  child of HATS-499). The 512-line SKILL.md — the longest in the library, bundled
  by `trait-agent` (reach 14 roles) — now violates nothing: a 133-line
  orchestrator/index (overview, core task CLI, FSM diagram, state→skill routing)
  with per-domain detail moved **verbatim** one level deep into `references/`
  (`lifecycle.md`, `hypotheses.md`, `proposals.md`, `attachments.md`,
  `relationships.md`), per the HATS-557 `>150` split policy. No behavior change:
  content relocated, the `description` frontmatter untouched. Motivation is
  policy-compliance + maintainability, **not** context savings — session-data
  analysis showed the skill *body* loads ~once per 10 sessions (backlog discipline
  rides the always-on `trait-agent` injection + `rule_backlog_discipline`, not the
  body). The real always-on lever — `trait-agent`'s standing footprint — is spun
  off to HATS-662.
- **38 skill `## When to Use` sections rewritten to boundary/disambiguation**
  (HATS-573, child of HATS-499) — applies the HATS-572 convention (the section
  loads *after* skill selection, so it must add what the one-line `description`
  can't) across the full restatement-only backlog the HATS-572 soft audit
  surfaced. Each section now names a concrete sibling to prefer or a concrete
  excluded case instead of re-listing the description's triggers — e.g.
  `audit-reviewer`↔`domain-reviewer`↔`review-role`,
  `incident-response`↔`systematic-debugging`, `backup-recovery` (data)↔
  `rollback-plan` (change), `ansible-ops` (config)↔`terraform-expert`
  (provisioning), `observability-setup`↔`reliability-checklist`. `gworkspace-cli`
  gained a section it previously lacked. Bodies-only — `description` frontmatter
  (owned by HATS-571) and `golang-*` left untouched; composition errors=0.
  Confirming evidence for HYP-038.
- **Skill-authoring discipline hardened from obra/superpowers (MIT)** (HATS-659,
  child of HATS-499). Three mechanics harvested into existing components — no new
  skill. `skill-template` + `skill-engineer` review checklist gain a **CSO
  anti-summary** rule (a `description` carries triggers + one capability phrase
  and never summarizes the procedure body — a body-summary is a shortcut the
  selector acts on *instead of* loading the skill) and a **validation-scenario**
  done-criterion (a skill is not done without one named RED baseline an agent
  fails without it; prose-level RED→GREEN→REFACTOR, no eval harness).
  `scope-guard` + `devils-advocate` gain a structural **rationalization red-flag
  table** (catch the talked-into-it thought before the action). Two-stage
  spec→quality review evaluated and skipped (no gain over `audit-reviewer`).
- **The init wizard now lists the live role catalog** instead of a
  hand-maintained list that drifted (HATS-625). The `initial-wizard`
  injection carries a new `<available_roles>` placeholder, expanded at
  prompt-build time (`build_session_prompt`, next to the HATS-380
  `<ai_hats_dir>` expansion) with the user-facing roles the resolver
  actually sees — so new roles (e.g. `dev-web`, `role-curator`) and
  project-local roles appear automatically, while engine-internal
  (`core`-layer) roles are filtered out. Layer is derived from the
  resolved role path; the per-role summary from its injection H1. The
  shared renderer is `ai_hats.role_catalog.render_role_catalog`.
- **Pipeline subsystem: `launch_provider` megastep split** (HATS-535).
  The single `launch_provider` step that pre-HATS-535 owned spawn + audit
  derivation + SESSION_END hooks + auto-retro was structurally honest in
  YAML only down to "one step does everything" — masking the asymmetry
  where SubAgent's `audit.md` was meta-only despite claude SDK persisting
  the same JSONL HITL used. Refactor:
  - Step renamed `launch_provider` → `provider` (id and class).
    `LaunchProvider` is retained as a deprecated class alias so external
    YAMLs referencing `id: launch_provider` keep loading.
  - New `make_audit` step (`src/ai_hats/pipeline/steps/make_audit.py`) —
    sole `AuditWriter` invocation surface; reads claude JSONL via
    `_claude_jsonl_path` + `_discover_claude_jsonl` mtime fallback.
  - New `run_session_end` step (`src/ai_hats/pipeline/steps/run_session_end.py`)
    — retro decision + `write_retro_log` + `_spawn_session_reviewer_background`
    - SESSION_END hooks + cyan retro reminder banner.
  - Two new sub-pipelines `library/core/pipelines/finalize-hitl.yaml`
    (`make_audit + run_session_end`) and `finalize-subagent.yaml`
    (`make_audit` only). Invoked by `WrapRunner.run` /
    `_finalize_sub_agent` from their `finally` blocks via
    `pipeline.run(..., initial={...})` — `claude_session_id` and
    `hooks_env` flow as initial state, NOT through the main pipeline
    funnel.
  - `runtime._finalize_session` shrunk to `_finalize_session_basic`
    (per-runner cleanup only: metrics.json + trace stats + smoke).
    `_print_session_end` stays in `WrapRunner.run`'s outer `finally`
    so the session-id is SIGINT-safe (HATS-086 preserved); the inline
    retro reminder banner moved to `RunSessionEnd._print_retro_banner`.
  - **SubAgent gains structured `audit.md`** — single-turn `_run_attempt`
    callers and multi-turn `_finalize_session_audit` thread `work_dir`
    through to `_finalize_sub_agent`, which invokes `finalize-subagent`
    when both `work_dir` and `claude_session_id` are known. Pre-HATS-535
    SubAgent `audit.md` was meta-only; post-HATS-535 it carries
    `👤`/`👾`/🔧/💭 markers like HITL. Mirror of HATS-523 (which brought
    `meta_prompt.txt` to HITL parity with SubAgent).
  - YAML pipeline `human` and `execute` updated to `id: provider`.
    Compatibility: `id: launch_provider` still resolves (via alias),
    but `step.io.name` returns `"provider"` regardless of which id was
    used in the YAML.
- **Unified `Assembler._refresh()` entry-point** (HATS-469). The
  historical `init` / `set_role` / `bump` triple-dispatch is gone:
  a single `_refresh(*, install_time, result)` method now drives
  registry replay (`install_time=True` only — init and `do_bump`),
  scaffold + canonical aggregator heal, provider runtime hooks
  (`.claude/settings.json` + `_materialize_pretooluse_hooks`,
  always-fire so first-session bootstrap on Claude works), and
  role-specific git hooks. State-condition diagnostics
  (orphan-skill warning, empty `.agent/` note) split into
  `_run_diagnostics()` which fires ONLY on user-initiated paths
  (`do_bump`, init re-init, `self update`) — runtime `set_role`
  stays silent (no per-session orphan-warning spam). Internal
  refactor: `Assembler.bump()` was removed; the `do_bump` CLI
  composes `_run_v07_migration` + `compose_for_role` + `_refresh`
  - `_run_diagnostics` inline. `cli/assembly.py`'s post-init
    auto-bump block was removed (init itself is the refresh path).
    Behaviour change on re-init: existing projects with
    `migration_step=0` (pre-HATS-471 shape) now replay the registry
    on `ai-hats self init` — same effect as the old `self bump`
    auto-trigger but via init directly.
- `self update`'s auto-bump now runs via `python -m
  ai_hats._bump_internal` (new hidden module entry-point) instead of
  `ai-hats self bump`. Behaviour is identical — same flags
  (`--migrate-force` / `--check-branches`), same fresh-interpreter
  semantics required by HATS-400 — but the entry-point is private and
  not surfaced in `--help` / `--tree` (HATS-470).

### Removed

- **The `.claude/plans → plan-sync` plan detour is gone** (HATS-637). A plan is
  always a task and always lives at the one canonical path
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`. `transition <ID> plan` no
  longer imports `.claude/plans/<NN>-*.md` (a stray file there is now inert),
  and the `ai-hats task plan-sync` command is removed together with its engine
  internals (`_sync_plan_from_claude_plans`, `find_claude_plan_for_task`,
  `PlanSyncAmbiguousError`). Write plan content straight into the scaffold; the
  per-section gate (HATS-635) still blocks `transition execute` on an empty
  plan. Migration: replace `task plan-sync <id> --from-file <f>` with a direct
  Write/Edit of the tracker `plan.md` — see
  [`docs/migration-v0.8.0.md`](docs/migration-v0.8.0.md).
- **`ai-hats self bump` CLI command.** Bump functionality is preserved
  and now runs only via the auto-bump path inside `ai-hats self
  update` (fresh subprocess, HATS-400) and inline from `ai-hats self
  init`. Direct user invocation of bump is rare in practice and the
  new internal entry-point makes the "framework-internal" status
  honest. The hidden `python -m ai_hats._bump_internal` remains for
  the subprocess case (HATS-470). Migration: use `ai-hats self update`
  (or `self init`) — see
  [`docs/migration-v0.8.0.md`](docs/migration-v0.8.0.md).

### Fixed

- **e2e install tests are now hermetic against an inherited `PYTHONPATH`** (HATS-685)
  — `tests/e2e/` builds subprocess envs with `os.environ.copy()`. `src/ai_hats/`
  has no `library/` subdir (it maps to the `ai_hats.library` package only at
  build time), so an inherited `PYTHONPATH=<repo>/src` — the worktree test
  workaround, and exactly what `ai-hats wt exec` sets — leaked into the
  launcher's `self init` subprocess, redirecting its `ai_hats` import to the
  source tree where `files("ai_hats.library")` raises `ModuleNotFoundError` →
  built-in roles vanished → "Role 'assistant' not found". `test_e2e_fresh_init_heals`
  was false-RED from any worktree (the standard agent execute context) and ~15
  other real-install e2e tests were latently exposed. An autouse fixture in
  `tests/e2e/conftest.py` now strips a shared denylist (`PYTHONPATH` +
  `PYTHONHOME`/`PYTHONSTARTUP`/`VIRTUAL_ENV`/`AI_HATS_DIR`/`AI_HATS_USER_HOME`)
  from `os.environ` for every e2e test, so subprocess envs exercise the real
  installed package. Deliberate `PYTHONPATH=src` tests re-set it explicitly and
  are unaffected.
- **SDK sub-agent transcript folded into audit when JSONL is absent** (HATS-682)
  — `AuditWriter.build()` parses structured turns from claude's JSONL (or the
  trace-log fallback). SDK sub-agents run with `isolation=discard` leave a
  non-empty `transcript.txt` (the LLM's final stdout) but no reachable JSONL
  (tmp-worktree project_key mismatch) and no `trace.log`, so `build()` parsed
  zero turns and emitted a `turns:0` stub — the real work (e.g. a full
  hypothesis-intake draft) was silently dropped from `audit.md`. Re-measured
  over 158 live sessions: ~15 of the 31 tiny (<2 KB) audits were this case, not
  empty sessions. `build()` now folds `transcript.txt` into the audit body
  (`## Transcript` section) **only when no structured turns were parsed** — so it
  never duplicates content already rendered as turns. `metrics.json` counters
  stay honest (no synthesized turns); `reasoning.log` is excluded (noisy/large);
  oversize is still bounded downstream by `SessionReviewRunner._truncate_audit`
  (HATS-684). Verified on `session_20260531-193008-1`: 561 B stub → 2.6 KB with
  the recovered draft.
- **Content-aware reviewer audit delivery** (HATS-684, supersedes the HATS-424
  squeeze) — `SessionReviewRunner._truncate_audit` was a blunt 8 KB head+tail
  middle-drop that cut the real evidence (🔧 tool-calls / 👾 responses) reviewers
  cite, while keeping the redundant first-turn ingested-evidence echo. Generation
  is now lossless (HATS-681/666/683), so size is managed at delivery: the
  first-turn 👤 ingested block (`# PROJECT_STATE` backlog dump / `# Reflect-all`
  handoff — 64% of corpus bytes, redundant since the reviewer already has the
  target's real content) is head-keep-bounded to 2 KB, and **all signal is kept
  verbatim** — no tight budget (capping signal was itself the cause of "cannot
  cite evidence" → `n/a` verdicts). A 250 KB safety-valve (head/tail trim,
  HATS-424 tail invariant preserved) is the only hard ceiling and never fires on
  the live corpus. Re-measured over 155 audits: median 40.6 KB → 11.5 KB;
  session-reviewer −85%, judge −77%; interactive (maintainer/role-curator) signal
  preserved in full (−0%, zero signal-loss).
- **Judge reports no longer persist the literal `payload` stub** (HATS-671) —
  reports under `sessions/retros/judge/` were occasionally written as a 7-byte
  `payload` literal instead of the report body. Root cause was test pollution,
  not the production `reflect all` pipeline: `test_save_artifact_expands_ai_hats_dir_placeholder`
  escaped its `tmp_path` and wrote into the real (gitignored) `sessions/` dir via
  two vectors — an ambient `AI_HATS_DIR` (env precedence over `project_dir`) and
  a CWD-relative `<ai_hats_dir>` expansion. Fixed by an autouse
  `_isolate_ai_hats_dir` fixture that clears ambient `AI_HATS_DIR` for every
  test, and by anchoring a relative `<ai_hats_dir>` expansion to `project_dir`
  in `SaveArtifact` so the write is CWD-independent (out-of-tree absolute
  `AI_HATS_DIR` is untouched; production was already correct).
- **`self update` rebuilds a python-broken versioned venv instead of skipping it**
  (HATS-657). After a host python upgrade a `versions/<sha>/` venv is left
  *complete* (`.complete` sentinel + `bin/ai-hats`) yet *unrunnable* — its
  `bin/python` symlink dangles. The launcher already falls back to `.venv` in that
  case (HATS-656), but `read_current_sha` and the `self update` reuse gate still
  treated the broken venv as usable: the update saw `already_current` and skipped
  the rebuild (the versioned install stayed broken), and the HATS-655 dormancy
  advisory false-fired ("your launcher predates the versioned layout" — wrong; the
  launcher is current and correctly skipping a broken venv). A single shared
  `is_usable_version` predicate (sentinel **and** `bin/ai-hats` **and**
  `bin/python` on disk) — mirroring the launcher's `-x bin/ai-hats && -x bin/python`
  — now gates both `read_current_sha` and the reuse path, so a python-broken
  versioned install resolves to "not current", routes to `.venv`, and the managed
  `self update` rebuilds it (rmtree+reinstall) with the advisory silent. Covered by
  an extended real-pip e2e (`test_e2e_install_init_break_heal`).
- **git-hook dispatcher forwards STDIN to every `.d/` hook** (HATS-654). The
  `<event>` dispatcher ran each `<event>.d/*` script in a shared-stdin loop, so
  the first stdin-consuming hook drained the git protocol and every later hook
  read EOF. For `pre-push` this silently no-opped the e2e+smoke master gate
  (HATS-550): its `empty stdin → exit 0` fast-path fired because the
  lexicographically-earlier shared-state hook had already consumed the ref list,
  leaving master pushes ungated. The dispatcher now captures STDIN once for
  events with a documented stdin protocol (`pre-push`/`pre-receive`/
  `post-receive`/`post-rewrite`/`proc-receive`/`reference-transaction`) and
  replays a fresh copy into each hook. Scoped by event name (not a runtime
  `[[ -t 0 ]]` probe) so stdin-less events (`pre-commit`/`post-*`) never `cat`
  and cannot hang on a tty/open pipe. Covered by a real-push e2e gate
  (`tests/e2e/test_prepush_dispatcher_stdin_fanout.py`).
- **SKILL.md lint findings closed (agnix Phase 1)** (HATS-626 / HYP-059).
  Three error classes the HATS-617 agnix PoC surfaced are now green:
  (a) 5 protocol skills shipped with **no YAML frontmatter** — added
  `name`+`description` to `judge-auditor-protocol`, `judge-protocol`,
  `judge-role-protocol`, `review-role`, `maintainer-quality-gate` (the 3
  with a `metadata.yaml` reuse its description verbatim). This also
  un-degrades their Claude Code skill-catalog entries, which
  `providers._extract_frontmatter_description` had been falling back to the
  bare skill name for. (b) **34 broken `assets/*` links** across 5 golang
  skills — re-vendored the 32 referenced asset files from the upstream
  commit each skill's `metadata.yaml` already records (`b29499a`,
  `samber/cc-skills-golang`), so the assets match the SKILL.md bodies.
  (c) **`metadata.openclaw` nested-map** parse error in all 33 golang
  frontmatters — stripped (kept `metadata.{author,version}`,
  `user-invocable`, `license`, `compatibility`, `allowed-tools`). Removing
  the openclaw parse error also unmasked a latent name/dir mismatch in
  `golang-linter` (frontmatter `name: golang-lint` vs dir `golang-linter`),
  now fixed. agnix reports 0 errors across all 89 library skills.
- **Managed PreToolUse hook command resolves from any cwd**
  (HATS-615). `ClaudeProvider._desired_runtime_entries` wired the
  HATS-437 shared-state guard (and skill-declared runtime hooks) into
  `.claude/settings.json` with a **bare relative** command path
  (`.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh`).
  Claude Code resolves a relative hook `command` against the agent's
  **cwd**, not the project root, so a session / sub-agent starting in a
  subdirectory invoked a path that did not exist — `/bin/sh` exited 127
  and the shared-state safety net was silently disabled (reproduced:
  cwd=root → exit 0; cwd=subdir → exit 127). The emitted command is now
  prefixed with `$CLAUDE_PROJECT_DIR/` (the existing
  `paths.CLAUDE_PROJECT_DIR_VAR` single-source-of-truth, which Claude
  Code expands at hook-execution time), so it resolves regardless of
  cwd. The migration healer / asserter already strip the prefix, and
  `_upsert_managed_entry` overwrites the stale bare entry in place, so
  existing projects self-heal on `ai-hats self update`.
- **Session-reviewer no longer crashes on dict-shaped observations**
  (HATS-610). The reviewer LLM occasionally emits an `observations`
  bullet as a single-key mapping (`{'<title>': '<detail>'}`) instead of
  a string. `SessionReviewV1.observations` is `list[str]` (`extra=
  "forbid"`), so such an entry passed the lenient IS-A-LIST shape check
  in `_validate_analysis_shape`, returned from `_run_and_validate`, then
  crashed terminally in `_merge` — *outside* the retry loop, so retries
  could never recover it (`SessionReviewError`, reviewer exit≠0;
  model-agnostic, the root cause of the flaky e2e
  `test_role_session_retro_vertical`). `observations` are non-critical
  narrative, so `_run_and_validate` now coerces non-string entries
  (`{title: detail}` → `'title: detail'`) at a single point before the
  strict merge, rather than crash; HYP/PROP refs stay strict. The e2e
  test's deterministic PROP-wiring is asserted via `_render_open_proposals`
  injection (over the prior soft "reviewer acted on the seed" assertion).
  A separate residual reviewer-retry-stacking timeout flake is tracked by
  HATS-614 / HYP-055.
- **`ai-hats self init` provider menu marks every detected provider**
  (HATS-613). The wizard menu marked only the dict-first provider whose
  `~/.<name>` config dir existed (gemini) as `(recommended)` and
  pre-selected it. A user with both `~/.claude` and `~/.gemini` saw gemini
  recommended and claude unmarked — as if claude were undetected — and
  pressing Enter silently picked gemini. `_detect_provider_default()` (one
  string) is replaced by `_detected_providers()` (the full list); the menu
  now marks **every** detected provider `(detected — found ~/.<name>)`, and
  pre-selects a click default ONLY when exactly one provider is detected —
  zero or several is ambiguous, so the user picks explicitly instead of
  inheriting the dict-first bias.
- **`ai-hats self init` works as the first command in a fresh project**
  (HATS-612). The host launcher only healed the per-project venv for
  `self update`, so a fresh-project `ai-hats self init` was rejected with
  `Run: ai-hats self update` — the user trying to *init* was told to
  *update*. `heal_if_needed` now also fires on `self init`, so a single
  command creates the default venv and configures the project. The
  venv-missing hint is context-aware: a fresh project (no `ai-hats.yaml`)
  is pointed at `self init`; an already-initialized project whose venv
  broke (e.g. a host python upgrade) keeps `self update` for heal-recovery.
  `install-launcher.sh`'s post-install "Next" hint leads with `self init`.
- **Typo'd lifecycle hook event keys are now rejected at YAML load**
  (HATS-515). `composition.hooks` previously inherited `_YamlModel`'s
  `extra="ignore"`, so a misspelled event (e.g. `sesion_start:`) was
  silently dropped and the hook never ran. `HooksConfig` now carries a
  `model_validator(mode="before")` that fails fast with
  `unknown hook event(s): <name>; allowed: …`. `_merge_hooks` derives its
  event list from the `LifecycleEvent` enum instead of a hardcoded
  6-string tuple, removing the parallel-list drift vector. Direct kwarg
  construction is unaffected — the validator only triggers on dict (YAML)
  input. Silent-key sibling of HATS-452's silent-None; ADR-0005 gains the
  invariant for future model authors.
- **master CI was red on four independent counts** (HATS-600). None were a
  regression from a single change — source had moved on without its tests,
  plus two latent gate failures:
  - **6 stale tests** realigned with intentional, reviewed source changes:
    HATS-510 dropped `integration::google` from the `assistant` role and
    moved `rule_core_vs_usage_split` ownership to the `library-curator`
    trait; HATS-501/505/507 added a `RoleNotFoundError` pre-check in
    `ComposeRole.run`; HATS-549 Phase 4 routes non-owned hooks
    (`pre-commit.sh`) to `user-hooks/` instead of `library/hooks/`.
  - **py3.11 collection `SyntaxError`** — two `tests/e2e/` files reused
    double quotes inside double-quoted f-strings (PEP-701, 3.12+ only);
    switched the inner `env[...]` subscript to single quotes.
  - **`lint (ruff)` job** — 20 pre-existing violations (F401/E401/F541)
    cleared via `ruff check --fix` (behaviour-preserving).
  - **coverage gate (76% < 78%)** — the unit suite (`-m 'not integration'`)
    barely touches `worktree.py`/`state.py`, which are exercised by
    integration tests CI deselected. A new `coverage` job runs unit +
    non-e2e real-git integration tests and owns the gate (`worktree.py`
    26%→90%, total ~83%); the `test` matrix now validates the unit suite
    across versions without a gate. The e2e tier (per-session venv builds)
    stays out of CI's hot path.
- **Done-guard / `wt merge` refused an already-merged task when the main
  checkout HEAD had wandered** (HATS-596). `Worktree.merge()` decided
  "is this merged?" from the main checkout's current HEAD branch
  (HATS-533 guard), so finalizing a task whose branch was already merged
  into its base — while the main checkout sat on a concurrent feature
  branch — emitted a false "base branch mismatch" and refused, even
  though the work was fully in `master`. Now merge-verification is
  **checkout-independent**: when the task branch tip is already an
  ancestor of the recorded base ref (`git merge-base --is-ancestor`,
  local base only), no `git merge` runs — the worktree is torn down
  cleanly regardless of where the main checkout points. `_check_clean`
  is still honored (force-bypassable). Two adjacent fixes:
  - `transition done` now plumbs `--force` into the merge so a corrective
    override reaches the merge guards (previously `--force` only relaxed
    the FSM state guard, never the git-integration check). `--force` does
    **not** relax the HEAD-mismatch guard — that stays a correctness gate
    against wrong-branch merges.
  - The HEAD-mismatch guard is gated on base-branch existence, reconciling
    HATS-533 with HATS-253: a deleted base now falls through to the
    `OriginalBranchMissing` path (which preserves the worktree branch for
    manual rebase) instead of a misleading mismatch refusal.
- **`self update` forward-compat deadlock on a newer-written
  `ai-hats.yaml`** (HATS-581). An older installed binary hard-crashed on
  a config field a newer binary had written (e.g. `migration_step`, added
  without a `schema_version` bump): `ProjectConfig`'s `extra="forbid"`
  raised at the pre-install config read, blocking the very `self update`
  that would have delivered code able to parse it. Two layers:
  - `ProjectConfig.from_yaml` now strips unknown top-level keys with a
    stderr WARN (`dropping unknown field '<key>'`) instead of raising —
    forward-compat, mirroring the existing deprecated-field strip.
    `extra="forbid"` stays as a backstop for nested models.
  - `ai-hats self update` tolerates an unparseable config: it degrades
    (prints a graceful "not parseable by the installed version" message,
    skips the composition snapshot) and forces the fresh-interpreter bump
    so the newly installed code heals the config — rather than aborting
    with a traceback before the package install.
- **Migration healer auto-rewrites to missing destinations** (HATS-549
  Phase 2). `migration_healer` Stage A1 / A2 substitutions previously
  rewrote legacy `.agent/<stem>/` refs in user files to the new layout
  without checking the new path existed on disk — masking historical
  data-loss as "successful self-heal". Per-file gate now refuses to
  heal a file if ANY ref inside it has BOTH legacy source and new
  destination missing; the ref lands in Stage B inventory with
  `reason="dst-missing"` and a `tar -xzf` recovery hint pointing at
  the Phase 1 backup. Empty legacy + empty destination is the data-loss
  signal; leaving the legacy path in place preserves the user's
  visibility into the failure.
- **`ai-hats self update` end-of-bump smoke-assert** (HATS-549
  Phase 3, `src/ai_hats/migration_assert.py`). Walks
  `.claude/settings.json{,.local}` PreToolUse / PostToolUse /
  SessionStart / SessionEnd / UserPromptSubmit / Stop / SubagentStop /
  Notification / PreCompact hook commands; for each path-like
  `command` value (expands `$CLAUDE_PROJECT_DIR/`) verifies on-disk
  existence. On any broken ref → `AssemblyError` with per-entry
  diagnosis + recovery one-liner pointing at the Phase 1 backup.
  Wired at three CLI sites: `cli/assembly.py::do_bump` (direct +
  `_bump_internal` subprocess path), `cli/assembly.py::self_init`
  (re-init only), `cli/maintenance.py` self-update in-process branch.
  The in-process branch additionally surfaces bump failure as a
  non-zero exit via a new flag — pre-fix this path swallowed
  `AssemblyError` and reported failure on stdout but exited 0,
  silently violating the safety contract.
- **`ai-hats wt merge` / `ai-hats task transition <ID> done` refuse
  when main-repo HEAD has wandered off the worktree's merge target**
  (HATS-533). `WorktreeManager._fast_forward_merge` and `_squash_merge`
  ran `git merge` in the main-repo cwd without first verifying main-repo
  HEAD was still on `self._original_branch` (the branch captured at
  `wt create` time). If HEAD moved between create and merge — manual
  `git checkout`, an IDE branch-switch, a peer agent operating directly
  in the main repo without a linked worktree — the merge silently landed
  on whatever branch was currently checked out. Same silent-wrong-branch
  class as HATS-486; bug existed since the original `feat(runtime): add
  git worktree isolation` (2026-03-27, present in v0.3.0 through v0.7.0).
  Live trigger: HATS-509's own session — worktree from master, peer
  agent committed directly on `task/hats-514` in main repo, `transition
  done` merged into the wrong branch; recovered via `git cherry-pick`.
  New `WorktreeBaseBranchMismatchError` raised BEFORE any mutation; CLI
  handlers on both surfaces emit a copy-pasteable recipe (`cd <main-repo>;
  git checkout <expected>; ai-hats wt merge` for direct callers; same
  shape with `task transition <ID> done` for the transition surface).
  Symmetric with HATS-518 (create-time twin). `--force` / `--accept-drift`
  do NOT bypass — those override different safety contracts.
- **`ai-hats task transition <ID> done` no longer leaks a misleading
  `--accept-drift` hint** (HATS-509). When the internal `wt merge`
  failed on drift, the `WorktreeDriftError` body ended with "re-run
  with `--accept-drift`" — but that flag exists on `wt merge`, not on
  `task transition`. Users copy-pasted the suggestion and hit
  `No such option`. The recipe now lives in CLI handlers, not in the
  exception body: `task transition done` catches the error and emits a
  copy-pasteable two-step path (`cd <main-repo>; ai-hats wt merge
  --accept-drift; ai-hats task transition <ID> done`) with an explicit
  note that the flag belongs to `wt merge`. Direct `ai-hats wt merge`
  callers keep equivalent UX — their CLI handler appends the recipe
  with the full command form. Origin: HATS-505 retrospective.
- **`ai-hats wt create` / `ai-hats task transition <ID> execute` now
  refuse when main-repo HEAD is not on a canonical base branch
  (`master` / `main`)** (HATS-518). Previously `WorktreeManager.create()`
  silently captured whatever branch was current as the worktree's merge
  target — if the operator parked the main repo on a feature branch
  (e.g. `task/hats-510`) before invoking `transition execute`, subsequent
  `ai-hats wt merge --accept-drift` happily merged INTO that feature
  branch instead of master. The CLI reported "merged" while master
  stayed untouched (live incident: HATS-486 session, recovered manually
  via `git checkout master && git merge --ff-only task/hats-510`). New
  guard `assert_head_is_canonical_base()` fires at both call sites
  before any `git worktree add` runs. Recovery: `git checkout <base>`
  in the main repo, then re-run. No-op on detached HEAD, non-git dirs,
  and exotic repos that have neither `master` nor `main`.
- **`task transition <ID> execute` no longer fails when the target
  branch already exists** (HATS-517). Three-way classifier inside
  `WorktreeManager.create()` under the HATS-479 create-lock:
  (A) branch exists, no worktree owns it → attach to a fresh linked
  worktree via positional `git worktree add <path> <branch>`, normal
  lifecycle proceeds; (B) branch is checked out in the MAIN worktree
  (`project_dir`) → refuse with an actionable hint pointing at
  `git switch` or `ai-hats task close` (CLI exit 2). At the CLI
  boundary, the HATS-518 canonical-base guard fires earlier and
  reports its own `WorktreeBaseBranchError` (exit 1) — the
  classifier's Case B stays as defense-in-depth for direct Python-API
  callers (`WorktreeManager().create()` in tests / external scripts);
  (C) branch is already a linked worktree but its ai-hats state JSON
  was lost (manual delete, backup restore) → adopt the existing path
  and re-persist state. Pre-fix workaround was `task close` which
  skipped the `document → review` walk. `--force --reason` only
  bypassed the FSM guard, not the worktree side-effect.
- **PreToolUse hook safety net restored** (HATS-437 + HATS-467). Post
  HATS-294 `.claude/settings.json`'s PreToolUse entry pointed at
  `<ai_hats_dir>/library/hooks/pre_bash_shared_state_guard.sh` but
  the file was never materialized → every Bash invocation died with
  "No such file or directory" and the shared-state guard was silently
  a no-op in every session. New `Assembler._materialize_pretooluse_hooks()`
  copies `*.sh` from package data (`ai_hats.library/hooks/`) to
  `<ai_hats_dir>/library/hooks/` with mode 0o755 via
  `safe_delete.replace()`. Wired into `init` / `set_role` / `bump`
  alongside the existing settings.json wiring. Idempotent
  (bytes-compare), stale files swept via `safe_delete.discard()`,
  manifest at `<target>/.manifest` tracks managed names.
- `self update` / `self init` bump path warns when an orphan
  `.ai-hats-managed` marker is detected under `~/.claude/skills/`
  (typically left by a manual `cp -r .claude/skills/
  ~/.claude/skills/` performed pre-v0.7). ai-hats has never written to
  that location — user-level Claude skills are not managed and the dir
  drifts forever without a refresh path. The WARN prints a safe-remove
  hint (`rm -rf ~/.claude/skills/`) and re-fires until the user clears
  it; ai-hats does not delete the dir itself (HATS-465).
- Closes long-standing data-loss windows where `ai-hats self update` /
  `self init` would `shutil.rmtree` or blind-`write_text` over
  user-owned files (`.claude/role.md`, `.claude/settings.json`,
  `.gitignore` writes, CLAUDE.md no-markers branch,
  `migration_healer.heal_text_file` on non-git projects). All such
  operations now snapshot to the trash bin first (HATS-470).

## [0.7.0] - 2026-05-23

Composition-and-customization release. **MAJOR** bump driven by three shifts:

1. **v0.6 → v0.7 layout migration** is now folded into `self update` /
   `self bump`; the standalone `self migrate-v07` verb is retired
   (`Migration:` under *Removed*).
2. **User-level overlays** at `~/.ai-hats/customizations.yaml` ship as a
   first-class layer; `personal-workflow` migrates there
   (`Migration:` in the ✨ BREAKING section).
3. **Role architecture splits** — `assistant` = opinionated default
   (Google Workspace + personal-workflow bundled); `dev-python` = clean
   Python baseline; `maintainer` = new role for ai-hats-codebase work.

Also: composition is now an immutable contract (ADR-0005, HATS-452),
two-level defence against autonomous shared-state writes (HATS-437),
banner reads real git state (HATS-432) + fires on non-editable installs
(HATS-458), `self update` refuses silent downgrades (HATS-441) and
short-circuits pip on a no-op, `wt merge` has a pre-merge drift guard
(HATS-457).

### 🎭 v0.7 role architecture — `maintainer` + `dev-python` extraction (HATS-381 + HATS-392)

**`maintainer` extracted (HATS-381).** Codebase work on ai-hats itself
moves out of `assistant` into a dedicated role. New shipped content:

- `core/skills/design-minimalism` — every primitive at plan stage needs
  a concrete use case; speculative additions → Out of scope.
- `core/skills/predictive-accounting` — for shrink/refactor tasks,
  present baseline + delta *before* implementation.
- `usage/skills/doc-protocol` — plan-stage style forks + scope triage
  - pre-commit artifact verification (folds three prior memory-only
    patterns).
- `core/rules/rule_core_vs_usage_split` — universal-vs-project-specific
  decision tree for library content (sourced from PROP-037).
- `core/traits/ai-hats-framework` — wraps the rule + layered-library
  injection.

The `ai-hats-maintainer` trait injection grew from ~10 to ~90 lines:
Conventional Commits, what-NOT-to-commit, canonical CLI, glossary-first,
numbered-refs, d2 practical gotchas, release flow, 8 architectural
defaults, 3 anti-patterns. Replaces the last per-project memory
references.

**`dev-python` extracted (HATS-392).** `assistant` (8 traits) is
reframed as *opinionated all-in-one* — bundled Google Workspace +
personal-workflow; not a clean baseline. New **`dev-python`** (6 traits)
is the clean Python + Shell starter. Wizard Step 3 maps `pyproject.toml`
/ `setup.py` → `dev-python`; empty / non-Python projects still →
`assistant`.

### ✨ Bring your own traits/skills — user-level overlays (HATS-421 + HATS-433, **BREAKING**)

**The mechanism (HATS-421).** A second customization layer lives at
`~/.ai-hats/customizations.yaml` — same schema as project-level,
applied to every project. No more repeating `ai-hats config customize`
across N projects; personal content no longer leaks into the package.

```bash
mkdir -p ~/.ai-hats/traits/<your-trait>
$EDITOR ~/.ai-hats/traits/<your-trait>/config.yaml
ai-hats config customize <role> --add-trait <your-trait> --global
ai-hats config status   # full tree with (built-in) / (global) / (project) source-tags
```

Compose order: built-in → global → project (project wins on conflict).
`config status` annotates every component with a source-tag.

**Migration: HATS-433, BREAKING.** `personal-workflow` trait —
TEMPORARY in v0.6 — leaves the package and moves to user-scope. Affects
`maintainer` (10 → 9 traits) and `assistant` (8 → 7 traits). Trait body
unchanged.

```bash
mkdir -p ~/.ai-hats/traits/personal-workflow
# Recover content from the previous tag, then:
ai-hats config customize maintainer --add-trait personal-workflow --global
ai-hats config customize assistant  --add-trait personal-workflow --global
# In each project:
ai-hats self bump
```

Worked example: `docs/how-to-extend.md` §"Migrating from a removed
built-in component".

### Added

- **HATS-445** — `ai-hats execute --prompt <name>` resolves
  `initial_injections/<name>.md` through the full `library_paths` chain.
  Unlocks **shell-alias custom verbs**: plugin authors ship a role +
  injection and wrap `ai-hats execute` in a shell function — custom verb
  with zero ai-hats core changes. New section in
  `docs/how-to-extend.md`: "Custom verbs via shell aliases".
- **HATS-444** — `docs/INDEX.md` is the single source of truth for the
  wizard's companion-docs catalog. Mechanical enforcement via new git
  pre-commit hook (`pre-commit-docs-index.sh`) blocks commits that
  stage structural docs/ changes without staging `INDEX.md`. Override:
  `AI_HATS_DOCS_INDEX_ACK=1`.
- **HATS-437** — Two-level defence against autonomous shared-state
  writes (HYP-026 + HYP-027). Always-on rule
  `rule_pause_before_shared_state_write` forbids `gh pr
  create/close/merge`, `gh issue comment`, `gh release create`,
  `git push`, `TaskCreate` without per-command pause + user confirmation,
  and bans chaining them in one Bash invocation. Two hook scripts back
  the rule with deterministic blocks on the **irreversible** subset
  (`gh pr merge`, `git push --force`). Per-command ack via
  `AI_HATS_SHARED_STATE_ACK=1`. Gemini sessions get the rule +
  pre-push hook only (no PreToolUse equivalent in Gemini CLI).
- **HATS-442** — Session audit records the **effective role composition
  snapshot** (traits + rules + skills with source-tags) at session
  start. `session-reviewer` cites source-tags when filing proposals
  (framework vs user vs project). Closes the observability gap created
  by HATS-421.
- **HATS-408** — `ai-hats self migrate-v07` one-shot safe migration from
  v0.6 to v0.7. Inspects on-disk artefacts, diffs each vs composition
  baseline, refuses on user edits (`--force` bypasses). Atomic single
  git commit; idempotent. *(Superseded by HATS-415 — see Removed.)*
- **HATS-401** — Session-end **Update banner** in `execute` / `human`
  pipelines. When installed SHA lags upstream, surfaces short SHAs +
  `ai-hats self update` hint under `✨ Session summary`. Non-blocking
  detached probe writes to `<ai_hats_dir>/.cache/update-check.json`
  (24h TTL). Opt-out: `AI_HATS_NO_UPDATE_CHECK=1`.

### Changed

- **HATS-415** — `ai-hats self update` and `self bump` self-heal
  v0.6 → v0.7 layouts inline. Safe-to-delete v0.6 files (bytes match
  baseline) are swept transparently; user-edited files raise
  `AssemblyError` with per-file guidance. New flags: `--migrate-force`
  (bypass refusal) and `--check-branches` (warn on local branches
  modifying paths slated for deletion). No auto-commit — user owns the
  commit decision.
- **HATS-294** — Composition is now per-session in memory; canonical
  layer no longer materialises `priorities.md` / `role.md` /
  `traits/*.md` / `rules/*.md` / `skills_index.md`. `write_canonical`
  emits only the `imports.md` aggregator. Providers' `build_override`
  renamed to `build_session_prompt`.
- **Migration: HATS-407** — `ai-hats role set <name>` is yaml-only
  (writes `default_role:` to `ai-hats.yaml`). **Removed
  `ai-hats self rollback`** — yaml-only config means `git checkout
  ai-hats.yaml` is the recovery path. Users scripting `self rollback`
  should switch to `git checkout`.

### Removed

- **Migration: HATS-415** — `ai-hats self migrate-v07` CLI command
  removed. Its logic lives inline in `Assembler.bump()` and surfaces on
  `self update` / `self bump`. Flags re-homed: `--force` →
  `--migrate-force`, `--check-branches` kept. `--no-commit` has no
  analog. Migration: drop the `self migrate-v07` invocation, run
  `ai-hats self update` — sweep auto-applies on a v0.6-shape project.

### Fixed

- **`.gitignore` legacy block sweep** — `ai-hats self bump` / `self
  update` now removes the pre-HATS-317 `# AI-HATS:START..END` managed
  block from user `.gitignore` files. HATS-317 retired the dynamic
  generator in favour of a single static line at init, but never
  shipped the one-shot cleanup — every project initialized before
  HATS-317 carried 50–90 stale per-component entries
  (`.agent/ai-hats/rules/X.md`, `traits/Y.md`, etc.), many pointing at
  v0.7-vanished paths after HATS-294 stopped materialising the
  canonical layer. Doubly stale: redundant (the bare `.agent/`
  user-init line covers the subtree) AND broken (paths no longer
  exist). New `Assembler._strip_legacy_managed_block()` strips the
  block + one preceding blank-line separator, idempotent, respects
  `manage_gitignore = False`. Delivery pattern matches HATS-413:
  persisted on `self bump` only, no rewrite-on-read. Dogfooded on
  ai-hats's own `.gitignore` (121 → 48 lines).
- **`ai-hats self update`** — short-circuit `pip install` when installed
  SHA already matches remote `master`. Saved 10-15 s per no-op update
  (60s+ on slow links — users mistook for hang). Reuses the
  HATS-432/441 ahead/behind probe; bump still runs in-process so
  migrations apply. Bump path gained a Rich spinner so the
  `heal_external_refs` walk no longer looks like a hang.
- **HATS-457** — `ai-hats wt merge` drift guard (HYP-017). Between
  `wt create` and `wt merge` the base branch could advance — another
  agent's merge into local `master`, or `origin/<base>` pulled in
  commits — and the pre-merge `grep-verify` became silently stale.
  `WorktreeManager.create` snapshots base SHA; `wt merge` does a
  best-effort `git fetch` and refuses with `WorktreeDriftError` on
  divergence. New `--accept-drift` flag (separate from `--force` —
  two checks, two flags). Legacy state files gracefully skip.
- **HATS-452** — composition / pipeline value contract. Bare `ai-hats`
  was writing a `prompt.md` missing the merged role/trait injection —
  16k chars of behavioral guidance never reached the agent. Root cause:
  `compose_role` returned `{"system_prompt": ""}` for missing role;
  `WrapRunner.run_session` accepted the empty string and replaced the
  freshly-composed list with `[""]`. Four-layer fix per
  [ADR-0005](docs/adr/0005-composition-and-pipeline-value-contract.md):
  immutable `CompositionResult`, funnel drops `None` at merge boundary,
  `compose_role` emits `{}` for no-role, `WrapRunner.run` lost
  `system_prompt_override` (HITL has no override channel). New rule
  `rule_composition_value_contract` (always-on via `trait-agent`)
  documents the four invariants.
- **HATS-432** — Update-banner false-positive suppressed when installed
  HEAD is *ahead of* or *diverged from* cached upstream. New semantics:
  `has_update` is True only when installed is *strictly behind*
  (`behind > 0 and ahead == 0`). Probe runs `git fetch <url> master` +
  `rev-list --left-right --count`; banner prefers `git describe` labels
  (e.g. `v0.6.0 → v0.6.0-19-g…`).
- **HATS-432** — Update-banner hint corrected: `ai-hats self update`
  (actual CLI verb) instead of nonexistent top-level `ai-hats update`.
  Swept through the `ai-hats-maintainer` trait, README, and
  `docs/glossary.md`.
- **HATS-458** — Update banner fires for **non-editable installs**.
  HATS-441 lost ahead/behind detection for the majority install layout
  (`pip install`, not `-e`); axes stayed `None`, banner silent. New
  fallback: bare git mirror at `<ai_hats_dir>/.cache/probe-mirror/`,
  master fetched into it, `rev-list` resolves the baked installed SHA
  (`__commit_id__` from `_version.py`) against the freshly-fetched
  object graph. `run_check` tries the editable fast path first, falls
  back to the mirror.
- **HATS-441** — `self update` refuses silent downgrades when installed
  HEAD is ahead of remote master. Reuses the HATS-432 probe; new exit
  code `3` for refusal. `--force-downgrade` opts back into the
  destructive `pip install` for callers who know what they're doing.
- **HATS-416** — `migration_healer` skips `CHANGELOG.md`. The HATS-397
  auto-rewriter had been rewriting literal `.agent/hooks/` strings
  *inside* the HATS-412 entry that described the legacy-path bug —
  collapsed «canonical X instead of legacy X» prose to «canonical X
  instead of canonical X». CHANGELOG is historical record by
  convention. Whole-file skip, filename-specific.
- **HATS-413** — `self bump` persists yaml hardening so heals stick
  across CLI invocations. HATS-408 regression: `from_yaml` healed
  `default_role := active_role` in memory only, so every invocation
  re-logged `WARN: healed default_role` until the user explicitly ran
  `migrate-v07`. New `_normalize_yaml()` persists when deprecated
  fields remain in raw yaml. Idempotent. Read-only commands still
  don't persist (no-rewrite-on-read).
- **HATS-404** — `ai-hats hyp create` / `proposal create` surface
  duplicate-id collisions as a clean `Error:` line + exit 1 instead of
  a raw `FileExistsError` traceback.
- **HATS-403** — `ai-hats task create --id N` no longer silently
  overwrites an existing task. `TaskManager.create_task` raises
  `ValueError` before `mkdir` when the path exists. Closes a
  silent-data-loss seam.
- **HATS-424** — Session-reviewer audit truncation keeps both ends of
  the session, not just the head. Old `audit_text[:8000]` head-cut made
  end-of-session events (self-retrospective Skill calls, judge-report
  writes) invisible to the reviewer for audit > 8 KB. New
  `_truncate_audit` keeps 4 KB head + 4 KB tail with a marker.
- **HATS-418** — Session-retro pipeline dispatch restored. Since
  2026-05-13 every threshold-trigger session wrote the runtime decision
  line but no `session-reviewer spawn` followed — pipeline was
  0-output for ~30 sessions. Root cause: HATS-294 dropped the v0.6
  hook-copy side-effect; `HooksRunner._find_scripts` swept an empty
  dir. Fix: `WrapRunner._finalize_session` calls
  `auto_retro._spawn_session_reviewer_background` in-process, gated on
  `action == "run"` and `HATS_SKIP_RETRO != "1"`.
- **HATS-419** — `session-reviewer` retro pipeline no longer dies on
  markdown-fenced YAML. Model frequently wraps the body in
  `` ```yaml ... ``` `` inside the `BEGIN_REFLECT_SESSION_RETRO` markers;
  `_extract_yaml` passed the fence verbatim to `safe_load`. New
  `_strip_code_fence` helper. Unblocks ~30+ stranded sessions.
- **HATS-411** — PTY shutdown is bounded. `_pty_spawn` used to call
  blocking `ptyprocess.wait()`, which hung when a Claude/libuv child
  got stuck in macOS exit-pending state. Field repro 2026-05-20: 7
  simultaneously-stuck panes. New `pty_shutdown` module escalates
  grace → SIGTERM-pgroup → SIGKILL → `WNOHANG` reap. Returns exit
  code `124` (GNU `timeout` convention) when reap can't confirm exit.
  Timings overridable via `AI_HATS_PTY_GRACE_S` / `AI_HATS_PTY_TERM_S`.
- **HATS-412** — `HooksRunner` reads from canonical
  `<ai_hats_dir>/library/hooks/` instead of legacy `.agent/hooks/`.
  Latent since HATS-314's layout migration — skill-contributed
  `session_start` / `session_end` hooks silently never fired since.
- **HATS-400** — `ai-hats self update` re-execs auto-bump in a fresh
  Python interpreter when version changed. Old in-process call kept
  executing OLD in-memory code from the running update, so
  newly-delivered migrations didn't activate until a second
  `self bump`.
- **HATS-399** — Cleaned two stale legacy-path refs from bundled
  `library/` source. Without this, `bump`'s publish step kept
  re-injecting old paths into consumer mirrors, forcing the HATS-397
  healer to repeat work non-idempotently.
- **HATS-398** — `ai-hats self update` no longer pollutes "Recent
  changes" with `Merge branch 'task/hats-NNN'` titles. `git log` now
  passes `--no-merges`.
- **HATS-397** — `self bump` / `self update` self-heals stale
  legacy-path refs left in user-managed files after the v4 layout
  migration. JSON integration files (`.claude/settings.json{,.local}`)
  are always auto-rewritten; markdown / shell / template files are
  rewritten only when git-clean and otherwise listed in a session audit.

### Internal

- **HATS-456** — materialization facade (Phase 2 closure of HATS-452 /
  ADR-0005). New module `src/ai_hats/materialize.py` exposes
  `compose_for_role(assembler, role) -> CompositionResult` as the sole
  entry point; eight+ inlined `composer.compose(role, overlays=...)`
  sites now route through it. Drift-guard test fails on direct calls
  outside the facade. ADR-0005 appended with a "Phase 2" section.

## [0.6.0] - 2026-05-18

User-extensibility and reliability release. New CLI ergonomics on the
backlog (`task close`, `task link`/`unlink`, `task transition --force`)
shrink the brainstorm-to-done loop for work shipped on master. Harness
reliability lands: reporting pipeline steps can opt into zero-output
guards and timeout retry/escalation via a new `harness:` block. A new
E2E test gate (`dev_rule_e2e_gate`) requires real-subprocess coverage
for any CLI / shell / pip surface change, backed by the
`assert_command_exists` helper and a per-session plugin-dir refactor
that fixes a cluster of sub-agent skill-loading bugs while shaving
~4.5K composition tokens. Docs polish completes the 1.0 narrative
track: new `how-to-advanced.md` and `how-to-backlog.md`, a
numbered-refs convention sweep across all docs, and the glossary
extended with system roles, traits, and core skills.

### Fixed

- **`<ai_hats_dir>` placeholder leak in pipeline `save_artifact`** (HATS-395).
  HATS-380 fixed four writer surfaces (canonical writer, provider skill
  export, Claude/Gemini overrides, `SubAgentRunner._build_meta_prompt`)
  but missed the pipeline step
  `ai_hats.pipeline.steps.save.SaveArtifact`, which formatted templates
  like `<ai_hats_dir>/sessions/retros/judge/{ts}-report.md`
  (from `library/core/pipelines/reflect-all.yaml`) directly into
  `Path(...)` without expansion. Result: a recurring 0-byte file at the
  literal path `/<project>/<ai_hats_dir>/sessions/retros/judge/...`,
  reproduced on 2026-05-18 after the HATS-380 final fix had landed. The
  step now auto-adds `project_dir` to its `io.requires` whenever the
  template embeds `<ai_hats_dir>`, and expands the placeholder via
  `expand_path_placeholders` before the `.format()` call. Two new
  tests in `tests/test_pipeline_steps.py` lock both the
  expansion path (fails-under-revert) and backwards-compat for
  placeholder-free templates. The `placeholders.py` module docstring
  now lists all four writer gates.

### Changed

- **Judge pain-extraction protocol strengthened** (HATS-390). Two skills
  updated to make contrast-first reporting the default output of a
  judge sweep rather than a result of user push-back:
  - `library/core/skills/judge-protocol/SKILL.md` — new **Step 1.5
    "Inventory deliverables since prior report"** (window derived from
    prior report's ISO timestamp; first-run-ever falls back to last 7
    days) and **Step 3.5 "Counter-claims pass"** (devil's advocate
    gates: count-check, variance-vs-failure, shipped-vs-in-flight,
    survivor-bias). The report template now requires
    `## Deliverables since prior report` (before `## Hypotheses`) and
    `## Counter-claims` (before `## Notes`); section order is
    load-bearing. Step 3.5 ships with 3 few-shot examples that mirror
    the failure modes from session `20260518-140617-1` (over-stated
    cadence, mis-framed `inconclusive`, in-flight conflated with
    shipped regression) — the format trains behaviour rather than
    asserting a rule. Step 3 (PROP triage) gains a cost-citation
    heuristic: patience for cost-cited PROPs, faster `defer`/`reject`
    for uncited pain claims open ≥ 1 sweep cycle.
  - `library/core/skills/review-proposal/SKILL.md` — `--rationale`
    cost-citation rule formalised across Step 2b (create) and Step 3
    (triage). Field reference row updated; two new examples (✓ Good
    cost-cited PROP-036 with `9-test breakage + 1 plan pivot`, ✗ Bad
    uncited pain claim) document the precedent and anti-pattern.
    Out of scope: reuse of `self-retrospective` inside judge sweep (M4 —
    tracked separately via HYP-020) and any runtime/harness changes.
    Regression tracking is filed as a new HYP post-merge.

### Added

- **`assert_command_exists` test helper** (HATS-374). New
  `tests/_cli_helpers.py:assert_command_exists(*path)` shells out to
  `ai-hats <path> --help` and asserts exit 0. Lightweight catch for the
  "command moved between groups" bug class (HATS-333 bug B: bootstrap.sh
  kept referencing `ai-hats init` after HATS-242 nested it under `self`,
  and the unit test asserted only what bootstrap output — the missing
  command was invisible to the suite). Real subprocess → callers must
  carry `@pytest.mark.integration`. Sourced from PROP-032; signature
  generalised to variadic `*path` so 3-level paths (`task hyp create`)
  work without a None special case for top-level. Applied in
  `tests/test_bootstrap_sh.py` as a pre-flight check.

- **E2E test gate for CLI/shell/pip changes** (HATS-373). New rule
  `dev_rule_e2e_gate` requires any task that touches `src/ai_hats/cli/`,
  `scripts/*.sh`, `_bootstrap.py`, `cli/maintenance.py`, or
  `[project.scripts]` to include an e2e test under `tests/e2e/` (real
  bash + real pip + real `ai-hats` binary, `@pytest.mark.integration`)
  before transitioning to `done`. Pipeline-integration tests and
  in-process `CliRunner` tests do not satisfy the gate. Sourced from
  PROP-031; motivated by HATS-333, which shipped two production bugs
  (PEP 508 rejection of local-path `ai-hats @ /path`, click
  command-nesting drift) past a green unit suite that stubbed the very
  contracts the change broke.
- **`ai-hats-maintainer` trait** (HATS-373). New project-specific trait
  in `library/usage/traits/` bundling `dev_rule_e2e_gate` plus a
  plan-stage injection that names the gate. Attached to the `assistant`
  role. Reusable: any CLI-shipping ai-hats project can pin the same
  rule via its own maintainer trait without inheriting it globally.

- **Harness reliability** (HATS-378). Pipeline steps can opt into
  post-run validation via a new `harness:` block on the step YAML:
  ```yaml
  - id: run_session_review
    harness:
      reporting: true
      on_zero_output: harness_incident
      on_timeout: { retry: 1, budget_multiplier: 2, then: harness_incident }
  ```
  - **Zero-output guard** (HATS-323) — a reporting step whose sub-agent
    exited cleanly but emitted zero tokens AND zero tool calls now
    raises `HarnessZeroOutputError` instead of silently succeeding.
    Targets the failure mode in session `20260512-074105-1`
    (judge-for-role, 4 s, 0/0/0).
  - **Timeout retry + escalation** (HATS-321) — when `on_timeout` is
    set, `SubAgentRunner` retries a timed-out subprocess at the
    configured budget multiplier and raises `HarnessTimeoutError` only
    after retries are exhausted. Without a policy, the legacy behaviour
    (return session with `timed_out=True`) is preserved.
  - **New meta-PROP target `harness-incident`** — `reflect_session_main`
    routes `HarnessReliabilityError` (timeout, zero-output) to
    `target=harness-incident`, distinct from `target=session-reviewer`.
    Both targets dedup per failed session but coexist if both arise.
  - **Pipeline metrics** — `PipelineHarness.__exit__` writes
    `<run_dir>/pipeline_metrics.json` with `silent_zero_output_incidents`
    and `harness_timeout_incidents` counters.
  - Bundled pipelines `reflect-session`, `reflect-role`, `reflect-all`
    are opted-in. User pipelines without a `harness:` block keep
    pre-v0.6 behaviour.
- `ai-hats task close <id> --resolution "..."` — fast-close a task from
  `brainstorm`/`plan` straight to `done` for work shipped on master,
  without the worktree theatre. Subsumes the original "fast-close"
  request from HATS-172. (HATS-371)
- `ai-hats task link <FROM> <TO> [--type related|see-also|fold]` and
  `ai-hats task unlink ...` — cross-reference task cards. `related` /
  `see-also` are bidirectional; `fold` is directional and sets
  `folded_into` on the source. `ai-hats task show` renders outbound
  links plus inbound "Subsumed" backlinks. (HATS-371)
- `ai-hats task transition --force --reason "..."` — bypass the FSM
  guard for corrective overrides (e.g. undo an accidental
  `brainstorm → plan`); records the override in `work_log`. (HATS-371)
- `TaskCard` fields `related: []`, `see_also: []`, `folded_into: ""`.
  Round-trip is byte-clean: empty fields are not serialized. (HATS-371)
- E2E test `tests/e2e/test_task_cli.py` covering the HATS-371 task CLI
  surface (`task close`, `task link`/`unlink`, `task transition --force`)
  with real-subprocess assertions on state transitions, link rendering,
  and error exit codes. Closes the `dev_rule_e2e_gate` retroactively for
  HATS-371 during the v0.6 release cut. (HATS-370)

### Changed

- Task FSM diagram (`docs/assets/diagrams/backlog-task-fsm.d2`) refreshed
  to show the new `close` shortcuts and the `--force` override. (HATS-371)
- `ai-hats task list --search` now also matches against `related`,
  `see_also`, and `folded_into`. (HATS-371)

### Fixed

- `<ai_hats_dir>` placeholder is now expanded before skill/role/rule
  bodies reach the agent. Previously the LLM occasionally obeyed the
  literal token and wrote artefacts to `./<ai_hats_dir>/...` in the
  project root. Substitution happens at the writer layer
  (`Assembler._write_canonical_dir` and `BaseProvider.export_skills`);
  library source files keep the placeholder as canonical reference.
  (HATS-380)

## [0.5.0] - 2026-05-17

Bootstrap experience overhaul: `ai-hats self init` now opens an
interactive wizard that walks first-run users from provider pick to a
fully composed project, with advanced workspace setup (ai_hats_dir,
venv ownership, gitignore management) handled in-session by the
`initial-wizard` role. The built-in library splits into
`library/core/` (engine) and `library/usage/` (content) to document the
engine/content boundary. New narrative docs (`how-to-configure.md`,
`how-to-extend.md`, `glossary.md`) plus five d2-rendered process
diagrams replace ad-hoc references. Legacy Russian README and historical
migration guides retired.

### Changed

- Built-in library moved from `src/ai_hats/libraries/` to a root-level
  `library/` directory and split into two layers: `library/core/`
  (engine fundament — system roles, base traits, global rules,
  foundational skills, all pipelines + injections, provider
  templates) and `library/usage/` (curated content catalog —
  opinionated roles, domain traits, opt-in skills). Both ship inside
  the installed `ai_hats.library` sub-package; no runtime behavior
  change for users. The split documents the engine/content boundary
  and unblocks future content-package extraction. (HATS-363)
- Python module `ai_hats.library` (containing `LibraryResolver`) was
  renamed to `ai_hats.resolver` to free up `ai_hats.library` as a
  data sub-package. Import updates: `from ai_hats.resolver import
  LibraryResolver`. (HATS-363)
- Bootstrap wizard's advanced setup (project dir, venv ownership,
  `.gitignore` management) moved from three hardcoded `click.prompt`s
  into Step 2 of the in-session `initial-wizard` flow; the LLM
  explains trade-offs and applies values via new `ai-hats config set`
  flags: `--ai-hats-dir`, `--venv` / `--no-venv`,
  `--manage-gitignore` / `--no-manage-gitignore`. The
  `--ai-hats-dir` form invokes a new `Assembler.relocate()` path
  that moves `library/`, `tracker/`, `sessions/`, `STATE.md`,
  recreates the managed venv, and updates `ai-hats.yaml` +
  `.gitignore` atomically (refuses on collisions, idempotent on
  retry). Closes a footgun where manual yaml edits to `ai_hats_dir`
  silently broke projects. The three `self init` flags
  (`--ai-hats-dir`, `--venv`, `--no-manage-gitignore`) remain
  unchanged for scripted use. (HATS-366)
- All user-facing docs (`docs/ARCHITECTURE.md`, the `docs/how-to-*.md`
  set, and the diagram-preview pages) are now English-only. The
  README hints that browser auto-translate handles other languages
  cleanly. (HATS-352)
- `docs/how-to-feedback-loop.md` synced with HATS-252 reality (role
  names, schema versions, harness validation flow); the "Concept
  minimum" section was thinned to point at `docs/glossary.md` for
  core terms, retaining only loop-specific Verdict and Reflect-all
  handoff definitions. (HATS-354, HATS-361)
- `initial-wizard` role injection rewritten: session-opener template
  moved into the system prompt to fix POV-confusion (the wizard
  no longer fumbles single-word language replies); companion docs
  catalog now baked in so the model can pull `docs/how-to-configure.md`
  / `glossary.md` / `how-to.md` / `how-to-feedback-loop.md` /
  `how-to-extend.md` on demand. Step 1 hardened against echo-questions.
  Advanced-setup CLI prompt no longer leaks raw `[dim]…[/]` markup.
  (HATS-355)
- Documentation now uses `<ai_hats_dir>/*` instead of the legacy
  `.agent/*` path notation, matching projects that override the
  bootstrap directory. (HATS-362)

### Added

- `ai-hats self init` defaults to an interactive bootstrap wizard:
  CLI step picks a provider (smart-default by `~/.<provider>`) and
  writes a minimal `ai-hats.yaml`, then an in-session
  `initial-wizard` role takes over — detects the stack, asks for the
  conversation language, recommends a base role, runs an in-session
  `config customize` walk, sets the task prefix, and selects the
  reflection policy. Backwards-compatible: `-p X -r Y`,
  `--no-wizard`, or non-TTY stdin falls back to the flag-only
  scripted path; non-TTY without flags fails fast with guidance.
  The wizard path self-updates ai-hats from GitHub before starting
  so first-run users land on the freshest framework version
  (`--no-update` skips). Slow `pip install` invocations in both the
  wizard self-update and `ai-hats self update` now show a
  `console.status()` dots spinner. New `ai-hats config set
  --task-prefix` overrides an existing prefix (unlike
  `self init --task-prefix`, which errors on conflict). (HATS-347)
- Five d2-rendered process diagrams in `docs/ARCHITECTURE.md`
  covering session lifecycle, auto reflect-session, manual
  reflect-all, backlog state machines (task / HYP / PROP), and the
  composition flow. Brand-light palette finalized; `docs/diagrams-preview.md`
  ships theme + sketch + custom-palette galleries, and the README
  links architecture-page thumbnails for browse-friendly navigation.
  (HATS-348)
- `docs/how-to-extend.md` — new guide for authoring own roles / traits /
  rules / skills, override-precedence chain, and replacing system
  roles. (HATS-363)
- `docs/how-to-configure.md` — single narrative walkthrough for
  first-time project setup: `ai-hats.yaml` fields, wizard vs scripted
  init (six wizard steps documented inline), role pick, customization,
  feedback policy, venv ownership, verify and pitfalls. Becomes the
  recommended entry-point after README. Also absorbs the venv-ownership
  section formerly under `docs/how-to.md` §9. (HATS-355)
- `docs/glossary.md` — naming source-of-truth for ai-hats core concepts
  (Provider, Session, Role, Trait, Rule/Skill, Backlog, Reflect,
  Worktree, Artifacts). Linked from README and CONTRIBUTING; new docs
  reference it instead of redefining terms. (HATS-361)

### Removed

- `docs/README.ru.md`. Browser translation replaces the maintained
  Russian mirror. (HATS-352)
- `docs/migration.md` (pipx → launcher) and `docs/migration-311.md`
  (v3 → v4 layout). Migration tooling is no longer a project flow:
  breaking changes ship cleanly per release, and both legacy
  migrations are long past. References in README and `docs/how-to.md`
  removed. (HATS-356)

## [0.4.0] - 2026-05-16

First public release. The repository, its history, and its docs have
been audited for sensitive data; an English-first landing page has
been added; the public surface (CLI, `ai-hats.yaml` schema, tracker
format, skill format) is documented and SemVer-protected. CI gates
every PR.

### Added

- `LICENSE` — MIT license.
- `SECURITY.md` — disclosure channel and supported-version policy.
- `CONTRIBUTING.md` — dev setup, commit conventions, and a "what not to
  commit" section that complements the privacy pre-commit hook.
- `docs/RELEASING.md` — SemVer policy, breaking-change protocol, and the
  manual release checklist.
- `docs/ARCHITECTURE.md` — internal model (components, composition,
  task state machine, project structure, library layout, skill format).
- `docs/how-to-orchestration.md` — fan-out scenarios, session tags,
  `--json` output, exit-code contract.
- `docs/README.ru.md` — Russian README for native-language readers.
- `docs/assets/` — logo (multiple sizes + SVG silhouette), social-card
  PNG (1280×640), demo GIF/MP4, and a `README.md` with the regeneration
  pipeline (Gemini prompt, ImageMagick post-processing, vhs invocation).
- `scripts/demo.tape` — vhs script that records the README hero demo
  from real ai-hats state (config status → session list → active hyps).
- `.github/ISSUE_TEMPLATE/` (bug report + feature request) and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- `.github/workflows/ci.yml` — GitHub Actions pipeline: ruff lint, test
  matrix (Python 3.11 / 3.12 / 3.13) with a **78% coverage gate**,
  bandit (`-ll`) + pip-audit security scan, install-smoke that runs
  `scripts/install-launcher.sh` on a clean runner.
- `.github/dependabot.yml` — weekly pip + github-actions update PRs.
- `pyproject.toml`: `license = "MIT"`, `license-files`, `authors`,
  PyPI classifiers; `setuptools` build requirement bumped to ≥77 so the
  PEP 639 `license-files` key resolves; `[dev]` extras now include
  `bandit>=1.7` and `pip-audit>=2.7`; `[tool.coverage.*]` +
  `[tool.bandit]` configs.
- Privacy hook: new patterns for Claude session markers
  (`sessionId` / `requestId`, `"cwd": "/...`, structural JSONL keys like
  `parentUuid` / `toolUseResult`) plus a lower size threshold for new
  fixtures under `tests/fixtures/`.
- README CI status badge.

### Changed

- `README.md` is now English-first; the Russian version moves to
  `docs/README.ru.md` with a language switch in both. README trimmed
  from 554 lines to ~125 — most reference-grade content moved to
  dedicated `docs/*.md` files.
- `docs/migration-333.md` renamed to `docs/migration.md`; this is the
  canonical migration guide. References in `README.md`, `docs/how-to.md`,
  and `docs/migration-311.md` updated.

### Removed

- Internal-ticket references in user-facing prose (`HATS-NNN` tags in
  README, the "repo is still private" warning).
- The duplicated "how to update ai-hats in a project" block in README
  (already covered by the Quick-start step 3).

### Security

- **Fix CWE-377 / bandit B306 in `providers.py`:** switched from
  `tempfile.mktemp` (TOCTOU race — between `mktemp` returning the path
  and `write_text` creating the file an attacker on the same host
  could pre-create it) to `tempfile.mkstemp`, which atomically opens
  an fd at mode 0600.
- Purged `tests/fixtures/real_conversation.jsonl` from working tree and
  from the entire git history. The fixture carried a real Claude Code
  session: absolute `cwd`, `sessionId`, `requestId`, subscription tier,
  and unredacted user prompts.
- Stopped tracking `tests/fixtures/real_conversation.jsonl` and
  `tests/fixtures/real_trace.log` via `.gitignore` so debug captures
  cannot land again.
- Rewrote git history with `git filter-repo`:
  - dropped the fixture from every commit reachable from any ref,
  - replaced `/Users/<dev>/dev/...` paths with `/path/to/...` in blob
    diffs and commit messages,
  - rewrote author / committer email to `f@muratovv.me` (438 commits
    re-hashed).
- Pre-commit privacy hook hardened with Claude-session detection
  (`sessionId` / `requestId` / `cwd` / structural JSONL keys) and a
  lower soft-warn threshold for new files in `tests/fixtures/`.

## [0.3.0] — 2026-04 / pre-public

The state of the project before the public-release sweep. Tracked
in detail in the git log and the on-disk `tracker/` backlog (HATS-001
through HATS-340). Headline themes:

- Venv-first launcher architecture (HATS-333..340).
- Pipelines and composer subsystem (HATS-261..287).
- Reflection / feedback loop and the session-reviewer role.
- Multi-provider injection (Claude and Gemini).
- Worktree isolation for sub-agents.
- Tracker primitives: tasks with a state machine, hypotheses (HYP),
  proposals (PROP).

This entry is intentionally terse — versions before the public release
were maintained in a private repository and documented in commit
messages rather than this changelog. The Unreleased section above is
where the public changelog history starts.

[Unreleased]: https://github.com/muratovv/ai-hats/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/muratovv/ai-hats/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/muratovv/ai-hats/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/muratovv/ai-hats/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/muratovv/ai-hats/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/muratovv/ai-hats/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/muratovv/ai-hats/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/muratovv/ai-hats/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/muratovv/ai-hats/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/muratovv/ai-hats/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/muratovv/ai-hats/releases/tag/v0.3.0
