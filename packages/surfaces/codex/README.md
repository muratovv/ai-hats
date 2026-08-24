# ai-hats-codex

OpenAI Codex CLI surface plugin for
[ai-hats](https://github.com/muratovv/ai-hats). It registers `codex` through
the `ai_hats.providers` entry point and supports the normal interactive launch:

```console
$ ai-hats -p codex
```

## Delivery contract

- The composed role and always-on rules are passed per run through Codex's
  `developer_instructions` configuration override. No `AGENTS.md` or project
  `.codex` file is created or changed.
- Skills are copied to a durable session home at
  `<base CODEX_HOME>/.ai-hats/session-homes/<project-key>/<session-id>/skills`.
  That directory is registered through a session-scoped `CODEX_HOME`, so Codex
  exposes the selected skills through its `$` picker and native `skills/list`
  registry. The compact name, description, and exact `SKILL.md` path index
  remains as a fallback. Each session gets its own real skills tree, so
  parallel roles do not overwrite one another.
- Composed runtime guard manifests remain in ai-hats' external per-session
  cache. Stable per-run Codex hook definitions dispatch `PreToolUse`,
  `PermissionRequest`, and `PostToolUse` to the current session's manifest; the
  command contains no project path or session ID, so concurrent sessions stay
  isolated and a reviewed hook hash remains stable.
- Interactive sessions use `workspace-write` with `on-request` approvals.
  Automated sessions use `codex ... exec --json --ephemeral` with
  `workspace-write` and `never`, because they cannot answer approval prompts.
- Dangerous bypass flags are rejected on the normal ai-hats launch path.
- The provider does not pass `-C`: ai-hats owns the child process cwd, including
  its isolated task worktree. The canonical project path may deliberately point
  at the main checkout for shared tracker operations.

Claude-style runtime hooks can request interactive consent with an `ask`
decision, but Codex `PreToolUse` has no equivalent decision that opens a prompt.
The adapter therefore fails that event closed and reports the hook's recovery
instruction; a session-wide ACK must already be present in the environment that
launches ai-hats. When Codex has independently opened a native
`PermissionRequest`, the same hook's `ask` defers to that existing user prompt.
An agent cannot grant itself either form of consent from inside its tool command.

Codex's existing user authentication, configuration, and resume state remain
shared. When the role has skills, the plugin redirects `CODEX_HOME` to a real
session directory and projects the base Codex home's non-skill, non-SQLite
entries through symlinks; it neither reads nor copies their contents. The base
`sessions/` directory is also projected into the durable home, while
`CODEX_SQLITE_HOME` keeps SQLite-backed state in its configured shared home.
SQLite homes inside the ai-hats cache or managed session-home tree are rejected
before launch because either location can be removed during lifecycle cleanup.
The session's real `skills/` directory contains copied role skills plus
symlinks to non-conflicting base skills, with the role copy winning a same-name
collision.

On graceful exit, ai-hats rewrites only rollout paths that start with that
session home's exact path and point to an existing file under the base
`sessions/` directory. It removes the durable home only after SQLite has no
references to it. If the process or host crashes before cleanup, the durable
home remains, so the stored lexical rollout path still resolves and native
Codex resume continues to work. A later launch may canonicalize and remove that
home after its ai-hats session cache is gone. An invalid manifest, unknown
SQLite schema, or missing rollout target retains the home and emits a warning.
A role with no composed skills keeps the incoming Codex environment unchanged.

Codex requires explicit review of non-managed hook definitions. On the first
hook-enabled launch, inspect the stable ai-hats dispatcher with `/hooks` and
trust it only if it matches the command shown there. ai-hats never passes
`--dangerously-bypass-hook-trust`; until the definition is trusted, Codex may
skip it and reports that state in the TUI.

Trusting that stable dispatcher delegates hook selection to ai-hats and the
role you launched; it is not a separate review of every composed skill version.
Review project/custom library sources before composing them. At runtime the
dispatcher only executes executable files from the current session's copied
skills mirror, rejects path or symlink escapes, validates the session identity,
and fails closed if any of those pins are missing.

## Requirements

- `codex` on `PATH`, authenticated with the normal Codex login flow.
- `ai-hats>=0.15.0`.

Before the TUI handoff the surface runs only `codex --version` and
`codex login status`. Missing CLI and logged-out states produce redacted startup
remediation; ai-hats never runs login or reads credential files itself.
