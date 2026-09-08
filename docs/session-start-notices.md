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

**The hold is the wait, not the render.** A zero delay skips the wait and shows
the notices anyway, so a headless, CI or `AI_HATS_NON_INTERACTIVE` launch still
says what it found. Until HATS-1753 it did not: the zero-delay branch returned
before printing, and those runs recorded every notice in `diagnostics.json`
while showing none of them.

## Producers

All run in `WrapRunner.run()` between session creation and the PTY spawn,
each fail-open — a broken check must never block session start:

| Producer                         | Emits                                                                        |
| -------------------------------- | ---------------------------------------------------------------------------- |
| `_resync_managed_hooks`          | NOTE per healed hook surface; WARN on failure / version-skew (HATS-833)      |
| `_check_skill_collisions`        | NOTE on mirror heal; WARN on a home-scope skill collision (HATS-901/907)     |
| `_check_skill_script_collisions` | WARN per skill-script filename collision (HATS-1114)                         |
| `_payload_startup_notices`       | WARN per hooks warning carried from the first-run compose seam (HATS-970)    |
| `_payload_startup_notices`       | one notice per composition `Diagnostic`, at the level its producer set (HATS-1753, below) |
| finalize-hitl preload            | WARN when the finalize pipeline fails to eager-load (HATS-566)               |
| `_lint_provider_settings`        | WARN per provider-reported settings pitfall (HATS-1006, below)               |
| `_lint_env_drift`                | WARN when the editable dev env is stale — needs `uv sync` (HATS-1013, below) |
| `_check_broken_hook_refs`        | WARN per settings hook ref pointing at a missing file (HATS-1509, below)     |

## Provider settings lint (HATS-1006)

The provider CLI may detect problems in its own settings files but print them
post-spawn, where the alt-screen eats them — the motivating incident: Claude
Code v2.1.210 deprecated `Write(path)` / `NotebookEdit(path)` / `Glob(path)`
permission rules and warns once per offending rule at startup, invisibly in a
wrapped session.

The lint lives with the surface, not the runner: `Surface.settings_lint_warnings
(project_dir)` returns human-readable warnings (base surfaces: none), and
`WrapRunner._lint_provider_settings` maps them to WARN notices. `ClaudeSurface`
checks the settings chain

1. user-global `settings.json` (`$CLAUDE_CONFIG_DIR`, else `~/.claude/`),
2. project `.claude/settings.json`,
3. project `.claude/settings.local.json`

against a data-driven table of deprecated rule kinds
(`DEPRECATED_RULE_TOOLS` in `src/ai_hats/surfaces/claude/provider.py`; a new
upstream pitfall is one row). Every finding names the file, the rule, and the
exact replacement:

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
  • dev env outdated: stale ai-hats, ai-hats-tracker 0.5.0 -> 0.6.0 — run:
    uv sync --inexact --all-packages
```

The fix command is rendered on its own line, unquoted, so it copy-pastes
straight into a shell.

Gated to the dev checkout only: ai-hats installed editable AND the running
interpreter inside `<repo_root>/.venv` — consumer installs never run the
check. Warn-only (bare `uv sync` in exact mode would remove dev-extra
packages, so the hint pins `--inexact`); fail-open on uv missing, timeout, or
any exit code other than 0/1.

## Broken hook refs (HATS-1509)

A settings entry can outlive the script it names — the pre-HATS-1170 residue
`ai-hats:hats-437` points at `library/hooks/pre_bash_shared_state_guard.sh`, <!-- prose-refs: was -->
which materialization deletes once the guard moves into the `safety-guard`
skill and gets a skill-prefixed filename. The harness then prints
`No such file or directory` on every matching tool call, and nothing says the
entry is ai-hats's to reclaim.

`find_broken_hook_refs` (`src/ai_hats/migration_assert.py`) stats every
path-like hook command. `WrapRunner._check_broken_hook_refs` runs it over
`SESSION_SCAN_TARGETS` — `.claude/settings.json`, `.claude/settings.local.json`
and agy's pre-HATS-1166 `.gemini/settings.json` remnant, whose entries carry
`command` on the matcher itself rather than under a nested `hooks` list.

The remedy follows ownership, which is why `BrokenHookRef.managed` exists: an
`ai-hats:`-tagged entry (either tag spelling) is reclaimed by the install-time
sweep, so the notice names `ai-hats self update`; an untagged one is the user's
own and only they can fix it.

```
⚠ 1 startup warning(s):
  • .claude/settings.json: PreToolUse hook points at a missing file
    ($CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh)
    — the harness errors on every matching call; ai-hats owns this entry —
    run 'ai-hats self update' to reclaim it.
```

Reports only, never deletes: the sweep stays install-time (HATS-905). The same
scan is a hard refusal at the end of every install-time path
(`assert_runtime_hooks_resolve`) — but over the Claude pair only, so agy
residue warns without ever failing a bump.

## Composition diagnostics (HATS-1753)

Composition itself finds problems — a consent point disarmed by a later writer, a
`composition.apps` block no integration collects, a gate declared twice, a row
whose skill an overlay removed. All four used to reach the human by
`print(..., file=sys.stderr)` straight from the composer, which under a wrapped
session is the one channel the alternate screen buffer eats. A warning nobody
sees is not a warning, and three of those four have nothing to do with consent.

They now speak in `Diagnostic` (`src/ai_hats/diagnostics.py`) — a frozen
`(level, text, where, remedy)`, where `where` is the component YAML to open:

```
⚠ 1 startup warning(s):
  • /lib/roles/warn-role/config.yaml: composition.apps.rak is declared by
    'warn-role', but no integration in this build collects 'rak'
    (known: …) — those rows will never fire
    did you mean 'rack'?
```

`remedy` is the last line, indented under the bullet. The banner indents no
continuation of its own, so `Diagnostic.render` does it — the same way
`wrap_runner._broken_hook_refs_text` already does.

Three of the four composition sites carry one: a misspelt app key gets a
computed `difflib` guess (and stays silent when nothing is close, since a wrong
guess costs more than none), a row orphaned by an overlay names both exits, and
a gate declared twice names the two declarations to choose between. The fourth —
a consent point switched off by a later writer — deliberately carries **no**
remedy: last-writer-wins is the declared rule, so the flip is reported but never
dressed as a defect.

Transport is the sink convention already used for hooks warnings, with a typed
payload instead of `list[str]`: `compose_to_run(..., diagnostics=<list>)`
collects (through the private `compose_for_role` funnel behind it), and with no collector the findings go to stderr through
`emit_to_stderr` — one spelling, for the plain-CLI paths that have no banner.
`build_composition_payload` passes the seam's list, so the findings ride
`CompositionPayload.diagnostics` into the hold.

The level travels with the finding rather than being applied at the render
boundary. That is the difference from the neighbouring producers above, which
build `StartupNotice("warn", text)` from bare strings and so cannot express a
note — worth remembering when migrating one of them onto this channel.
