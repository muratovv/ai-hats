# Changelog

All notable changes to ai-hats are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions are produced from git tags via `setuptools-scm`; everything
since the latest tag lives under **Unreleased** until the next release.

## [Unreleased]

### Added
- **HATS-442** — Session audit now records the **effective role composition
  snapshot** (traits + rules + skills with per-component source-tags
  `(built-in)` / `(global)` / `(project)`) at session start. The snapshot
  is written into `audit.md` as a `## Composition` section and into
  `metrics.json` as a structured `composition` field. `SessionFacts`
  parses it; `session-reviewer` injects an `## Effective composition`
  block into the LLM evidence prompt and learns to cite source-tags when
  explaining behaviour or filing proposals (the tag tells you whether to
  target the framework, the user overlay, or this project's overlay).
  Backwards compatible — old sessions without the field reflect normally
  with `composition: None`. Closes the observability gap created by
  HATS-421: before this change, two sessions with the same role name but
  different user-level customizations produced identical-looking retros.

### ✨ Bring your own traits/skills — user-level overlays (HATS-421 + HATS-433, **BREAKING**)

One coherent user story shipped as two commits:

**The new mechanism (HATS-421).** A second customization layer lives at
`~/.ai-hats/customizations.yaml` — same schema as the project-level
`customizations:` block, just in your home directory so it applies to
every project you open. You no longer need to repeat
`ai-hats config customize` across N projects, and personal trait/skill/rule
content no longer has to leak into the ai-hats package as TEMPORARY seams.

```bash
# One-time, per machine:
mkdir -p ~/.ai-hats/traits/<your-trait>
$EDITOR ~/.ai-hats/traits/<your-trait>/config.yaml

# Attach globally — affects every project:
ai-hats config customize <role> --add-trait <your-trait> --global

# Inspect:
ai-hats config customize <role> --show               # both layers
ai-hats config customize <role> --show --global      # only user-wide
ai-hats config customize <role> --show --project     # only project
ai-hats config status                                # full tree with source-tags
```

Compose order: built-in role → global overlay → project overlay. Project
wins on cross-layer conflict (it's applied last). Within a single layer,
putting the same name in both `add` and `remove` is a first-class
"move-to-end" reorder operation — useful when you need a trait loaded last
so dedup/priority lands on your version. `config status` now annotates
every trait, rule, and skill with a `(built-in)` / `(global)` / `(project)`
source-tag and a legend line so it's always obvious where each component
came from.

**The migration (HATS-433, BREAKING).** With the mechanism in place, the
`personal-workflow` trait — TEMPORARY in v0.6 with the explicit exit
condition "remove once user-side skill-install lands" — leaves the
package and moves to user-scope. Affected roles: `maintainer` (10 → 9
traits), `assistant` (8 → 7 traits). `initial-wizard` Step 3 role
descriptions updated to point at `--global` instead of the bundled trait.
Trait body is unchanged — same plan-mode iteration hygiene rules, just
sourced from your home directory.

```bash
# Migration (one-time, per machine) — see docs/how-to-extend.md
# "Migrating from a removed built-in component" for the worked example.
mkdir -p ~/.ai-hats/traits/personal-workflow
# Recover content from the previous tag of this repo, then:

ai-hats config customize maintainer --add-trait personal-workflow --global
ai-hats config customize assistant  --add-trait personal-workflow --global

# In each project that uses these roles:
ai-hats self bump
```

**Under the hood.** New `models.UserConfig` with the same contract as
`ProjectConfig` (`from_yaml`, `save`; missing → empty; malformed →
`UserConfigError`). `Assembler` loads the user layer at construction.
`composer.compose` now accepts `overlays: list[OverlayConfig]` alongside
the legacy single-overlay form (all 8 in-tree call sites migrated;
backwards compatible). New `_get_overlays(role)` returns the ordered
`[global, project]` list; new `_get_overlay_provenance(role)` powers the
source-tag rendering. `initial-wizard` Step 4 now offers a project-only
vs user-wide choice when the user wants to customize. 32 new tests:
UserConfig loader, sequential-apply conflict matrix, CLI `--global`
routing, source-tag rendering, end-to-end roundtrip.

Docs: new `docs/how-to.md` §4b "Global overlays for personal workflow"
recipe, §4c "Reordering composition" recipe; `docs/how-to-configure.md`
§4 grew a "Two layers" subsection with the conflict matrix;
`docs/how-to-extend.md` worked example "Migrating from a removed
built-in component".

### Added
- **HATS-408** — `ai-hats self migrate-v07` — one-shot safe migration
  from v0.6 materialised canonical layout to v0.7 per-session compose.
  Inspects every on-disk artefact (canonical role-content files,
  library mirror dirs for rules/skills, flat hook scripts under
  `library/hooks/`), diffs each vs a freshly composed baseline,
  refuses with guidance on user edits (`--force` bypasses with one
  stderr WARN per overwritten file). Atomic single git commit; idempotent
  re-runs no-op. Flags: `--force / --no-commit / --check-branches`.
  Exit codes 0/1/2/3/4 documented in `--help`. The companion gate
  `Assembler._refuse_on_v06_layout` blocks `bump` / `self update` on
  v0.6 projects so the destructive sweep can't fire before the user
  runs `migrate-v07` — closes the silent-data-loss seam that exists
  when HATS-294 + HATS-407 ship together without it.
- **HATS-401** — Session-end **Update banner** in `execute` / `human`
  pipelines. When the installed `ai-hats` SHA lags upstream `master`, a
  three-line block surfaces under the `✨ Session summary`: short SHAs,
  the `ai-hats update` command, and a dim opt-out hint. The probe is
  non-blocking — a detached background subprocess writes the result to
  `<ai_hats_dir>/.cache/update-check.json` (24h TTL,
  stale-while-revalidate). Opt-out: `AI_HATS_NO_UPDATE_CHECK=1` suppresses
  both probe and banner. New module `ai_hats.update_check`, new pipeline
  steps `check_update_async` / `render_update_banner`, glossary entries
  for **Session summary** vs **Update banner**.

### Changed
- **HATS-415** — `ai-hats self update` and `self bump` now self-heal v0.6 →
  v0.7 layouts inline. The naive HATS-408 `_refuse_on_v06_layout` gate
  (manifest-only check) is replaced by the real `plan_migration`
  classifier inside `Assembler.bump()`: safe-to-delete v0.6 files (bytes
  match composition baseline) are swept transparently for the common
  case; user-edited files raise `AssemblyError` with per-file guidance
  pointing at the v0.7 home (`user-rules/` or `library/usage/...`). New
  flags on both `self update` and `self bump`: `--migrate-force`
  (bypass user-edit refusal, one stderr `WARN` per overwritten file)
  and `--check-branches` (warn when local branches modify paths slated
  for deletion). **No auto-commit** — sweep deletions land in the
  worktree, user commits at leisure (same pattern as the existing
  `_normalize_yaml` yaml rewrite). Migration triggers only when Tier-1
  framework files (`priorities.md` / `role.md` / `traits/*` / `rules/*` /
  `skills_index.md`) are present — projects with user-authored hooks
  under `library/hooks/<x>.sh` no longer falsely refuse.
- **HATS-294** — Composition is now per-session in memory; the canonical
  layer no longer materialises `priorities.md` / `role.md` /
  `traits/*.md` / `rules/*.md` / `skills_index.md`. `write_canonical`
  emits only the `imports.md` aggregator listing `@./user-rules/*.md`
  files (plus the `MANAGED` manifest tracking it). Providers'
  `build_override` renamed to `build_session_prompt`; runtime collapsed
  shadow-vs-permanent paths into a single compose path; per-session
  cache dir replaces the permanent `.claude/skills` export.
- **HATS-407** — `ai-hats role set <name>` is now yaml-only (writes
  `default_role:` to `ai-hats.yaml` and updates the running provider's
  system prompt inline). Removed `ai-hats self rollback` — yaml-only
  config means `git checkout` is the recovery path. Swept stale
  `.last_backup` pointers and dropped `PROFILE_FILE`.

### Removed
- **HATS-415** — `ai-hats self migrate-v07` CLI command. The one-shot
  v0.6 → v0.7 migration (introduced under HATS-408) is no longer a
  separate command — its logic lives inline in `Assembler.bump()` and
  surfaces on `self update` / `self bump`. Power-user levers re-homed
  as flags: `--force` → `--migrate-force`, `--check-branches` kept as
  is. `--no-commit` has no analog (bump never committed; user reviews
  and commits at leisure). The `chore(v0.7): migrate to dynamic role
  composition` atomic commit envelope is gone — the user owns the
  commit decision.

### Fixed
- **HATS-432** — Update-banner false-positive suppressed when installed
  HEAD is *ahead of* or *diverged from* cached upstream master. The old
  `installed_sha != latest_sha` check fired in both cases (live reproducer:
  arrow pointed backwards in time). New semantics: `has_update` is True
  only when installed is *strictly behind* upstream (`behind > 0 and
  ahead == 0`). Probe now runs `git fetch <url> master` into the package
  checkout, then `git rev-list --left-right --count <installed>...<latest>`
  for the counts; `git describe --tags` resolves human-readable labels.
  Cache schema gains `behind` / `ahead` / `installed_label` / `latest_label`
  (legacy cache files parse cleanly and regenerate on the next probe; no
  migration). Banner now prefers the `describe` labels (e.g.
  `v0.6.0 → v0.6.0-19-g…`) and falls back to short SHAs with an explicit
  `, +<behind> commits` suffix when no labels are available. New
  regression tests assert silence for installed-ahead and diverged states
  end-to-end.
- **HATS-432** — Update-banner hint corrected: the cyan command line now
  reads `ai-hats self update` (the actual CLI verb) instead of the
  nonexistent top-level `ai-hats update`. Same fix swept through the
  `ai-hats-maintainer` trait's Canonical CLI section (also `ai-hats bump`
  → `ai-hats self bump`), README §Update notification, and
  `docs/glossary.md` Update-banner entry so all user-facing prompts agree.
- **HATS-424** — Session-reviewer audit truncation now keeps both ends of
  the session, not just the head. The old `audit_text[:8000]` head-cut
  made end-of-session events (self-retrospective Skill calls, final
  commits, transitions, judge-report writes) structurally invisible to
  the reviewer when audit > 8 KB. Verified false-negative across 8
  sessions where `🔧 Skill: self-retrospective` lived at bytes 22K-60K
  and the reviewer returned `n/a` with "no self-retro visible". New
  `_truncate_audit` helper keeps `_AUDIT_HEAD` (4 KB) + `_AUDIT_TAIL`
  (4 KB) with a `... (<N> bytes truncated from middle) ...` marker so
  the reviewer knows the gap exists. Prompt budget unchanged. Three
  unit tests cover short-passes-through, long-keeps-sentinels, and
  boundary-no-truncation. Re-running `reflect session` on existing
  self-retro sessions restores correct HYP-020 signal (separate
  backfill task).
- **HATS-418** — Session-retro pipeline dispatch restored. Since 2026-05-13
  every threshold-trigger session wrote the `runtime decision run: …` line
  to `<runs>/session_<sid>/retro.log` but no `hook spawn` / `session-reviewer
  spawn` ever followed — pipeline was 0-output for ~30 sessions. Root cause:
  HATS-294 dropped the v0.6 `_collect_from_manifest` side-effect that copied
  skill-shipped hook scripts (e.g. `session_end_auto-retro.sh`) into
  `<ai_hats_dir>/library/hooks/`, so the `HooksRunner._find_scripts` sweep
  found an empty directory after HATS-412 wired it up. Fix bypasses the
  shell-hook indirection for this flow: `WrapRunner._finalize_session` now
  calls `auto_retro._spawn_session_reviewer_background` in-process right
  after writing the runtime decision line, gated on
  `action == "run"` and `HATS_SKIP_RETRO != "1"` (recursion guard). The
  `HooksRunner.run(SESSION_END)` call below stays intact for any
  user-authored hooks landing in `library/hooks/` later. Restoring the
  full skill→hook→runtime install path remains a deliberate non-goal —
  no concrete user demand. Pairs with HATS-419's parser fix; together
  they close both L1 (dispatch) and L2 (parse) regressions opened in
  the 2026-05-13 boundary window. New smoke tests under
  `tests/smoke/test_session_retro_pipeline.py` lock the dispatch
  contract and the `start_new_session=True` SIGHUP-immunity kwarg.
- **HATS-419** — `session-reviewer` retro pipeline no longer dies on
  markdown-fenced YAML. The model frequently wraps the YAML body in
  ` ```yaml ... ``` ` inside the `BEGIN_REFLECT_SESSION_RETRO` /
  `END_REFLECT_SESSION_RETRO` markers; `_extract_yaml` passed the fence
  verbatim to `yaml.safe_load`, which choked with `found character '\``
  that cannot start any token`. New `_strip_code_fence` helper removes
  the surrounding fence after delimiter extraction; plain YAML passes
  through unchanged. Unblocks the ~30+ stranded threshold-trigger
  sessions accumulated since the model behavior shifted; parent
  investigation HATS-418 still covers the L1 hook-dispatch and L3
  invalid-YAML failure modes.
- **HATS-411** — PTY shutdown is now bounded — the `_pty_spawn` finally
  block used to call `ptyprocess.wait()` (blocking `os.waitpid(pid, 0)`),
  which hung forever when a Claude/libuv child got stuck in macOS
  exit-pending state (`ps` STAT `?Es`, JS heap released but libuv
  handles still open). Field repro on 2026-05-20: 7 simultaneously-stuck
  panes across Claude 2.1.126/138/139/143. New `ai_hats.pty_shutdown`
  module escalates grace → SIGTERM-pgroup → SIGKILL → `WNOHANG` reap;
  worst case the zombie remains but the parent returns and the pane is
  recoverable. Timings overridable via `AI_HATS_PTY_GRACE_S` (default
  5.0) / `AI_HATS_PTY_TERM_S` (default 2.0). When the WNOHANG reap can't
  confirm exit (kernel still wedged), `_pty_spawn` now returns `124`
  (GNU `timeout` convention) instead of silently `0`, so callers see the
  unresolved-exit signal. Also emits DECRST mouse-tracking reset on the
  parent's outer stdout after shutdown — guarded by `os.isatty(fd)` so
  redirected output (`ai-hats run > out.log`) is not polluted with
  escape bytes — preventing raw SGR mouse reports from leaking into the
  surrounding shell when the child crashed without disabling them.
- **HATS-412** — `WrapRunner` lifecycle `HooksRunner` now reads from the
  canonical `<ai_hats_dir>/library/hooks/` instead of the legacy
  `.agent/hooks/` path. The bug was latent since HATS-314's layout
  migration (commit `2eb329d`) — `HooksRunner._find_scripts` returned
  `[]` for every project since, so skill-contributed `session_start` /
  `session_end` hooks silently never fired. Extracted
  `_make_session_hooks_runner` helper guards against future drift.
- **HATS-400** — `ai-hats self update` now re-execs auto-bump in a fresh
  Python interpreter when the version on disk actually changed. The old
  in-process call kept executing OLD in-memory code from the running
  update — so migrations or healer code newly delivered by pip install
  did NOT activate until the user manually ran a second `ai-hats self
  bump`. This was the proxmox regression where HATS-397 healer's first
  `self update` didn't fix `.claude/settings.json`. Same-version updates
  keep the in-process path (no overhead).
- **HATS-399** — Clean two stale legacy-path refs from the bundled
  `library/` source (`worktree-isolation/SKILL.md`,
  `git-mastery/git_hooks/pre-commit-smoke.sh`). Without this, `bump`'s
  publish step kept re-injecting old paths into consumer mirrors
  (`.claude/skills/`, `.githooks/`), forcing HATS-397 healer to repeat
  work on every bump (non-idempotent). New regression test
  (`test_library_no_legacy_refs`) prevents reintroduction.
- **HATS-398** — `ai-hats self update` no longer pollutes the "Recent
  changes" block with `Merge branch 'task/hats-NNN'` titles. The git-log
  fetch now passes `--no-merges`, leaving only conventional-commit titles
  from the actual work (`fix(...)`, `feat(...)`).
- **HATS-397** — `ai-hats self bump` / `self update` now self-heals stale
  legacy-path refs left behind in user-managed files after the v4 layout
  migration moves content under `<ai-hats_dir>/`. JSON integration points
  (`.claude/settings.json{,.local}`) are always auto-rewritten; markdown,
  shell, and template files (`*.md` / `*.txt` / `*.j2` / `*.sh` / `.envrc`)
  are rewritten only when git-clean and otherwise listed in
  `<ai-hats_dir>/sessions/audits/<ts>-legacy-refs.md`. Triggered by the
  proxmox regression where `.claude/settings.json` PreToolUse-hook
  references to `.agent/hooks/<file>` broke silently after `ai-hats self
  update`.

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

[Unreleased]: https://github.com/muratovv/ai-hats/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/muratovv/ai-hats/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/muratovv/ai-hats/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/muratovv/ai-hats/releases/tag/v0.3.0
