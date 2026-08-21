# ai-hats-cline

A **Cline surface plugin** for the [ai-hats](https://github.com/muratovv/ai-hats)
framework. It registers the [`cline`](https://cline.bot) CLI as a first-class
ai-hats **provider** through the `ai_hats.providers` entry point, so ai-hats
discovers it with zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).

Install it alongside `ai-hats` and `cline` appears next to the built-ins:

```console
$ ai-hats list providers
  agy     →  GEMINI.md
  claude  →  (session cache)
  cline   →  (session cache)
```

(The right-hand column is where the role reaches the surface from. Only agy has
a project-root file; claude and cline receive the composition from the
per-session cache and write nothing to the root.)

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
    (`<cache_root>/sessions/<sid>/skills` — outside the project, default
    `~/.cache/ai-hats/<project-key>/`, HATS-1398) and delivered to cline via
    `--config <cache>` (cline scans `<base>/skills`) — nothing lands in the
    project root (clean-root invariant, HATS-1171). `CLINE_DATA_DIR` is pinned
    to the real cline home so `--config` keeps the machine's auth.
  - composed `PreToolUse` and `PostToolUse` hooks are materialized beside the
    skills and delivered through Cline's native `--hooks-dir` flag. A
    surface-local adapter translates Cline command and file tools into the
    Claude-style payloads consumed by existing ai-hats guards (HATS-1775).
    Explicit `deny` and `ask` decisions cancel a `PreToolUse` call. Cline's
    hook response has no permission-prompt or input-rewrite channel, so an
    `ask` reports how to retry after external consent and never applies the
    hook's `updatedInput` itself. Missing or malformed session hook state is
    reported on stderr and fails open.
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

- cline `teams`/`spawn`, and PyPI publish.
