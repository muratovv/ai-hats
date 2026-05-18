# Changelog

All notable changes to ai-hats are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions are produced from git tags via `setuptools-scm`; everything
since the latest tag lives under **Unreleased** until the next release.

## [Unreleased]

### Added

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
