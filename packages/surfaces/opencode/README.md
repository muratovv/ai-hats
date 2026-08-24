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
