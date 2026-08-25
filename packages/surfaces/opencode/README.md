# ai-hats-opencode

[OpenCode](https://opencode.ai/) surface plugin for the [ai-hats](https://github.com/muratovv/ai-hats) framework.

Registers the `opencode` provider through the `ai_hats.providers` entry-point
group. A session materializes the composed role into the ai-hats session cache
and delivers it via OpenCode's per-session `OPENCODE_CONFIG` config file:

- **Role context** — a primary agent (`--agent ai-hats`) whose prompt carries the
  composition (traits, rules, skill index). The project root is never written.
- **Skills** — mirrored into the session cache with a prompt index of exact
  `SKILL.md` paths and `PATH` injection for skill scripts.
- **Runtime hooks** — a session-local dispatcher plugin registered through the
  config's `plugin` array; it reads a version-1 manifest (shared schema with the
  cline/codex surfaces) and translates OpenCode tool events to the Claude
  dialect, denying tool calls when a guard exits `2` or decides `block`.
- **Permissions** — the same manifest carries the role's permission rules; the
  generated config stays free of permission keys (see below).

## Permissions & consent semantics

Decisions about how a session may act belong to the **role** (its composed
hooks and consent declarations), not to a global-looking generated config — so
`opencode.json` carries no `permission` keys. The dispatcher plugin instead
answers OpenCode's native permission requests through the server API:

| Request class | HITL (supervisor at the TUI) | Automate (`opencode run`) |
| --- | --- | --- |
| Path under the ai-hats session cache (`external_directory`) | allowed by the manifest rule | allowed by the manifest rule |
| Anything else the role has no rule for | native TUI prompt — the supervisor answers | OpenCode's headless auto-reject (verified: no hang) |

Rules live in the manifest's `permissions` array (first match wins; a rule is
`{permission, prefix?, action}` with `action: allow` — anything unmatched
defers). A failed reply fails open to the platform channel, mirroring the
tool-hook contract. Consent-gated operations (`rack`, `wt`, pushes) do not
flow through this layer at all: they are enforced by the PATH consent wrappers
and the safety-guard tool hooks, exactly as on the codex/cline surfaces.

Version note (observed on opencode 1.18.21): the typed `permission.ask`
plugin hook advertised by `@opencode-ai/plugin` types is not wired at runtime;
the bus events `permission.asked`/`permission.replied` plus the
`POST /session/{id}/permissions/{permissionID}` endpoint are the working seam.
The global config file this version loads is `<config-dir>/config.json`; the
session `OPENCODE_CONFIG` document merges over it.

## Install

```bash
uv pip install -e packages/surfaces/opencode   # from an ai-hats checkout
```

or simply run `ai-hats -p opencode -r <role>` — ai-hats self-heals missing
in-tree surfaces automatically.

## Test

```bash
uv run pytest packages/surfaces/opencode/tests -q
```
