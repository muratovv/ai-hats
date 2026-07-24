# ai-hats-cline

A **Cline surface plugin** for the [ai-hats](https://github.com/muratovv/ai-hats)
framework. It registers the [`cline`](https://cline.bot) CLI as a first-class
ai-hats **provider** through the `ai_hats.providers` entry point, so ai-hats
discovers it with zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).

Install it alongside `ai-hats` and `cline` appears next to the built-ins:

```console
$ ai-hats list providers
  claude  →  CLAUDE.md
  gemini  →  GEMINI.md
  cline   →  CLINE.md
```

(The `→ CLINE.md` column is nominal — cline takes the role inline via `-s`, so
no `CLINE.md` file is ever written; it is just this provider's system-prompt
path label.)

Then compose any role onto cline:

```console
$ ai-hats -p cline -r <role>          # HITL: launches an interactive cline TUI
```

## What it owns

- **`ClineProvider`** — the `ai_hats.providers.Provider` adapter for `cline`:
  - the composed role reaches cline **inline** via `-s "<role>"` (no static
    `CLINE.md` — `update_system_prompt` is a no-op);
  - HITL launches the interactive TUI (`cline -i`); the automate path runs
    headless (`cline --yolo --json "<prompt>"`);
  - `--worktree` is never passed (ai-hats-wt owns isolation);
  - the role's skills are materialized into the **per-session cache**
    (`<ai_hats_dir>/.cache/sessions/<sid>/skills`) and delivered to cline via
    `--config <cache>` (cline scans `<base>/skills`) — nothing lands in the
    project root (clean-root invariant, HATS-1171). `CLINE_DATA_DIR` is pinned
    to the real cline home so `--config` keeps the machine's auth.
- **`ClineParser` + `resolve_transcript`** — cline's
  `~/.cline/data/sessions/<id>/<id>.messages.json` is discovered by the
  provider and parsed into a real `audit.md` (👤/👾 turn markers) and
  `usage.json` (token metrics), so `reflect` gets a factual layer for cline
  sessions (HATS-960, HATS-1087).

## Requirements

- `cline` v3.x on `PATH`, authenticated (`cline auth`).
- `ai-hats` (this plugin depends on the integrator for the `Provider` ABC, per
  ADR-0014).

## Not yet here

- Runtime bash-tool hooks: cline's TS plugin sandbox needs `jiti` (unbundled),
  so ai-hats ships no plugin — guarding falls to `SurfaceGuard`. A native
  `hooks.json` guard (no jiti) is tracked in HATS-1083.
- cline `teams`/`spawn`, and PyPI publish.
