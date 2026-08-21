# Changelog

All notable changes to `ai-hats-cline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Composed `PreToolUse` and `PostToolUse` chains now reach Cline HITL and
  Automate sessions through native `--hooks-dir` entrypoints materialized in
  the per-session cache. The surface-local adapter covers legacy and current
  Cline command/file tool names, including one file-hook payload per
  `apply_patch` target (HATS-1775).

### Security

- Explicit `deny` and `ask` decisions cancel Cline `PreToolUse` calls. Because
  Cline cannot open a permission prompt or apply a hook input rewrite from its
  hook response, `ask` remains blocked with retry guidance and never
  self-applies `updatedInput`. Missing or malformed session state and hook
  process failures emit diagnostics and fail open (HATS-1775).

## [0.4.0]

### Added

- **`session_skills_root`** (HATS-1540) — where this surface mirrors a session's
  composed skills (`<session_cache_dir>/skills/`). A bound `checks:` gate now
  resolves its script from that root in-session, so a surface WITHOUT the
  accessor inherits the `Provider` default of `None` and **every bound
  transition in its sessions is refused**. Upgrade this package together with
  the `ai-hats` release that carries the accessor.

### Changed

- Cline now runs through the unified artifact-builder (ADR-0018,
  `build_category_artifact`) on the clean-root invariant, both HITL and
  Automate. Role skills materialize into the **per-session cache**
  (`<cache_root>/sessions/<sid>/skills` — outside the project, default
  `~/.cache/ai-hats/<project-key>/`, HATS-1398) and reach cline via
  `--config <cache>` (cline scans `<base>/skills`; the spike HATS-1191 proved
  the flag). Nothing is written into the project root — no `.cline/`, no
  `.gitignore` mutation. Each session owns its cache dir, so the old
  ref-counted `.cline/skills` marker / filelock is gone (HATS-1171).

### Added

- `get_env` sets `CLINE_DATA_DIR` to the real cline home (`~/.cline/data`).
  `--config` relocates cline's base dir, so pinning the data dir keeps auth,
  session history, and `resolve_transcript` intact (HATS-1171).
- `get_env` still sets `CLINE_HUB_PORT` to a per-session ephemeral port so
  parallel sessions and `cline --help` don't crash with `EADDRINUSE`
  (HATS-973).

### Removed

- The TS hook plugin (`ai-hats-hooks.ts`), `ensure_runtime_hooks`,
  `CLINE_HOOKS_DIR`, and the `.cline/plugins` / `.cline/skills` materialization
  (plus the `.gitignore` mutation). The plugin never loaded — cline's plugin
  sandbox requires `jiti`, which the CLI does not bundle (see HATS-1083). This
  release therefore left Cline without per-tool runtime gating; HATS-1775 later
  restored it through native `--hooks-dir` entrypoints (HATS-1171).

## [0.3.0]

`ClineParser` (Adapter B, HATS-960) + TS plugin hooks (HATS-964).

### Added

- `ensure_runtime_hooks` override — materializes a TS plugin wrapper
  (`ai-hats-hooks.ts`) + hook index (`ai-hats-hooks.json`) into
  `<project>/.cline/plugins/` (HATS-964). `CLINE_HOOKS_DIR` env +
  `--hooks-dir` CLI flag point cline at the directory so the plugin loads
  (cline v3.0.3 does NOT auto-discover `.cline/plugins/`). The plugin's
  `beforeTool` hook bridges cline's AgentPlugin lifecycle to ai-hats's
  existing bash guard (`pre_bash_shared_state_guard.sh`), translating
  `context.input` → `{"tool_input":{"command":...}}` and blocking on
  non-zero exit (explicit fail_closed). `.cline/plugins/` auto-gitignored.
- `get_env` now sets `AI_HATS_DIR` + `AI_HATS_PROJECT_DIR` +
  `CLINE_HOOKS_DIR` (path to the materialized plugins) so the plugin can
  locate the guard scripts at runtime and cline scans the directory at
  session start.
- `ai_hats_cline.ClineParser` — a `TranscriptParser` that normalizes cline's
  single-object `<id>.messages.json` into observe's `ParsedTranscript` (turns +
  `model_stats` + `agg_usage`) and a full `usage/v1` report (always-on proxy,
  reconstructed-attribution timeline, tool aggregates). Wired via
  `ClineProvider.transcript_parser()` (HATS-948), replacing the default
  trace-only parse.

### Changed

- Depends on `ai-hats-observe>=0.3.0` (the `TranscriptParser` base + `usage/v1`
  schema `ClineParser` reuses) — the MVP omitted it.

## [0.2.0]

Native skill materialization into `.cline/skills/` (HATS-963).

### Added

- `materialize_runtime_skills` override — copies the composed role's skills
  into `<project>/.cline/skills/` (cline's native discovery path, HATS-963).
  `/skills` in the TUI now shows the role's skills; `/skill-name` loads bodies.
  Idempotent; user-authored skills preserved via `.ai-hats-managed` marker.
  `filelock` guards the wipe-and-rebuild against concurrent sessions.
- `.cline/skills/` auto-added to project `.gitignore` (materialized mirror).

### Changed

- `include_skills=True` kept as safe fallback — flip to `False` gated on a
  live smoke proving `/skills` works in the TUI (plan R7 kill criteria).

## [0.1.0]

First cut of the Cline surface plugin (HATS-956) — the first in-tree consumer of
the provider IoC seam (HATS-870). Registers `cline` as an ai-hats provider via
the `ai_hats.providers` entry point, with zero edits to `src/ai_hats/**`.

### Added

- `ai_hats_cline.ClineProvider` — the `Provider` adapter for the `cline` CLI:
  inline-`-s` role delivery, interactive TUI for HITL (`cline -i`), headless
  `--yolo --json` for the automate path, no `--worktree`, ambient
  `CLINE_DATA_DIR` (keeps the machine's cline auth). Registered under the
  `ai_hats.providers` entry point as `cline`.
