# gworkspace-cli — setup guide

This guide walks the user through installing and authorizing the **Google Workspace CLI** (`gws`) so that the [`gworkspace-cli`](../../library/usage/skills/gworkspace-cli/SKILL.md) skill can use it from `Bash`.

ai-hats stays secret-agnostic — auth tokens and OAuth state live entirely under `~/.config/gws/` (encrypted at rest with AES-256-GCM) and never enter the repo.

## 1. Install

Pick one:

```bash
# Homebrew (macOS / Linux)
brew install gws

# npm
npm install -g @googleworkspace/cli

# cargo
cargo install gws

# pre-built binary — https://github.com/googleworkspace/cli/releases
```

Verify:

```bash
gws --version
```

If `command not found` — the binary is not on `$PATH`. Re-check the installer output, or use the pre-built binary route.

## 2. Auth

Interactive flow (recommended for a developer machine):

```bash
gws auth setup    # creates a GCP project, enables required APIs
gws auth login    # OAuth consent in the browser
gws auth status   # confirm "Authenticated as <email>"
```

State is written to `~/.config/gws/`:
- credentials — encrypted via OS keyring or local key file
- `client_secret.json` — OAuth client metadata

Headless / CI:

```bash
gws auth export --unmasked > /secure/path/credentials.json
export GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=/secure/path/credentials.json
```

Service-account or pre-obtained token flows are also supported — see [official docs](https://github.com/googleworkspace/cli).

## 3. Permission allowlist (Claude Code)

By default Claude Code prompts for permission on every `Bash` invocation. To auto-approve `gws` calls, add a single pattern to **`.claude/settings.json`** — either the project-local file (`<repo>/.claude/settings.json`) or the global one (`~/.claude/settings.json`):

```json
{
  "permissions": {
    "allow": [
      "Bash(gws:*)"
    ]
  }
}
```

`Bash(gws:*)` matches any subcommand (`gws drive ...`, `gws sheets ...`, etc.).

Without this entry, every `gws ...` call will trigger an interactive prompt. With it, the agent runs `gws` like any other allowed tool.

## 4. Verification smoke-test

Confirm the wiring end-to-end:

```bash
gws drive files list --params '{"pageSize":1}'
```

Expected: a JSON object with a `files` array (possibly empty if the account has no files). Errors and what they mean:

| Error                              | Meaning                                  | Fix                                |
|------------------------------------|------------------------------------------|------------------------------------|
| `command not found: gws`           | binary missing from `$PATH`              | re-run install step                |
| `not authenticated`                | no credentials in `~/.config/gws/`       | re-run `gws auth login`            |
| `403 insufficient permissions`     | scope not granted to the OAuth client    | re-run `gws auth setup`            |
| `quota exceeded`                   | GCP project hit a Workspace API quota    | enable billing or raise the quota  |

## 5. After setup

- The [`gworkspace-cli`](../../library/usage/skills/gworkspace-cli/SKILL.md) skill is now usable in any role that composes it (directly or via the `integration::google` trait).
- Project-specific conventions (sheet schemas, naming, idempotency rules) belong in a **project-local** skill, not in the framework skill — see [`docs/how-to-cli-integrations.md`](../how-to-cli-integrations.md).

## References

- Official CLI: <https://github.com/googleworkspace/cli>
- Framework convention: [`docs/how-to-cli-integrations.md`](../how-to-cli-integrations.md)
