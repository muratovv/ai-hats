# Wiring external services via CLI skills

External services (Google Workspace, GitHub, BigQuery, etc.) attach to a role as **a regular skill** that documents a CLI tool. ai-hats stays secret-agnostic: auth, tokens, and keys are owned by the CLI and the user.

## Principle

```
external service = CLI tool on $PATH + a skill that documents it
```

The agent invokes the CLI through `Bash`, the same as any other command. No MCP servers, no extra protocols, no in-tree infrastructure inside ai-hats.

## Layout of an integration skill

`<library>/skills/<tool-name>-cli/SKILL.md`:

```markdown
---
name: <tool-name>-cli
description: <one line about the service this skill covers>
triggers:
  - "<when the agent should load this skill>"
skip:
  - "<when the skill can be skipped>"
tags: [cli, integration]
---

# <Tool Name> CLI

## Installation

```bash
brew install <tool>     # or npm/curl/pip
```

## Auth (one-time)

```bash
<tool> auth login       # OAuth / token setup, done by hand
```

Auth state lives in `~/.config/<tool>/` (or equivalent). ai-hats does not manage it.

## Common operations

- `<tool> <command> ...` — what it does + an example.
- ...

## Notes

- Permission allowlist: to auto-approve invocations, add `Bash(<tool>:*)` to `.claude/settings.json`.
- If the CLI is not installed — Bash returns `command not found`; ask the user to install it.
```

## What must NOT be in the skill

- **Secrets** of any kind. No tokens / keys / passwords in `SKILL.md`, `metadata.yaml`, or examples.
- **Hardcoded install paths.** The CLI must resolve via `$PATH`.
- **Wrapper scripts with hardcoded paths.** If auth needs a wrapper (e.g. reading from Keychain) — that's a user-side artifact (lives in `~/.local/bin/`), not part of the skill.

## Wiring into a role

In a trait/role `composition`:

```yaml
composition:
  skills:
    - gworkspace-cli
    - github-cli
```

After `ai-hats bump` the skill becomes visible to the agent through the standard skill-injection mechanism.

## Examples

- [`gworkspace-cli`](../library/usage/skills/gworkspace-cli/SKILL.md) — Google Workspace via the `gws` CLI. **Landed.** Setup walk-through: [`docs/integrations/gworkspace-cli-setup.md`](integrations/gworkspace-cli-setup.md). Bundled into trait `integration::google` (`library/usage/traits/integration/google/config.yaml`). Use that setup guide as the reference layout for future CLI integration skills.
- Additional CLI integrations are added as child tasks under HATS-341.
