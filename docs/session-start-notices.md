# Session-start notices

Pre-launch lines `WrapRunner` renders BEFORE the wrapped TUI spawns. The wrapped
CLI tears the terminal into the alternate screen buffer the instant it starts,
clobbering anything printed before it — so any message that must reach the human
is rendered pre-spawn and held on screen by the **read-hold** (HATS-825/833/847).

## Notice model

One notice is a `StartupNotice(level, text)` (`src/ai_hats/startup_notices.py`):

| Level  | Color       | Meaning                                                     |
| ------ | ----------- | ----------------------------------------------------------- |
| `note` | bold green  | "we fixed drift" — an auto-heal happened, nothing wrong     |
| `warn` | bold yellow | "degraded setup" — a fail-open startup step found a problem |

Any notice triggers the hold; a clean start renders nothing and holds for
nothing.

## Read-hold policy

`_startup_hold_seconds` (`src/ai_hats/startup_notices.py`): 10 s when notices
exist on a TTY, `0` otherwise (headless runs are never delayed). The countdown
is Enter-skippable (HATS-847) and Ctrl-C aborts the launch. `AI_HATS_STARTUP_HOLD`
overrides the delay for every case (`0` disables).

## Producers

All run in `WrapRunner.run()` between session creation and the PTY spawn,
each fail-open — a broken check must never block session start:

| Producer                   | Emits                                                                        |
| -------------------------- | ---------------------------------------------------------------------------- |
| `_resync_managed_hooks`    | NOTE per healed hook surface; WARN on failure / version-skew (HATS-833)      |
| `_check_skill_collisions`  | NOTE on mirror heal; WARN on a home-scope skill collision (HATS-901/907)     |
| `_payload_startup_notices` | WARN per hooks warning carried from the first-run compose seam (HATS-970)    |
| finalize-hitl preload      | WARN when the finalize pipeline fails to eager-load (HATS-566)               |
| `_lint_provider_settings`  | WARN per provider-reported settings pitfall (HATS-1006, below)               |
| `_lint_env_drift`          | WARN when the editable dev env is stale — needs `uv sync` (HATS-1013, below) |

## Provider settings lint (HATS-1006)

The provider CLI may detect problems in its own settings files but print them
post-spawn, where the alt-screen eats them — the motivating incident: Claude
Code v2.1.210 deprecated `Write(path)` / `NotebookEdit(path)` / `Glob(path)`
permission rules and warns once per offending rule at startup, invisibly in a
wrapped session.

The lint lives with the surface, not the runner: `Provider.settings_lint_warnings
(project_dir)` returns human-readable warnings (base surfaces: none), and
`WrapRunner._lint_provider_settings` maps them to WARN notices. `ClaudeProvider`
checks the settings chain

1. user-global `settings.json` (`$CLAUDE_CONFIG_DIR`, else `~/.claude/`),
2. project `.claude/settings.json`,
3. project `.claude/settings.local.json`

against a data-driven table of deprecated rule kinds
(`DEPRECATED_RULE_TOOLS` in `src/ai_hats/providers.py`; a new upstream pitfall
is one row). Every finding names the file, the rule, and the exact replacement:

```
⚠ 1 startup warning(s):
  • ~/.claude/settings.json: deny rule Write(//**/.env) is ignored by Claude Code ≥2.1.210 — replace with Edit(//**/.env)
```

Warn-only by design: the settings files are user-owned, so ai-hats never
rewrites them (contrast: managed-hook surfaces, which ARE ai-hats-owned and
auto-heal). Per-file fail-open: a missing or malformed settings file
contributes nothing — a broken settings file is the provider CLI's own loud
failure.

## Editable env-drift lint (HATS-1013)

uv editable installs freeze dist-info metadata at sync time: after any
workspace version bump, `importlib.metadata` — and everything on top of it
(`--version`, `pip check`, the HATS-992 requires-verifier) — keeps reporting
the last-synced version until `uv sync` runs. The motivating incident
(HATS-991 F5): `ai-hats-tracker --version` said 0.5.0 in a venv whose source
was already 0.6.0.

`stale_dev_env_warnings` (`src/ai_hats/env_drift.py`) wraps
`uv sync --check --inexact --all-packages --project <repo_root>` — the verdict
is uv's documented exit code, never output parsing (`--inexact` is what keeps
intentionally-installed dev extras from producing a permanent false
"outdated"). Output lines naming workspace members are best-effort message
enrichment:

```
⚠ 1 startup warning(s):
  • dev env outdated: stale ai-hats, ai-hats-tracker 0.5.0 -> 0.6.0 — run 'uv sync --inexact --all-packages'
```

Gated to the dev checkout only: ai-hats installed editable AND the running
interpreter inside `<repo_root>/.venv` — consumer installs never run the
check. Warn-only (bare `uv sync` in exact mode would remove dev-extra
packages, so the hint pins `--inexact`); fail-open on uv missing, timeout, or
any exit code other than 0/1.
