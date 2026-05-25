# Changelog

All notable changes to ai-hats are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions are produced from git tags via `setuptools-scm`; everything
since the latest tag lives under **Unreleased** until the next release.

## [Unreleased]

### Fixed
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
  guard `_assert_head_is_canonical_base()` fires at both call sites
  before any `git worktree add` runs. Recovery: `git checkout <base>`
  in the main repo, then re-run. No-op on detached HEAD, non-git dirs,
  and exotic repos that have neither `master` nor `main`.

### Added
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

### Changed
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
  + `_run_diagnostics` inline. `cli/assembly.py`'s post-init
  auto-bump block was removed (init itself is the refresh path).
  Behaviour change on re-init: existing projects with
  `migration_step=0` (pre-HATS-471 shape) now replay the registry
  on `ai-hats self init` — same effect as the old `self bump`
  auto-trigger but via init directly.

### Fixed
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

### Added
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
- `self update`'s auto-bump now runs via `python -m
  ai_hats._bump_internal` (new hidden module entry-point) instead of
  `ai-hats self bump`. Behaviour is identical — same flags
  (`--migrate-force` / `--check-branches`), same fresh-interpreter
  semantics required by HATS-400 — but the entry-point is private and
  not surfaced in `--help` / `--tree` (HATS-470).

### Removed
- **`ai-hats self bump` CLI command.** Bump functionality is preserved
  and now runs only via the auto-bump path inside `ai-hats self
  update` (fresh subprocess, HATS-400) and inline from `ai-hats self
  init`. Direct user invocation of bump is rare in practice and the
  new internal entry-point makes the "framework-internal" status
  honest. The hidden `python -m ai_hats._bump_internal` remains for
  the subprocess case (HATS-470).

### Fixed
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
  + pre-commit artifact verification (folds three prior memory-only
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
  ` ```yaml ... ``` ` inside the `BEGIN_REFLECT_SESSION_RETRO` markers;
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

[Unreleased]: https://github.com/muratovv/ai-hats/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/muratovv/ai-hats/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/muratovv/ai-hats/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/muratovv/ai-hats/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/muratovv/ai-hats/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/muratovv/ai-hats/releases/tag/v0.3.0
